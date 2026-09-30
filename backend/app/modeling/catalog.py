"""把模型里的物理列 / 计算字段暴露给表达式沙箱作为"可引用对象"。

裸标识符解析规则（保持简单、无注入面）：
- 若标识符是 owning_table 的物理列，解析到该列；
- 否则若在可见表集合内全局唯一，解析到唯一列；
- 否则若是某个计算字段的 id（或去掉 cf_ 前缀的短名），内联展开；
- 否则报错。不允许表名.列名、不允许下标。

计算字段按依赖顺序预编译（带环检测），编译产物在后续查询里复用；
字面量参数在每次生成 SQL 时按当前参数序列重新编号（见 sandbox 内联逻辑）。
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Set

from ..expressions import FieldRef, collect_names, compile_expression
from ..expressions.sandbox import ExpressionError
from .schema import ModelSpec


def column_index(model: ModelSpec) -> Dict[str, List[FieldRef]]:
    """列名（小写） -> 同名物理列引用列表（跨表可能有多个）。"""
    by_column: Dict[str, List[FieldRef]] = {}
    for t in model.tables:
        for c in t.columns:
            by_column.setdefault(c.name.lower(), []).append(
                FieldRef(key=c.name.lower(), table=t.name,
                         column=c.name, kind="column"))
    return by_column


def build_scope(model: ModelSpec,
                owning_table: str,
                visible_tables: Iterable[str] | None = None
                ) -> Dict[str, FieldRef]:
    """构造沙箱作用域（含已预编译、已回填的计算字段引用）。

    visible_tables 为 None 时，全局唯一回退只使用 owning_table，
    避免 order_id 这类跨表同名列把无关表误拖进 JOIN。
    """
    by_column = column_index(model)
    visible = set(visible_tables) if visible_tables is not None else {
        owning_table
    }
    visible.add(owning_table)

    scope: Dict[str, FieldRef] = {}
    # 物理列：优先本表，其次可见表中全局唯一
    for col_name, refs in by_column.items():
        own = [r for r in refs if r.table == owning_table]
        visible_refs = [r for r in refs if r.table in visible]
        if own:
            scope[col_name] = own[0]
        elif len(visible_refs) == 1:
            scope[col_name] = visible_refs[0]
        # 歧义或不可见时不注册 -> 引用即报错（如裸 id 不会被静默猜错表）

    calcs = {c.id.lower(): c for c in model.calculated_fields}
    for cid in calcs:
        ref = FieldRef(key=cid, table=calcs[cid].table, column=None, kind="calc")
        scope[cid] = ref
        short = cid[3:] if cid.startswith("cf_") else cid
        if short != cid:
            scope.setdefault(short, ref)

    compiled: Set[str] = set()

    def resolve(cid: str, stack: List[str]) -> None:
        if cid in compiled:
            return
        if cid in stack:
            raise ExpressionError(
                f"计算字段存在循环引用：{' -> '.join(stack + [cid])}", 1, 0)
        calc = calcs[cid]
        local_scope = _local_scope(by_column, calcs, scope, calc.table,
                                   getattr(calc, "join_tables", []))
        # 先用不内联的方式拿到引用清单，再递归展开依赖的同表计算字段
        refs = collect_names(calc.expression, local_scope)
        for key in refs:
            dep_ref = local_scope.get(key)
            if (dep_ref is not None and dep_ref.kind == "calc"
                    and dep_ref.table == calc.table
                    and dep_ref.key != cid):
                resolve(dep_ref.key, stack + [cid])
        # 依赖已回填，正式编译
        params: List[object] = []
        result = compile_expression(calc.expression, local_scope, params)
        target = scope[cid]
        target.calc_sql = result.sql
        target.calc_params = tuple(params)
        target.calc_refs = tuple(result.referenced_keys)
        target.calc_join_tables = tuple(getattr(calc, "join_tables", []))
        compiled.add(cid)

    for cid in calcs:
        resolve(cid, [])

    return scope


def _local_scope(by_column, calcs, global_scope, table: str,
                 extra_tables=()) -> Dict[str, FieldRef]:
    """单个计算字段编译时的作用域：
    本表物理列 + 声明的 join_tables 物理列（在允许集合内全局唯一）
    + 同表计算字段（已编译则带产物）。"""
    allowed = {table, *extra_tables}
    local: Dict[str, FieldRef] = {}
    for col_name, refs in by_column.items():
        in_scope = [r for r in refs if r.table in allowed]
        own = [r for r in in_scope if r.table == table]
        if own:
            local[col_name] = own[0]
        elif len(in_scope) == 1:
            local[col_name] = in_scope[0]
    for cid, calc in calcs.items():
        if calc.table == table:
            r = global_scope[cid]
            local[cid] = r
            short = cid[3:] if cid.startswith("cf_") else cid
            if short != cid:
                local.setdefault(short, r)
    return local


def collect_tables(scope: Dict[str, FieldRef],
                   referenced_keys: Iterable[str]) -> List[str]:
    """表达式编译后，根据引用清单推导依赖的物理表（穿透计算字段）。"""
    tables: List[str] = []

    def walk(key: str, seen_calcs: Set[str]) -> None:
        ref = scope.get(key)
        if ref is None:
            return
        if ref.kind == "column":
            tables.append(ref.table)
        elif key not in seen_calcs:
            seen_calcs.add(key)
            tables.append(ref.table)
            tables.extend(ref.calc_join_tables)
            for inner in ref.calc_refs:
                walk(inner, seen_calcs)

    seen: Set[str] = set()
    for key in referenced_keys:
        walk(key, seen)
    return _dedupe(tables)


def _dedupe(items: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def validate_calc_expression(model: ModelSpec, owning_table: str,
                             source: str,
                             join_tables: Iterable[str] | None = None
                             ) -> dict:
    """保存计算字段前的校验入口：使用与真实生成时一致的作用域。

    返回 {valid, referenced?, error?}，error 含行列位置。
    """
    from ..expressions import validate as _validate

    by_column = column_index(model)
    scope: Dict[str, FieldRef] = {}
    allowed = {owning_table, *(join_tables or [])}
    for col_name, refs in by_column.items():
        in_scope = [r for r in refs if r.table in allowed]
        own = [r for r in in_scope if r.table == owning_table]
        if own:
            scope[col_name] = own[0]
        elif len(in_scope) == 1:
            scope[col_name] = in_scope[0]
    calcs = {c.id.lower(): c for c in model.calculated_fields
             if c.table == owning_table}
    for cid, calc in calcs.items():
        ref = FieldRef(key=cid, table=calc.table, kind="calc")
        scope[cid] = ref
        short = cid[3:] if cid.startswith("cf_") else cid
        if short != cid:
            scope.setdefault(short, ref)

    # 预编译同表已有计算字段，保证引用它们也能通过校验
    compiled: Set[str] = set()

    def precompile(cid: str, stack: List[str]) -> None:
        if cid in compiled:
            return
        if cid in stack:
            raise ExpressionError(
                f"计算字段存在循环引用：{' -> '.join(stack + [cid])}", 1, 0)
        # 该 calc 自己的作用域要带上它声明的 join_tables
        dep_calc = calcs[cid]
        dep_scope = _local_scope(by_column, calcs, scope, dep_calc.table,
                                 getattr(dep_calc, "join_tables", []))
        refs = collect_names(dep_calc.expression, dep_scope)
        for key in refs:
            if key in calcs and key != cid:
                precompile(key, stack + [cid])
        params: List[object] = []
        result = compile_expression(dep_calc.expression, dep_scope, params)
        scope[cid].calc_sql = result.sql
        scope[cid].calc_params = tuple(params)
        scope[cid].calc_refs = tuple(result.referenced_keys)
        compiled.add(cid)

    for cid in calcs:
        precompile(cid, [])

    result = _validate(source, scope)
    if result.get("valid"):
        tables = collect_tables(scope, result["referenced"])
        result["tables"] = _dedupe(tables)
    return result
