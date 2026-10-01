"""真实连 Postgres 的端到端核对：把翻译出的 SQL 真的交给样例库执行，
按需求里点名的数字逐项核对，而不只是比对 SQL 文本。

运行方式：
    # 方式一：docker compose 起库（默认映射到 localhost:55432）
    docker compose up -d db
    cd backend && python -m pytest tests/test_execution_db.py

    # 方式二：指定任意 Postgres
    TEST_DATABASE_URL=postgresql://u:p@host:5432/db python -m pytest tests/test_execution_db.py

没有可用数据库时整文件 skip（其余纯函数测试不受影响）。

这里刻意使用默认 search_path（不含 biz），以验证"表归属以模型登记为准、
SQL 全限定名定位"——任何靠会话设置才能跑通的退化都会在这里失败。
"""
from __future__ import annotations

import re

import pytest

from app.modeling.loader import load_sample_model
from app.sqlgen import (FilterSpec, GroupEntry, QuerySpec, SqlGenerator,
                        apply_drill)


def spec(rows=(), cols=(), measures=(), filters=(), limit=200):
    return QuerySpec(
        rows=[GroupEntry(dimension=d) for d in rows],
        columns=[GroupEntry(dimension=d) for d in cols],
        measures=list(measures),
        filters=list(filters),
        limit=limit,
    )


def run(sample_db, gen, sp):
    """生成并执行，断言每个绑定参数都被语句使用，返回 (行字典列表, GeneratedQuery)。"""
    gq = gen.generate(sp)
    used = sorted({int(n) for n in re.findall(r"\$(\d+)", gq.sql)})
    assert used == list(range(1, len(gq.params) + 1)), (
        f"占位参数与参数列表不一致：SQL 用 {used}，参数 {gq.params}\n{gq.sql}")
    assert ";" not in gq.sql.rstrip()
    records = sample_db.fetch(gq.sql, *gq.params)
    keys = [c.key for c in gq.columns]
    rows = [dict(zip(keys, rec.values())) for rec in records]
    return rows, gq


def by_dim(rows, dim, measure):
    return {r[dim]: (None if r[measure] is None else float(r[measure]))
            for r in rows}


# ----------------------------------------------------------------------
# 模式定位：默认 search_path（无 biz）下，所有拖放组合都必须能执行
# ----------------------------------------------------------------------
def test_search_path_has_no_biz(sample_db):
    # 前置条件：会话里确实解析不到裸表名
    assert sample_db.fetchval("SHOW search_path") == '"$user", public'
    with pytest.raises(Exception):
        sample_db.fetch("SELECT count(*) FROM orders")


def test_single_table_runs_without_schema_in_search_path(sample_db):
    gen = SqlGenerator(load_sample_model())
    rows, gq = run(sample_db, gen, spec(
        rows=["dim_order_status"], measures=["m_order_total"]))
    assert "JOIN" not in gq.sql.upper()
    assert '"biz"."orders"' in gq.sql
    assert {r["dim_order_status"] for r in rows} == {
        "paid", "pending", "cancelled"}


# ----------------------------------------------------------------------
# 需求数字一：客户大区 × 订单总额（N:1 安全路径，直接聚合）
# ----------------------------------------------------------------------
def test_region_order_total(sample_db):
    gen = SqlGenerator(load_sample_model())
    rows, _ = run(sample_db, gen, spec(
        rows=["dim_customer_region"], measures=["m_order_total"]))
    got = by_dim(rows, "dim_customer_region", "m_order_total")
    assert got == pytest.approx({
        "华东": 8047, "华北": 14496, "华南": 9324, "西南": 407})


# ----------------------------------------------------------------------
# 需求数字二：商品品类 × 订单总额 —— 跨过 1:N，订单对每品类只计一次
# ----------------------------------------------------------------------
def test_category_order_total_counts_order_once(sample_db):
    gen = SqlGenerator(load_sample_model())
    rows, gq = run(sample_db, gen, spec(
        rows=["dim_product_category"], measures=["m_order_total"]))
    got = by_dim(rows, "dim_product_category", "m_order_total")
    assert got == pytest.approx({
        "外设": 15618, "家具": 4123, "电脑": 19724, "配件": 12533})
    # 直接 JOIN 求和会得到被放大的错误值（外设 24513 / 配件 16989）
    assert got["外设"] != 24513 and got["配件"] != 16989
    assert "orders" in gq.fanout_tables


def test_category_order_total_and_qty_do_not_interfere(sample_db):
    """同一次拖放：去重的订单总额与多侧原生的销量各自正确，互不相乘。"""
    gen = SqlGenerator(load_sample_model())
    rows, _ = run(sample_db, gen, spec(
        rows=["dim_product_category"],
        measures=["m_order_total", "m_qty"]))
    totals = by_dim(rows, "dim_product_category", "m_order_total")
    qtys = {r["dim_product_category"]: r["m_qty"] for r in rows}
    assert totals == pytest.approx({
        "外设": 15618, "家具": 4123, "电脑": 19724, "配件": 12533})
    assert qtys == {"外设": 13, "家具": 4, "电脑": 4, "配件": 31}


