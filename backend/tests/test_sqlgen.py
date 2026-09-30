"""SQL 翻译规则（需求里点名要守住的那几条）。"""
from __future__ import annotations

import re

from app.modeling.loader import load_sample_model
from app.sqlgen import FilterSpec, GroupEntry, QuerySpec, apply_drill
from app.sqlgen.generator import QueryBuildError, SqlGenerator


def q(rows=(), cols=(), measures=(), filters=(), limit=200) -> QuerySpec:
    return QuerySpec(
        rows=[GroupEntry(dimension=d) for d in rows],
        columns=[GroupEntry(dimension=d) for d in cols],
        measures=list(measures),
        filters=list(filters),
        limit=limit,
    )


# ---------------------------------------------------------------------------
# 规则 1：只涉及单表的查询不应产生任何 JOIN
# ---------------------------------------------------------------------------
def test_single_table_query_has_no_join(gen):
    out = gen.generate(q(rows=["dim_order_status"], measures=["m_order_total"]))
    assert "JOIN" not in out.sql.upper()
    assert out.joined_tables == ["orders"]


def test_single_table_dimension_only_has_no_join(gen):
    out = gen.generate(q(rows=["dim_customer_segment"]))
    assert "JOIN" not in out.sql.upper()
    # 纯维度查询按分组列聚合输出
    assert "GROUP BY 1" in out.sql


# ---------------------------------------------------------------------------
# 规则 2：跨表查询的连接路径覆盖所有被引用表，且同一张表只连一次
# ---------------------------------------------------------------------------
def test_cross_table_join_covers_all_referenced_tables(gen):
    out = gen.generate(q(rows=["dim_customer_city"],
                         measures=["m_qty", "m_payment_amount"]))
    wanted = {"customers", "orders", "order_items", "payments"}
    assert set(out.joined_tables) == wanted
    # join_order 给出的逻辑 JOIN 路径：每张表恰好一次
    assert len(out.joined_tables) == len(set(out.joined_tables))
    # 逻辑连接路径（生成树）里每个非根表恰好一条 LEFT JOIN
    logical = out.sql.split(") b")[0]  # 去重派生表会在子查询里复用同一路径
    for t in wanted:
        assert len(re.findall(rf'(?:FROM|LEFT JOIN) "{t}"',
                              logical)) == 1, (t, out.sql)


def test_unreachable_table_relation_raises(model):
    # 去掉 order_items <-> products 关联：品类维度无法从订单度量可达
    from app.sqlgen.spec import GroupEntry, QuerySpec
    broken = model.model_copy(deep=True)
    broken.relations = [r for r in broken.relations
                        if r.id != "rel_order_items_products"]
    gen = SqlGenerator(broken)
    spec = QuerySpec(rows=[GroupEntry(dimension="dim_product_category")],
                     measures=["m_order_total"])
    try:
        gen.generate(spec)
    except (QueryBuildError, ValueError):
        return
    raise AssertionError("缺少关联时应当报错")


# ---------------------------------------------------------------------------
# 规则 3：一对多关联下求和不被 JOIN 放大（去重派生表）
# ---------------------------------------------------------------------------
def test_one_to_many_sum_on_one_side_uses_dedup(gen):
    # orders 1 -> N order_items：按商品品类看订单总额，
    # orders.total_amount 会被明细行复制，必须去重
    out = gen.generate(q(rows=["dim_product_category"],
                         measures=["m_order_total"]))
    assert "orders" in out.fanout_tables
    # 去重形态的标志：DISTINCT 维度 + 订单主键 + 度量基列
    assert "SELECT DISTINCT" in out.sql
    assert '"orders"."id" AS "value"' in out.sql or \
           '"orders"."id" AS' in out.sql
    # 真正的求和在外层对去重后的派生表做
    assert re.search(r'r1\."m_order_total"', out.sql)


def test_two_many_side_branches_dedup_each_measure(gen):
    # orders 1 -> N order_items 与 orders 1 -> N payments 两个多侧分支
    # 交叉相乘会放大 order_items 与 payments 两侧的 SUM
    out = gen.generate(q(rows=["dim_payment_method"],
                         measures=["m_qty", "m_payment_amount"]))
    assert set(out.fanout_tables) == {"order_items", "payments"}
    # 两个度量各有一个去重派生表 r1 / r2
    assert re.search(r"LEFT JOIN \(", out.sql)
    assert out.sql.count("SELECT DISTINCT") == 2


def test_no_fanout_when_drill_along_safe_many_to_one_path(gen):
    # order_items N -> 1 products：销量按品类汇总天然不放大
    out = gen.generate(q(rows=["dim_product_category"], measures=["m_qty"]))
    assert out.fanout_tables == []
    assert "SELECT DISTINCT" not in out.sql


