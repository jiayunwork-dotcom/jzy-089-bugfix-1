"""几组"拖拽意图 -> 期望 SQL"的固定核对用例（随产品附带）。

运行：
    cd backend && python -m pytest tests/test_expected_queries.py -q

每组用例固定了模型对象 id（见 app/modeling/sample_model.json），
断言翻译结果中必须出现的关键结构，而不是整段字符串，
使用例对空白/别名风格的小幅调整保持稳健。
"""
from __future__ import annotations

import re

from app.sqlgen import FilterSpec, GroupEntry, QuerySpec, SqlGenerator


def gen():
    from app.modeling.loader import load_sample_model
    return SqlGenerator(load_sample_model())


def spec(rows=(), cols=(), measures=(), filters=(), limit=200):
    return QuerySpec(
        rows=[GroupEntry(dimension=d) for d in rows],
        columns=[GroupEntry(dimension=d) for d in cols],
        measures=list(measures),
        filters=list(filters),
        limit=limit,
    )


# --- 用例 A：单表，状态 x 订单总额，渠道 in 筛选 -------------------------
def test_case_a_single_table():
    out = gen().generate(spec(
        rows=["dim_order_status"],
        measures=["m_order_total"],
        filters=[FilterSpec(kind="dimension", target="dim_order_channel",
                            op="in", value=["web", "app"])],
    ))
    sql = out.sql
    assert re.search(r'FROM "orders"', sql)
    assert "JOIN" not in sql.upper()
    assert re.search(
        r'\("orders"\."channel" IN \(\$1, \$2\)\)', sql)
    assert re.search(
        r'SUM\("orders"\."total_amount"\) AS "m_order_total"', sql)
    assert "GROUP BY 1" in sql
    assert out.params == ["web", "app", 200]


# --- 用例 B：跨 3 表 N:1 安全路径，大区 x 销量 --------------------------
def test_case_b_many_to_one_path():
    out = gen().generate(spec(
        rows=["dim_customer_region"],
        measures=["m_qty"],   # order_items N->orders N->customers，无放大
    ))
    assert out.joined_tables == ["order_items", "orders", "customers"]
    assert out.fanout_tables == []
    assert sql_count(out.sql, r'LEFT JOIN "(\w+)"') == 2


# --- 用例 C：1:N 放大，品类 x 订单总额，必须去重 ------------------------
def test_case_c_fanout_orders_total_by_category():
    out = gen().generate(spec(
        rows=["dim_product_category"],
        measures=["m_order_total"],
    ))
    assert out.fanout_tables == ["orders"]
    # 去重内层：DISTINCT 品类 + 订单主键 + 金额
    assert re.search(r'SELECT DISTINCT "products"\."category", '
                     r'"orders"\."id" AS "__pk", '
                     r'"orders"\."total_amount" AS "__v_m_order_total"',
                     out.sql)
    # 外层对去重结果求和
    assert re.search(r'SUM\("__v_m_order_total"\) AS "m_order_total"', out.sql)
    # 所有表仅在逻辑生成树中出现一次（子查询复用除外：orders 出现 2 次属预期）
    assert out.joined_tables == ["orders", "order_items", "products"]


# --- 用例 D：双多侧分支，支付方式 x 销量/支付额，各走各的去重表 --------
def test_case_d_two_many_branches():
    out = gen().generate(spec(
        rows=["dim_payment_method"],
        measures=["m_qty", "m_payment_amount"],
    ))
    assert set(out.fanout_tables) == {"order_items", "payments"}
    assert out.sql.count("SELECT DISTINCT") == 2
    assert re.search(r'r1\."m_qty"', out.sql)
    assert re.search(r'r2\."m_payment_amount"', out.sql)


# --- 用例 E：时间层级下钻（年 -> 年+季），度量/过滤不变 ------------------
def test_case_e_date_drill():
    from app.sqlgen import apply_drill
    from app.modeling.loader import load_sample_model
    model = load_sample_model()
    base = spec(rows=["dim_order_year"],
                measures=["m_order_count"],
                filters=[FilterSpec(kind="dimension",
                                    target="dim_order_status",
                                    op="eq", value="paid")])
    drilled = apply_drill(model, base, "dim_order_year")
    assert [g.dimension for g in drilled.rows] == [
        "dim_order_year", "dim_order_quarter"]
    assert drilled.measures == base.measures
    assert drilled.filters == base.filters

    g = SqlGenerator(model)
    before, after = g.generate(base), g.generate(drilled)
    # WHERE 条件文本一致（参数序号随分组层级顺延，过滤值仍是 'paid'）
    assert '("orders"."status" = $2)' in before.sql
    assert '("orders"."status" = $3)' in after.sql
    assert before.params[-2:] == after.params[-2:] == ["paid", 200]


# --- 用例 F：度量级过滤 + 去重（品类 x 待支付订单额） -------------------
def test_case_f_measure_filter_with_dedup():
    out = gen().generate(spec(
        rows=["dim_product_category"],
        measures=["m_pending_amount"],
    ))
    # 过滤条件被提为布尔标志列
    assert re.search(
        r'\(\("orders"\."status" = \$1\)\) AS "__f_m_pending_amount"',
        out.sql) or '"__f_m_pending_amount"' in out.sql
    assert 'FILTER (WHERE "__f_m_pending_amount")' in out.sql
    assert out.params[0] == "pending"


# --- 用例 G：数值区间筛选参数化 -----------------------------------------
def test_case_g_numeric_between():
    out = gen().generate(spec(
        rows=["dim_customer_segment"],
        measures=["m_avg_order"],
        filters=[FilterSpec(kind="measure", target="m_avg_order",
                            op="between", value=[100, 1000])],
    ))
    assert "HAVING" in out.sql
    assert re.search(r"BETWEEN \$\d+ AND \$\d+", out.sql)
    assert 100 in out.params and 1000 in out.params


def sql_count(sql: str, pattern: str) -> int:
    return len(re.findall(pattern, sql))