# ----------------------------------------------------------------------
# 需求数字三：支付方式 × 订单总额（orders 1:N payments，分期订单分摊）
# ----------------------------------------------------------------------
def test_payment_method_order_total(sample_db):
    gen = SqlGenerator(load_sample_model())
    rows, _ = run(sample_db, gen, spec(
        rows=["dim_payment_method"], measures=["m_order_total"]))
    got = by_dim(rows, "dim_payment_method", "m_order_total")
    assert got == pytest.approx({"alipay": 16878, "card": 15443,
                                 "wechat": 4700, None: 8446})


def test_payment_method_with_three_measures(sample_db):
    """支付方式上同时挂三个度量：订单总额(去重) + 销量/支付额(多侧各表)。"""
    gen = SqlGenerator(load_sample_model())
    rows, _ = run(sample_db, gen, spec(
        rows=["dim_payment_method"],
        measures=["m_order_total", "m_qty", "m_payment_amount"]))
    totals = by_dim(rows, "dim_payment_method", "m_order_total")
    pays = by_dim(rows, "dim_payment_method", "m_payment_amount")
    assert totals == pytest.approx(
        {"alipay": 16878, "card": 15443, "wechat": 4700, None: 8446})
    # 支付额在 payments 粒度原生聚合，不被任何分支放大
    # （未支付订单分组无支付行，LEFT JOIN 下 SUM 为 NULL）
    assert pays == pytest.approx(
        {"alipay": 10280, "card": 9547, "wechat": 4001, None: None})


# ----------------------------------------------------------------------
# 需求数字四：度量筛选在一对多组合下生效，且每个参数都被引用
# ----------------------------------------------------------------------
def test_category_order_total_measure_filter_gt_15000(sample_db):
    gen = SqlGenerator(load_sample_model())
    sp = spec(rows=["dim_product_category"],
              measures=["m_order_total"],
              filters=[FilterSpec(kind="measure", target="m_order_total",
                                  op="gt", value=15000)])
    rows, gq = run(sample_db, gen, sp)
    got = {r["dim_product_category"]: float(r["m_order_total"]) for r in rows}
    assert set(got) == {"外设", "电脑"}
    assert got == pytest.approx({"外设": 15618, "电脑": 19724})
    # 过滤值是绑定参数且确实进入语句（不再是"多一个没人引用的值"）
    assert 15000 in gq.params
    assert re.search(r'"m_order_total" > \$1', gq.sql)
    assert "$1" in gq.sql
    # 阈值收紧到 19000 只剩电脑（19724），外设 15618 被滤掉
    sp2 = spec(rows=["dim_product_category"], measures=["m_order_total"],
               filters=[FilterSpec(kind="measure", target="m_order_total",
                                   op="gt", value=19000)])
    rows2, _ = run(sample_db, gen, sp2)
    assert [r["dim_product_category"] for r in rows2] == ["电脑"]


def test_flat_shape_measure_filter_still_works(sample_db):
    # 非放大场景的度量过滤同样走聚合后过滤，结果正确、参数被使用
    gen = SqlGenerator(load_sample_model())
    sp = spec(rows=["dim_customer_segment"], measures=["m_avg_order"],
              filters=[FilterSpec(kind="measure", target="m_avg_order",
                                  op="between", value=[1000, 2000])])
    rows, gq = run(sample_db, gen, sp)
    got = {r["dim_customer_segment"]: float(r["m_avg_order"]) for r in rows}
    # SMB 均值约 1111 落区间，KA 约 3062 被过滤
    assert set(got) == {"SMB"}
    for v in got.values():
        assert 1000 <= v <= 2000


# ----------------------------------------------------------------------
# 枚举下拉：所有维度（含表达式、跨表、多模式表）都能取到非空枚举
# ----------------------------------------------------------------------
@pytest.mark.parametrize("dim_id,expected_nonempty", [
    ("dim_customer_region", {"华东", "华北", "华南", "西南"}),
    ("dim_product_category", {"外设", "家具", "电脑", "配件"}),
    ("dim_payment_method", {"alipay", "wechat", "card"}),
    ("dim_order_status", {"paid", "pending", "cancelled"}),
    ("dim_order_channel", {"web", "app", "store"}),
    ("dim_order_month", None),     # 表达式维度：DATE_TRUNC 可执行即可
])
def test_dimension_enum_dropdown(sample_db, dim_id, expected_nonempty):
    gen = SqlGenerator(load_sample_model())
    gq = gen.generate_distinct(dim_id, 500)
    records = sample_db.fetch(gq.sql, *gq.params)
    values = [r[0] for r in records]
    assert values, f"{dim_id} 枚举不应为空"
    if expected_nonempty is not None:
        assert set(values) == expected_nonempty


