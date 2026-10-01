"""SQL 翻译器内核（产品的"可验证内核"）。

输入 QuerySpec（拖拽意图）+ ModelSpec（语义模型），
输出参数化 SELECT（GeneratedQuery）。核心规则：

1. 单表查询不产生 JOIN；
2. 跨表查询用 BFS 生成树覆盖全部被引用表，同一张表只连接一次；
3. 一对多关联会放大"一"侧（或兄弟多侧分支）的行数，命中的度量改走
   "按主键去重的派生表"聚合，避免 SUM 被 JOIN 重复计数；
4. 字段投放顺序决定 SELECT / GROUP BY 顺序，同样输入逐字节稳定；
5. 下钻只追加更细层级，过滤与度量不变；
6. 所有字面量只以 $n 参数占位出现，SQL 文本里永不内联用户输入；
7. 表归属哪个模式（schema）以模型登记为准：所有表名一律发全限定名
   （"模式"."表"），不依赖会话 search_path。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from ..expressions import FieldRef, quote_ident
from ..expressions.sandbox import compile_expression
from ..modeling.catalog import build_scope, collect_tables
from ..modeling.schema import ModelSpec
from .graph import JoinEdge, JoinGraph
from .spec import FilterSpec, QuerySpec

_AGG_SQL = {
    "sum": "SUM",
    "count": "COUNT",
    "count_distinct": "COUNT(DISTINCT",
    "avg": "AVG",
    "min": "MIN",
    "max": "MAX",
}


class QueryBuildError(ValueError):
    """拖拽意图在当前模型上无法翻译成查询（对应 HTTP 400）。"""


@dataclass
class ResultColumn:
    key: str
    label: str
    data_type: str


@dataclass
class GeneratedQuery:
    sql: str
    params: List[object]
    columns: List[ResultColumn] = field(default_factory=list)
    joined_tables: List[str] = field(default_factory=list)
    fanout_tables: List[str] = field(default_factory=list)


@dataclass
class _Dim:
    id: str
    name: str
    data_type: str
    sql_fragment: str
    tables: List[str]


@dataclass
class _Measure:
    id: str
    name: str
    table: str
    agg: str
    base_sql: Optional[str]          # count(*) 时为 None
    filter_sql: Optional[str]
    data_type: str
    tables: List[str]


@dataclass
class _Ctx:
    params: List[object]
    edges: List[JoinEdge]
    join_order: List[str]
    where: str


class SqlGenerator:
    def __init__(self, model: ModelSpec):
        self.model = model
        self.graph = JoinGraph(model)

    # ================= 公共入口 =================
    def generate(self, spec: QuerySpec) -> GeneratedQuery:
        if not spec.rows and not spec.columns and not spec.measures:
            raise QueryBuildError("请至少把一个维度或度量拖入查询区域")
        params: List[object] = []

        dims = [self._compile_dimension(d, params)
                for d in spec.group_dimensions()]
        measures = [self._compile_measure(mid, params)
                    for mid in spec.measures]
        # 只出现在度量筛选里、没拖进数值区的度量：照样编译，用于过滤
        filter_only: List[_Measure] = []
        present = {m.id for m in measures}
        for f in spec.measure_filters():
            if f.target not in present:
                filter_only.append(self._compile_measure(f.target, params))
                present.add(f.target)

        wanted: Set[str] = set()
        for d in dims:
            wanted.update(d.tables)
        for m in measures + filter_only:
            wanted.update(m.tables)
        for f in spec.filters:
            wanted.update(self._filter_tables(f))

        root = (measures + filter_only)[0].table if (measures or filter_only) \
            else dims[0].tables[0]
        wanted.add(root)
        edges = self.graph.spanning_tree(root, wanted) if len(wanted) > 1 else []
        join_order = [root] + [e.child for e in edges]
        risky = self._fanout_measure_tables(measures + filter_only, edges)

        where = self._build_where(spec.dimension_filters(), params)
        ctx = _Ctx(params, edges, join_order, where)
        # 聚合过滤条件在所有聚合值算好之后才生成（引用聚合列别名）
        measure_preds = self._measure_filter_predicates(
            spec, measures + filter_only, params)

        if risky:
            sql, cols = self._render_with_dedup(
                spec, dims, measures, filter_only, edges, join_order,
                risky, measure_preds, ctx)
        else:
            sql, cols = self._render_flat(
                spec, dims, measures, filter_only, measure_preds, ctx)
        return GeneratedQuery(
            sql=sql, params=params, columns=cols,
            joined_tables=join_order, fanout_tables=sorted(risky))

    # ================= 枚举值查询（筛选下拉框） =================
    def generate_distinct(self, dim_id: str, limit: int) -> "GeneratedQuery":
        params: List[object] = []
        dim = self._compile_dimension(dim_id, params)
        root_t = self.model.table(dim.tables[0])
        lines = [
            f'SELECT DISTINCT {dim.sql_fragment} AS "value"',
            f'FROM {root_t.qname()}',
        ]
        # 表达式维度可能依赖多张表
        wanted = set(dim.tables)
        if len(wanted) > 1:
            for e in self.graph.spanning_tree(dim.tables[0], wanted):
                lines.append(
                    f"LEFT JOIN {self._qname(e.child)} ON {e.on_clause(self._qname)}")
        lines.append(f'WHERE {dim.sql_fragment} IS NOT NULL')
        lines.append('ORDER BY "value"')
        params.append(min(max(int(limit), 1), 1000))
        lines.append(f"LIMIT ${len(params)}")
        return GeneratedQuery(
            sql="\n".join(lines), params=params,
            joined_tables=dim.tables,
        )

    # ================= 表名（模式以模型登记为准） =================
    def _qname(self, table_name: str) -> str:
        return self.model.table(table_name).qname()

    # ================= 元素编译 =================
    def _scope(self, owning_table: str) -> Dict[str, FieldRef]:
        return build_scope(self.model, owning_table)

    def _compile_dimension(self, dim_id: str,
                           params: List[object]) -> _Dim:
        # 分组项可以是维度，也可以是计算字段（前端把两者都投放到 行/列）
        try:
            d = self.model.dimension(dim_id)
        except KeyError:
            try:
                c = self.model.calculated(dim_id)
            except KeyError:
                raise QueryBuildError(f"未知维度：{dim_id}")
            scope = self._scope(c.table)
            compiled = compile_expression(c.expression, scope, params)
            return _Dim(c.id, c.name, c.data_type, compiled.sql,
                        _dedupe(collect_tables(scope, compiled.referenced_keys)))
        if d.expression:
            scope = self._scope(d.table)
            compiled = compile_expression(d.expression, scope, params)
            fragment = compiled.sql
            tables = collect_tables(scope, compiled.referenced_keys)
        else:
            fragment = f'{self._qname(d.table)}."{d.column}"'
            tables = [d.table]
        return _Dim(d.id, d.name, d.data_type, fragment, _dedupe(tables))

    def _compile_measure(self, measure_id: str,
                         params: List[object]) -> _Measure:
        try:
            m = self.model.measure(measure_id)
        except KeyError:
            raise QueryBuildError(f"未知度量：{measure_id}")
        tables = [m.table]
        if m.agg == "count" and not m.column and not m.expression:
            base_sql: Optional[str] = None
        elif m.expression:
            scope = self._scope(m.table)
            refs = compile_expression(m.expression, scope, [])
            compiled = compile_expression(m.expression, scope, params)
            base_sql = compiled.sql
            tables += collect_tables(scope, refs.referenced_keys)
        else:
            base_sql = f'{self._qname(m.table)}."{m.column}"'

        filter_sql = None
        if m.filter:
            scope = self._scope(m.table)
            refs = compile_expression(m.filter, scope, [])
            filter_sql = compile_expression(m.filter, scope, params).sql
            tables += collect_tables(scope, refs.referenced_keys)
        return _Measure(m.id, m.name, m.table, m.agg, base_sql, filter_sql,
                        "number", _dedupe(tables))

    def _filter_tables(self, f: FilterSpec) -> List[str]:
        if f.kind == "measure":
            m = self.model.measure(f.target)
            tables = [m.table]
            if m.expression:
                scope = self._scope(m.table)
                refs = compile_expression(m.expression, scope, [])
                tables += collect_tables(scope, refs.referenced_keys)
            if m.filter:
                scope = self._scope(m.table)
                refs = compile_expression(m.filter, scope, [])
                tables += collect_tables(scope, refs.referenced_keys)
            return _dedupe(tables)
        if f.kind == "dimension":
            try:
                return [self.model.dimension(f.target).table]
            except KeyError:
                return [self.model.calculated(f.target).table]
        calc = self.model.calculated(f.target)
        scope = self._scope(calc.table)
        refs = compile_expression(calc.expression, scope, [])
        return _dedupe([calc.table] + collect_tables(scope, refs.referenced_keys))

    # ================= WHERE / 度量过滤 =================
    def _dimension_sql(self, dim_id: str, params: List[object]) -> str:
        try:
            d = self.model.dimension(dim_id)
        except KeyError:
            c = self.model.calculated(dim_id)
            return compile_expression(
                c.expression, self._scope(c.table), params).sql
        if d.expression:
            return compile_expression(d.expression, self._scope(d.table), params).sql
        return f'{self._qname(d.table)}."{d.column}"'

    def _build_where(self, filters: List[FilterSpec],
                     params: List[object]) -> str:
        parts = []
        for f in filters:
            if f.kind == "dimension":
                try:
                    target = self._dimension_sql(f.target, params)
                except KeyError:
                    # 前端把计算字段也以 dimension 载体投放到筛选区
                    calc = self.model.calculated(f.target)
                    target = compile_expression(
                        calc.expression, self._scope(calc.table), params).sql
            else:
                calc = self.model.calculated(f.target)
                target = compile_expression(
                    calc.expression, self._scope(calc.table), params).sql
            parts.append(operator_sql(target, f, params))
        return " AND ".join(parts)

    def _measure_filter_predicates(self, spec: QuerySpec,
                                   measures: List[_Measure],
                                   params: List[object]) -> List[str]:
        """聚合完成后再施加的度量过滤，左值直接引用聚合列的输出别名。

        这样无论聚合来自扁平 GROUP BY 还是去重派生表，条件都落在
        "已经算好的聚合值"上，且每个绑定参数必然出现在最终语句里。
        """
        by_id = {m.id: m for m in measures}
        preds: List[str] = []
        for f in spec.measure_filters():
            m = by_id.get(f.target)
            if m is None:
                m = self._compile_measure(f.target, params)
            preds.append(operator_sql(alias(m.id), f, params))
        return preds

    def _agg_expr(self, m: _Measure) -> str:
        if m.agg == "count" and not m.base_sql:
            core = "COUNT(*)"
        elif m.agg == "count_distinct":
            core = f"COUNT(DISTINCT {m.base_sql})"
        else:
            core = f"{_AGG_SQL[m.agg]}({m.base_sql})"
        if m.filter_sql:
            core += f" FILTER (WHERE {m.filter_sql})"
        return core

    # ================= 放大检测 =================
    def _fanout_measure_tables(self, measures: List[_Measure],
                               edges: List[JoinEdge]) -> Set[str]:
        """度量表 T 的行在最终连接结果里被复制 <=> 存在生成树边 (P->C)：

        - T 不在 C 的子树里（该边是 T 路径旁的分叉）且每个 P 行对多个 C 行；
        - T 在 C 的子树里（含 T==C）且每个 C 行对多个 P 行
          （即 N:1 边被按"多侧为父"接入，整个一侧子树被多侧父表复制）。
        """
        children: Dict[str, List[JoinEdge]] = {}
        for e in edges:
            children.setdefault(e.parent, []).append(e)

        def subtree(table: str) -> Set[str]:
            out = {table}
            for e in children.get(table, []):
                out |= subtree(e.child)
            return out

        risky: Set[str] = set()
        for m in measures:
            for e in edges:
                if m.table in subtree(e.child):
                    duplicated = e.parent_per_child_is_many()
                else:
                    duplicated = e.child_per_parent_is_many()
                if duplicated:
                    risky.add(m.table)
                    break
        return risky

    # ================= 渲染：普通形态 =================
    def _render_flat(self, spec: QuerySpec, dims: List[_Dim],
                     measures: List[_Measure], filter_only: List[_Measure],
                     measure_preds: List[str], ctx: _Ctx
                     ) -> Tuple[str, List[ResultColumn]]:
        show = [self._agg_expr(m) + f" AS {alias(m.id)}" for m in measures]
        hidden = [self._agg_expr(m) + f" AS {alias(m.id)}"
                  for m in filter_only]
        # 维度起成稳定输出别名：度量过滤的外层包装按别名引用分组列
        items = [f"{d.sql_fragment} AS {alias(d.id)}" for d in dims]
        items += show + hidden
        lines = ["SELECT " + ", ".join(items),
                 f"FROM {self._qname(ctx.join_order[0])}"]
        for e in ctx.edges:
            lines.append(
                f"LEFT JOIN {self._qname(e.child)} ON {e.on_clause(self._qname)}")
        if ctx.where:
            lines.append(f"WHERE {ctx.where}")
        if dims:
            lines.append("GROUP BY " + ", ".join(
                str(i + 1) for i in range(len(dims))))

        # 无度量过滤：排序 + LIMIT 直接落在聚合结果上
        if not measure_preds:
            if dims:
                lines.append("ORDER BY " + ", ".join(
                    str(i + 1) for i in range(len(dims))))
            ctx.params.append(min(max(int(spec.limit), 1), 10_000))
            lines.append(f"LIMIT ${len(ctx.params)}")
            return "\n".join(lines), self._columns(dims, measures)

        # 有度量过滤：先包一层做聚合后过滤（此时内层不能先 LIMIT，否则会在
        # 过滤之前把行裁掉），排序 + LIMIT 只放在最外层
        outer = [f'SELECT {self._outer_select_list(dims, measures)}',
                 "FROM (",
                 indent("\n".join(lines), "  "),
                 ") __agg",
                 "WHERE " + " AND ".join(measure_preds)]
        if dims:
            outer.append("ORDER BY " + ", ".join(
                str(i + 1) for i in range(len(dims))))
        ctx.params.append(min(max(int(spec.limit), 1), 10_000))
        outer.append(f"LIMIT ${len(ctx.params)}")
        return "\n".join(outer), self._columns(dims, measures)

    # ================= 渲染：去重派生表形态 =================
    def _render_with_dedup(self, spec: QuerySpec, dims: List[_Dim],
                           measures: List[_Measure], filter_only: List[_Measure],
                           edges: List[JoinEdge], join_order: List[str],
                           risky: Set[str], measure_preds: List[str],
                           ctx: _Ctx
                           ) -> Tuple[str, List[ResultColumn]]:
        safe = [m for m in measures if m.table not in risky]
        safe_hidden = [m for m in filter_only if m.table not in risky]
        risky_list = [m for m in measures if m.table in risky]
        risky_hidden = [m for m in filter_only if m.table in risky]
        dim_sql = [d.sql_fragment for d in dims]

        # 基础子查询：维度 + 安全度量（安全度量在 JOIN 后的行集上聚合不放大）
        # 维度起成稳定输出别名，供外层 b."dim_.." 与去重表回连使用
        base_items = [f"{frag} AS {alias(d.id)}"
                      for frag, d in zip(dim_sql, dims)]
        base_items += [f"{self._agg_expr(m)} AS {alias(m.id)}"
                       for m in safe + safe_hidden]
        if not base_items:
            base_items = ['1 AS "__one"']
        base = ["SELECT " + ", ".join(base_items),
                f"FROM {self._qname(join_order[0])}"]
        for e in edges:
            base.append(
                f"LEFT JOIN {self._qname(e.child)} ON {e.on_clause(self._qname)}")
        if ctx.where:
            base.append(f"WHERE {ctx.where}")
        # 有维度才 GROUP BY；无维度时（仅度量）是全局单行聚合
        if dims:
            base.append("GROUP BY " + ", ".join(
                str(i + 1) for i in range(len(dims))))

        # 每个风险度量表一个去重派生表：
        # 内层 SELECT DISTINCT 维度表达式 + 主键 + 度量基列(+过滤标志)，
        # 先消掉 JOIN 复制；中层只按"派生表输出列"（维度别名）分组聚合。
        # 注意：中层 FROM 的是 __dedup 子查询，物理表名已离开作用域，
        # 因此维度必须用别名引用，不能再写 "products"."category"。
        by_table: Dict[str, List[_Measure]] = {}
        for m in risky_list + risky_hidden:
            by_table.setdefault(m.table, []).append(m)

        outer_measures: Dict[str, str] = {}
        extra_joins: List[str] = []
        for idx, (table, ms) in enumerate(sorted(by_table.items()), start=1):
            dt = f"r{idx}"
            pk = self.model.table(table).primary_key
            # 维度在 DISTINCT 内层就要起好输出别名：中层 FROM 的是 __dedup
            # 派生表，只能按它暴露的列名引用维度，物理表名已离开作用域。
            inner_items = [
                f"{frag} AS {alias(d.id)}" for frag, d in zip(dim_sql, dims)
            ]
            inner_items.append(f'{self._qname(table)}."{pk}" AS "__pk"')
            mid_items: List[str] = []   # 中层 SELECT：维度别名 + 聚合别名
            for d in dims:
                mid_items.append(alias(d.id))
            for m in ms:
                if m.agg == "count" and not m.base_sql:
                    raw = "1"
                elif m.base_sql is not None:
                    raw = m.base_sql
                else:
                    raw = f'{self._qname(table)}."{pk}"'
                inner_items.append(f"{raw} AS {value_alias(m.id)}")
                # 度量级过滤：把条件作为布尔标志带进 DISTINCT 内层，
                # 中层聚合只能引用派生表列，因此 FILTER 必须落在标志列上
                flag = None
                if m.filter_sql:
                    flag = flag_alias(m.id)
                    inner_items.append(f"({m.filter_sql}) AS {flag}")
                va = value_alias(m.id)
                if m.agg == "sum":
                    agg = f"SUM({va})"
                elif m.agg == "count_distinct":
                    agg = f"COUNT(DISTINCT {va})"
                elif m.agg == "avg":
                    agg = f"AVG({va})"
                else:  # count / min / max
                    agg = f"{_AGG_SQL[m.agg]}({va})"
                if flag:
                    agg += f" FILTER (WHERE {flag})"
                mid_items.append(f"{agg} AS {alias(m.id)}")
                outer_measures[m.id] = f"{dt}.{alias(m.id)}"

            inner_sql = "\n".join([
                "SELECT DISTINCT " + ", ".join(inner_items),
                f"FROM {self._qname(join_order[0])}",
                *[f"LEFT JOIN {self._qname(e.child)} ON {e.on_clause(self._qname)}"
                  for e in edges],
            ] + ([f"WHERE {ctx.where}"] if ctx.where else []))
            # 无维度时中层是全局聚合（一行），不能写 GROUP BY：
            # 此时第 1 列是聚合表达式，GROUP BY 1 会被 Postgres 拒绝。
            sub_lines = [
                "SELECT " + ", ".join(mid_items),
                "FROM (",
                indent(inner_sql, "  "),
                ") __dedup",
            ]
            if dims:
                sub_lines.append(
                    "GROUP BY " + ", ".join(
                        str(i + 1) for i in range(len(dims))))
            sub = "\n".join(sub_lines)
            if dims:
                on = " AND ".join(
                    f"{dt}.{alias(d.id)} IS NOT DISTINCT FROM b.{alias(d.id)}"
                    for d in dims)
            else:
                on = "TRUE"
            extra_joins.append(
                f"LEFT JOIN (\n{indent(sub, '  ')}\n) {dt} ON {on}")

        # 外层 SELECT：维度 + 度量（严格按投放顺序取列）
        safe_ids = {m.id: f"b.{alias(m.id)}" for m in safe + safe_hidden}
        select_items = [f"b.{alias(d.id)}" for d in dims]
        for m in measures:
            select_items.append(
                safe_ids.get(m.id) or outer_measures[m.id])
        # 仅用于过滤、没拖进数值区的度量也必须在核心层投影出来，外层
        # __agg 的 WHERE 才能引用；最外层 SELECT 只回投可见列。
        if measure_preds:
            for m in safe_hidden:
                select_items.append(f"b.{alias(m.id)}")
            for m in risky_hidden:
                select_items.append(outer_measures[m.id])

        ctx.params.append(min(max(int(spec.limit), 1), 10_000))
        limit_sql = f"LIMIT ${len(ctx.params)}"
        order_cols = ("ORDER BY " + ", ".join(
            f"b.{alias(d.id)}" for d in dims)) if dims else None

        core_lines = ["SELECT " + ", ".join(select_items),
                      "FROM (",
                      indent("\n".join(base), "  "),
                      ") b"]
        core_lines += extra_joins

        if not measure_preds:
            if order_cols:
                core_lines.append(order_cols)
            core_lines.append(limit_sql)
            return "\n".join(core_lines), self._columns(dims, measures)

        # 度量过滤：再包一层，对最终聚合列别名施加。核心层既不 ORDER 也不
        # LIMIT —— 必须先在完整分组集合上过滤，再由外层排序截断；否则像
        # "订单总额 > 20000 只剩电脑" 这类用例会被内层 LIMIT 提前裁掉。
        outer = [f"SELECT {self._outer_select_list(dims, measures)}",
                 "FROM (",
                 indent("\n".join(core_lines), "  "),
                 ") __agg",
                 "WHERE " + " AND ".join(measure_preds)]
        if dims:
            outer.append("ORDER BY " + ", ".join(
                str(i + 1) for i in range(len(dims))))
        outer.append(limit_sql)
        return "\n".join(outer), self._columns(dims, measures)

    @staticmethod
    def _outer_select_list(dims: List[_Dim],
                           measures: List[_Measure]) -> str:
        return ", ".join(
            [alias(d.id) for d in dims] + [alias(m.id) for m in measures])

    def _columns(self, dims: List[_Dim],
                 measures: List[_Measure]) -> List[ResultColumn]:
        return [ResultColumn(d.id, d.name, d.data_type) for d in dims] + [
            ResultColumn(m.id, m.name, m.data_type) for m in measures]


# ----------------------------------------------------------------------
# 过滤操作符 -> 参数化 SQL
# ----------------------------------------------------------------------
def operator_sql(target_sql: str, f: FilterSpec,
                 params: List[object]) -> str:
    op, value = f.op, f.value
    if op == "is_null":
        return f"({target_sql} IS NULL)"
    if op == "is_not_null":
        return f"({target_sql} IS NOT NULL)"
    if op == "between":
        lo, hi = value
        params.extend([lo, hi])
        n = len(params)
        return f"({target_sql} BETWEEN ${n - 1} AND ${n})"
    if op in ("in", "not_in"):
        values = list(value or [])
        if not values:
            return "(FALSE)" if op == "in" else "(TRUE)"
        start = len(params) + 1
        params.extend(values)
        ph = ", ".join(f"${i}" for i in range(start, start + len(values)))
        kw = "IN" if op == "in" else "NOT IN"
        return f"({target_sql} {kw} ({ph}))"
    params.append(value)
    n = len(params)
    if op == "contains":
        return f"({target_sql} LIKE '%' || ${n} || '%')"
    sym = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=",
           "lt": "<", "lte": "<="}[op]
    return f"({target_sql} {sym} ${n})"


def alias(id_: str) -> str:
    safe = "".join(ch for ch in id_ if ch.isalnum() or ch == "_")
    return quote_ident(safe)


def value_alias(id_: str) -> str:
    safe = "".join(ch for ch in id_ if ch.isalnum() or ch == "_")
    return quote_ident(f"__v_{safe}")


def flag_alias(id_: str) -> str:
    safe = "".join(ch for ch in id_ if ch.isalnum() or ch == "_")
    return quote_ident(f"__f_{safe}")


def indent(text: str, prefix: str) -> str:
    return "\n".join(prefix + line for line in text.split("\n"))


def _dedupe(items: List[str]) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out
