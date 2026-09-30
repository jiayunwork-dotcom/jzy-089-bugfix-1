"""用 sqlglot 的 Postgres 方言解析所有典型查询，确保生成 SQL 语法合法。

这是"没有真实 PG 实例也能守住语法正确性"的一层；
真实容器里的结果正确性由 test_expected_queries 的结构断言 + 手工核对保证。
"""
from __future__ import annotations

import re

import pytest

sqlglot = pytest.importorskip("sqlglot")

from app.modeling.loader import load_sample_model
from app.sqlgen import FilterSpec, QuerySpec, SqlGenerator, apply_drill


def parse(sql: str):
    # sqlglot 不认识 $1 占位符，替换成绑定参数风格 ? 再解析
    normalized = re.sub(r"\$\d+", "?", sql)
    return sqlglot.parse_one(normalized, read="postgres")


@pytest.fixture(scope="module")
def gen():
    return SqlGenerator(load_sample_model())


SPECS = [
    QuerySpec(measures=["m_sales"]),
    QuerySpec(
        rows=[{"dimension": "dim_order_status"}],
        measures=["m_order_total"],
        filters=[FilterSpec(kind="dimension", target="dim_order_channel",
                            op="in", value=["web", "app"])],
    ),
    QuerySpec(
        rows=[{"dimension": "dim_customer_region"}],
        measures=["m_qty", "m_payment_amount"]),
    QuerySpec(
        rows=[{"dimension": "dim_product_category"}],
        measures=["m_order_total", "m_qty"]),
    QuerySpec(
        rows=[{"dimension": "dim_payment_method"}],
        measures=["m_qty", "m_payment_amount"]),
    QuerySpec(
        rows=[{"dimension": "dim_product_category"}],
        measures=["m_pending_amount"]),
    QuerySpec(
        rows=[{"dimension": "dim_customer_segment"}],
        measures=["m_avg_order"],
        filters=[FilterSpec(kind="measure", target="m_avg_order",
                            op="between", value=[100, 1000])]),
    QuerySpec(
        rows=[{"dimension": "dim_order_status"},
              {"dimension": "dim_customer_region"}],
        measures=["m_order_count"]),
    QuerySpec(
        rows=[{"dimension": "dim_order_date"}],
        measures=["m_order_total"],
        filters=[FilterSpec(kind="dimension", target="dim_order_date",
                            op="between", value=["2024-01-01", "2024-06-30"])]),
]


@pytest.mark.parametrize("spec", SPECS)
def test_generated_sql_parses_as_postgres(gen, spec):
    out = gen.generate(spec)
    ast = parse(out.sql)
    # 顶层必须是 SELECT
    assert ast.key == "select"


def test_drilled_query_parses(gen):
    model = load_sample_model()
    base = QuerySpec(rows=[{"dimension": "dim_order_year"}],
                     measures=["m_order_total"])
    drilled = apply_drill(model, base, "dim_order_year")
    parse(gen.generate(drilled).sql)


def test_distinct_enum_query_parses(gen):
    parse(gen.generate_distinct("dim_order_month", 100).sql)
    parse(gen.generate_distinct("dim_order_status", 100).sql)