# ----------------------------------------------------------------------
# 下钻：只追加更细一级，结果可执行，且父子汇总一致
# ----------------------------------------------------------------------
def test_drill_geo_region_to_province(sample_db, model):
    gen = SqlGenerator(model)
    base = spec(rows=["dim_customer_region"], measures=["m_order_total"])
    drilled = apply_drill(model, base, "dim_customer_region")
    assert [g.dimension for g in drilled.rows] == [
        "dim_customer_region", "dim_customer_province"]
    rows, _ = run(sample_db, gen, drilled)
    # 下钻后每个大区的省份合计 == 大区值
    base_rows, _ = run(sample_db, gen, base)
    region_total = by_dim(base_rows, "dim_customer_region", "m_order_total")
    agg: dict[str, float] = {}
    for r in rows:
        agg[r["dim_customer_region"]] = agg.get(
            r["dim_customer_region"], 0) + float(r["m_order_total"])
    assert agg == pytest.approx(region_total)


def test_drill_date_hierarchy_runs(sample_db, model):
    gen = SqlGenerator(model)
    base = spec(rows=["dim_order_year"], measures=["m_order_count"])
    drilled = apply_drill(model, base, "dim_order_year")
    rows, _ = run(sample_db, gen, drilled)
    assert rows  # 年+季可执行且非空
    # 再钻一级到月
    drilled2 = apply_drill(model, drilled, "dim_order_quarter")
    rows2, _ = run(sample_db, gen, drilled2)
    assert rows2


def test_drill_with_dimension_filter_and_fanout(sample_db, model):
    # 品类层级下钻 + 一对多去重 + 父值过滤同时存在
    gen = SqlGenerator(model)
    base = spec(rows=["dim_product_category"], measures=["m_order_total"],
                filters=[FilterSpec(kind="dimension",
                                    target="dim_product_category",
                                    op="eq", value="外设")])
    drilled = apply_drill(model, base, "dim_product_category")
    rows, _ = run(sample_db, gen, drilled)
    assert rows
    for r in rows:
        assert r["dim_product_category"] == "外设"


# ----------------------------------------------------------------------
# 既有护栏在真实库上仍然成立
# ----------------------------------------------------------------------
def test_single_table_has_no_join_and_runs(sample_db):
    gen = SqlGenerator(load_sample_model())
    rows, gq = run(sample_db, gen, spec(measures=["m_sales"]))
    assert "JOIN" not in gq.sql.upper()
    assert rows and rows[0]["m_sales"] > 0


def test_same_table_joined_at_most_once(sample_db):
    gen = SqlGenerator(load_sample_model())
    # 五张表全拖上：逻辑路径里每张表恰好一次
    sp = spec(rows=["dim_customer_city"],
              measures=["m_order_total", "m_qty", "m_payment_amount"])
    _, gq = run(sample_db, gen, sp)
    base = gq.sql[gq.sql.index("FROM ("):gq.sql.index(") b")]
    for t in ("customers", "orders", "order_items", "products", "payments"):
        assert len(re.findall(rf'(?:FROM|LEFT JOIN) "biz"\."{t}"',
                              base)) <= 1


def test_read_only_blocks_write_through_generated_path(sample_db):
    # 生成的是 SELECT；在 READ ONLY 事务里尝试写必然失败（护栏纵深）
    sample_db.execute("BEGIN")
    sample_db.execute("SET TRANSACTION READ ONLY")
    with pytest.raises(Exception):
        sample_db.execute("DELETE FROM biz.orders")
    sample_db.execute("ROLLBACK")
    # 数据仍在
    assert sample_db.fetchval("SELECT count(*) FROM biz.orders") == 15


def test_literal_never_inlined(sample_db):
    gen = SqlGenerator(load_sample_model())
    sp = spec(rows=["dim_order_status"], measures=["m_order_total"],
              filters=[FilterSpec(kind="dimension", target="dim_order_channel",
                                  op="eq", value="web")])
    _, gq = run(sample_db, gen, sp)
    assert "web" not in gq.sql
    assert "'web'" not in gq.sql


# ----------------------------------------------------------------------
# 无维度（仅度量）的一对多全局合计：不写 GROUP BY，数字与单侧一致
# ----------------------------------------------------------------------
def test_grand_total_one_to_many_order_total(sample_db):
    gen = SqlGenerator(load_sample_model())
    rows, _ = run(sample_db, gen, spec(
        measures=["m_order_total", "m_qty"]))
    assert float(rows[0]["m_order_total"]) == 32274.0
    assert rows[0]["m_qty"] == 52


def test_grand_total_two_many_branches(sample_db):
    gen = SqlGenerator(load_sample_model())
    rows, _ = run(sample_db, gen, spec(
        measures=["m_qty", "m_payment_amount"]))
    assert rows[0]["m_qty"] == 52
    assert float(rows[0]["m_payment_amount"]) == 23828.0


def test_grand_total_with_measure_filter_no_dim(sample_db):
    # 无维度 + 度量过滤：外层过滤生效、参数被使用、不产生非法 GROUP BY
    gen = SqlGenerator(load_sample_model())
    sp = spec(measures=["m_order_total"],
              filters=[FilterSpec(kind="measure", target="m_order_total",
                                  op="gt", value=999999)])
    rows, gq = run(sample_db, gen, sp)
    assert rows == []  # 全局总额 32274 < 999999，被过滤掉
    assert "GROUP BY" not in gq.sql.upper()
    assert 999999 in gq.params