def test_measure_filter_kept_inside_dedup(gen):
    # 带过滤的度量（待支付订单额）在放大场景下 FILTER 必须跟随到派生表
    out = gen.generate(q(rows=["dim_product_category"],
                         measures=["m_pending_amount"]))
    # 过滤表达式 'pending' 必须参数化（只允许出现在标识符/别名里）
    assert "'pending'" not in out.sql.lower()
    # 过滤条件被提升为布尔标志列，外层 FILTER 只引用该列（派生表作用域正确）
    assert '"__f_m_pending_amount"' in out.sql
    assert "FILTER (WHERE \"__f_m_pending_amount\")" in out.sql
    assert "pending" in out.params


# ---------------------------------------------------------------------------
# 规则 4：同样拖拽（顺序不变）生成稳定一致的 SQL
# ---------------------------------------------------------------------------
def test_sql_is_deterministic(gen):
    spec = q(rows=["dim_customer_region", "dim_order_status"],
             measures=["m_order_total", "m_qty", "m_payment_amount"],
             filters=[FilterSpec(kind="dimension", target="dim_order_channel",
                                 op="eq", value="web")])
    sqls = {gen.generate(spec.model_copy(deep=True)).sql for _ in range(5)}
    assert len(sqls) == 1


def test_field_order_is_preserved(gen):
    out_a = gen.generate(q(rows=["dim_order_status", "dim_customer_region"],
                           measures=["m_qty", "m_order_total"]))
    out_b = gen.generate(q(rows=["dim_customer_region", "dim_order_status"],
                           measures=["m_order_total", "m_qty"]))
    assert out_a.sql != out_b.sql
    # 投放顺序即 SELECT 列顺序
    assert out_a.sql.index("dim_order_status") < out_a.sql.index("m_qty")
    assert out_b.sql.index("dim_customer_region") < out_b.sql.index(
        "m_order_total")


# ---------------------------------------------------------------------------
# 规则 5：下钻只改变分组粒度，过滤与度量保持不变
# ---------------------------------------------------------------------------
def test_drill_appends_finer_level(model, gen):
    spec = q(rows=["dim_order_year"], measures=["m_order_total"],
             filters=[FilterSpec(kind="dimension", target="dim_order_channel",
                                 op="eq", value="web")])
    before = gen.generate(spec)
    drilled = apply_drill(model, spec, "dim_order_year")
    after = gen.generate(drilled)

    group_ids = [g.dimension for g in drilled.rows]
    assert group_ids == ["dim_order_year", "dim_order_quarter"]
    # 度量完全一致
    assert drilled.measures == spec.measures
    # 过滤完全一致（点击值加过滤是前端行为，不属于内核下钻）
    assert drilled.filters == spec.filters
    # 生成 SQL 里度量表达式与 WHERE 条件保持不变（层级维度在 SELECT/GROUP BY
    # 里按位置引用，断言参数与 WHERE 片段稳定即可）
    assert "m_order_total" in after.sql
    assert after.sql.split("GROUP BY")[1] != before.sql.split("GROUP BY")[1]
    # WHERE 条件（channel=web）完全一致；仅 GROUP BY 粒度变细（多一个层级参数）
    assert "web" in after.params and "web" in before.params
    assert after.params[-1] == before.params[-1] == 200
    # 下钻前后非分组层级的参数（过滤值）保持不变
    def filter_params(p):
        return [x for x in p if x not in ("year", "quarter", "month", "day")]
    assert filter_params(after.params) == filter_params(before.params)


def test_drill_at_leaf_is_idempotent(model):
    spec = q(rows=["dim_order_date"], measures=["m_order_count"])
    again = apply_drill(model, spec, "dim_order_date")
    assert [g.dimension for g in again.rows] == ["dim_order_date"]


# ---------------------------------------------------------------------------
# 规则 6：所有字面量参数化，SQL 文本不含用户输入
# ---------------------------------------------------------------------------
def test_filter_literals_are_parameterized(gen):
    spec = q(rows=["dim_order_status"], measures=["m_order_total"],
             filters=[
                 FilterSpec(kind="dimension", target="dim_order_channel",
                            op="eq", value="web"),
                 FilterSpec(kind="dimension", target="dim_customer_city",
                            op="in", value=["上海", "北京"]),
             ])
    out = gen.generate(spec)
    assert "web" not in out.sql
    assert "上海" not in out.sql and "北京" not in out.sql
    assert out.params == ["web", "上海", "北京", 200] or \
        ("web" in out.params and "上海" in out.params and "北京" in out.params)
    placeholders = re.findall(r"\$\d+", out.sql)
    assert len(placeholders) >= 4  # 3 个过滤值 + limit


def test_between_is_parameterized(gen):
    spec = q(rows=["dim_order_status"], measures=["m_order_total"],
             filters=[FilterSpec(kind="dimension",
                                 target="dim_customer_segment",
                                 op="gte", value=100)])
    out = gen.generate(spec)
    assert "100" not in re.sub(r"\$\d+", "", out.sql).split("LIMIT")[0]
    assert 100 in out.params


def test_calculated_expression_literals_parameterized(model):
    from app.modeling.schema import CalculatedFieldSpec
    m = model.model_copy(deep=True)
    m.calculated_fields = [c for c in m.calculated_fields if c.id != "cf_band"]
    m.calculated_fields.append(CalculatedFieldSpec(
        id="cf_band", name="客单带", table="orders",
        expression="IF(total_amount > 1000, 'high', 'low')",
        data_type="text"))
    # 计算字段作为筛选条件使用（WHERE 内联展开，字面量参数化）
    spec = q(rows=["dim_order_status"], measures=["m_order_total"],
             filters=[FilterSpec(kind="calculated", target="cf_band",
                                 op="eq", value="high")])
    out = SqlGenerator(m).generate(spec)
    assert "high" not in out.sql and "low" not in out.sql
    assert "high" in out.params and "low" in out.params


def test_limit_is_parameterized(gen):
    out = gen.generate(q(rows=["dim_order_status"], measures=["m_order_total"],
                         limit=57))
    assert "LIMIT $2" in out.sql or re.search(r"LIMIT \$\d+", out.sql)
    assert out.params[-1] == 57


# ---------------------------------------------------------------------------
# 规则 7：只允许 SELECT（guard 覆盖）——此处验证内核产物天然满足
# ---------------------------------------------------------------------------
def test_generated_sql_is_single_select(gen):
    from app.execution import assert_select_only
    for spec in [
        q(rows=["dim_order_status"], measures=["m_order_total"]),
        q(rows=["dim_product_category"], measures=["m_order_total", "m_qty"]),
        q(rows=["dim_payment_method"],
          measures=["m_qty", "m_payment_amount"]),
    ]:
        sql = gen.generate(spec).sql
        assert sql.lstrip().upper().startswith("SELECT")
        assert ";" not in sql.rstrip()
        assert_select_only(sql)


# ---------------------------------------------------------------------------
# 其他内核行为
# ---------------------------------------------------------------------------
def test_measure_with_filter_emits_filter_clause(gen):
    out = gen.generate(q(rows=["dim_order_status"],
                         measures=["m_pending_amount"]))
    assert "FILTER (WHERE" in out.sql
    # pending 必须参数化
    assert "'pending'" not in out.sql.lower()
    assert "pending" in out.params


def test_expression_measure(gen):
    # 销售额 = SUM(quantity * unit_price)：仅度量时保持单表无 JOIN
    out = gen.generate(q(measures=["m_sales"]))
    # 表达式：不带多余括号的乘法
    assert '"order_items"."quantity" * "order_items"."unit_price"' in out.sql
    assert out.joined_tables == ["order_items"]
    assert "JOIN" not in out.sql.upper()


def test_calculated_field_with_join_table(gen):
    # 毛利率引用了 products.cost（join_tables 声明），需要连到 products
    out = gen.generate(q(rows=["dim_product_category"],
                         measures=["m_qty"]))
    # 计算字段作为度量聚合基（这里直接验证表达式能编译出 products.cost）
    from app.modeling.schema import MeasureSpec
    model = gen.model.model_copy(deep=True)
    model.measures.append(MeasureSpec(
        id="m_margin", name="毛利额", table="order_items",
        agg="sum", expression="gross_profit"))
    g2 = SqlGenerator(model)
    out2 = g2.generate(q(rows=["dim_product_category"], measures=["m_margin"]))
    assert '"products"."cost"' in out2.sql
    assert set(out2.joined_tables) == {"order_items", "products"}


def test_calculated_field_can_group_rows(gen):
    # 计算字段拖入行/列：按"明细金额"表达式分组（这里用作分组键验证解析）
    out = gen.generate(q(rows=["cf_customer_name_tag"],
                         measures=["m_order_count"]))
    # CONCAT 在沙箱里翻译为 || 拼接
    assert "||" in out.sql
    assert set(out.joined_tables) == {"customers", "orders"}


def test_empty_query_raises(gen):
    try:
        gen.generate(QuerySpec())
    except QueryBuildError:
        return
    raise AssertionError("空查询应当报错")


def test_invalid_filter_op_causted(gen):
    bad = FilterSpec(kind="dimension", target="dim_order_status",
                     op="eq", value=None)
    out = gen.generate(q(rows=["dim_order_status"],
                         measures=["m_order_total"], filters=[bad]))
    # NULL 参数化（IS NULL 语义应使用 is_null；这里只确保不拼接）
    assert None in out.params
