"""真正连接 Postgres 执行的端到端核对（样例模型 × 样例库）。

文本断言类测试（test_sqlgen / test_expected_queries）只比对生成的 SQL
字符串，发现不了"模式未限定 / 派生表作用域 / 孤儿参数"这类只有执行才
暴露的问题——本文件把典型拖拽组合真正发给数据库，并按样例数据核对
结果数字。

运行方式（需要一个可连的数据库；compose 环境即 db 服务）：

    DRAGQUERY_TEST_DSN=postgresql://drag:drag@localhost:55432/dragdb \
        python -m pytest tests/test_live_db.py -q

未设置 DRAGQUERY_TEST_DSN 时整组跳过，不影响纯函数测试。
每个用例都新建连接，不 SET 任何会话变量——模拟分析师真实会话。
"""
from __future__ import annotations

import asyncio
import os
import re
from decimal import Decimal

import pytest

asyncpg = pytest.importorskip("asyncpg")

DSN = os.environ.get("DRAGQUERY_TEST_DSN")
pytestmark = pytest.mark.skipif(
    not DSN, reason="未设置 DRAGQUERY_TEST_DSN，跳过连库执行测试")

from app.modeling.loader import load_sample_model
from app.sqlgen import (FilterSpec, GroupEntry, QuerySpec, SqlGenerator,
                        apply_drill)

_SCHEMA_SQL = os.path.join(
    os.path.dirname(__file__), "..", "..", "db", "init", "01_sample_schema.sql")


def run(coro):
    return asyncio.run(coro)


def spec(rows=(), cols=(), measures=(), filters=(), limit=200) -> QuerySpec:
    return QuerySpec(
        rows=[GroupEntry(dimension=d) for d in rows],
        columns=[GroupEntry(dimension=d) for d in cols],
        measures=list(measures),
        filters=list(filters),
        limit=limit,
    )


@pytest.fixture(scope="module", autouse=True)
def sample_db():
    """把随服务附带的样例库灌进测试数据库（幂等：先 DROP 再建）。"""
    with open(_SCHEMA_SQL, encoding="utf-8") as f:
        sql = f.read()

    async def setup():
        conn = await asyncpg.connect(DSN)
        try:
            await conn.execute(sql)
        finally:
            await conn.close()

    run(setup())
    yield


def generate_and_fetch(s: QuerySpec, model=None):
    """生成 SQL 并在全新连接上执行（不依赖任何会话级设置）。"""
    gen = SqlGenerator(model or load_sample_model())
    out = gen.generate(s)

    async def q():
        conn = await asyncpg.connect(DSN)
        try:
            return await conn.fetch(out.sql, *out.params)
        finally:
            await conn.close()

    return out, run(q())


def as_dict(rows, n_keys=1):
    """asyncpg Record 列表 -> {维度键: 度量值}。

    维度键单列取标量、多列取元组；度量同理。Decimal 归一成 float。
    """
    out = {}
    for r in rows:
        vals = [float(v) if isinstance(v, Decimal) else v
                for v in r.values()]
        key = vals[0] if n_keys == 1 else tuple(vals[:n_keys])
        rest = vals[n_keys:]
        out[key] = rest[0] if len(rest) == 1 else tuple(rest)
    return out


def assert_params_closed(out):
    """每个 $n 占位符都有参数、每个参数都被语句引用（无孤儿参数）。"""
    used = {int(n) for n in re.findall(r"\$(\d+)", out.sql)}
    assert used == set(range(1, len(out.params) + 1)), (
        f"占位符 {sorted(used)} 与参数列表 {out.params} 不对应")


# ---------------------------------------------------------------------------
# 一对多组合："一"侧度量对每个维度值只计一次
# ---------------------------------------------------------------------------
def test_region_x_order_total(sample_db):
    _, rows = generate_and_fetch(
        spec(rows=["dim_customer_region"], measures=["m_order_total"]))
    assert as_dict(rows) == {
        "华东": 8047.0, "华北": 14496.0, "华南": 9324.0, "西南": 407.0}


def test_category_x_order_total_dedup(sample_db):
    out, rows = generate_and_fetch(
        spec(rows=["dim_product_category"], measures=["m_order_total"]))
    assert out.fanout_tables == ["orders"]
    # 直接连接求和会得到 外设 24513 / 配件 16989（被明细行放大），
    # 去重后每个订单对每个品类只计一次
    assert as_dict(rows) == {
        "外设": 15618.0, "家具": 4123.0, "电脑": 19724.0, "配件": 12533.0}


def test_category_x_total_and_qty_independent(sample_db):
    """同一次拖拽两个度量：风险度量走去重表，安全度量在骨架里，互不干扰。"""
    _, rows = generate_and_fetch(spec(
        rows=["dim_product_category"], measures=["m_order_total", "m_qty"]))
    assert as_dict(rows) == {
        "外设": (15618.0, 13.0), "家具": (4123.0, 4.0),
        "电脑": (19724.0, 4.0), "配件": (12533.0, 31.0)}


def test_payment_method_x_order_total(sample_db):
    _, rows = generate_and_fetch(
        spec(rows=["dim_payment_method"], measures=["m_order_total"]))
    got = as_dict(rows)
    assert got["alipay"] == 16878.0
    assert got["card"] == 15443.0
    assert got["wechat"] == 4700.0
    # 无支付记录的订单（1003/1004/1007/1012）落入 NULL 分组
    assert got[None] == 8446.0


# ---------------------------------------------------------------------------
# 度量筛选在一对多组合下生效，且参数一一对应
# ---------------------------------------------------------------------------
def test_measure_filter_on_fanout_combo(sample_db):
    out, rows = generate_and_fetch(spec(
        rows=["dim_product_category"],
        measures=["m_order_total"],
        filters=[FilterSpec(kind="measure", target="m_order_total",
                            op="gt", value=15000)]))
    assert as_dict(rows) == {"外设": 15618.0, "电脑": 19724.0}
    # 筛选值只以参数形式出现
    assert "15000" not in out.sql
    assert 15000 in out.params
    assert_params_closed(out)


def test_measure_filter_on_undragged_measure(sample_db):
    """筛选一个没拖进数值区的度量：作为隐藏度量参与计算与过滤。"""
    out, rows = generate_and_fetch(spec(
        rows=["dim_product_category"],
        measures=["m_qty"],
        filters=[FilterSpec(kind="measure", target="m_order_total",
                            op="gt", value=15000)]))
    assert as_dict(rows) == {"外设": 13.0, "电脑": 4.0}
    assert_params_closed(out)


def test_grand_total_without_dimension(sample_db):
    """无维度 + 两个跨表度量（回归：中层查询不能再 GROUP BY 聚合列）。"""
    _, rows = generate_and_fetch(
        spec(measures=["m_order_total", "m_qty"]))
    assert [[float(v) for v in r.values()] for r in rows] == [[32274.0, 52.0]]


# ---------------------------------------------------------------------------
# 表归属以模型登记的模式为准，与会话 search_path 无关
# ---------------------------------------------------------------------------
def test_schema_qualified_names_execute_without_session_setup(sample_db):
    out, rows = generate_and_fetch(
        spec(rows=["dim_order_status"], measures=["m_order_total"]))
    assert '"biz"."orders"' in out.sql
    assert "JOIN" not in out.sql.upper()  # 单表查询不产生连接
    assert sum(float(r[1]) for r in rows) == 32274.0


def test_model_registered_schema_is_authoritative(sample_db):
    """把 products 登记到另一个模式：查询跟着模型走，不需要改会话。"""
    async def prepare():
        conn = await asyncpg.connect(DSN)
        try:
            await conn.execute(
                "CREATE SCHEMA IF NOT EXISTS analytics;"
                "DROP TABLE IF EXISTS analytics.products;"
                "CREATE TABLE analytics.products AS "
                "SELECT * FROM biz.products")
        finally:
            await conn.close()

    async def cleanup():
        conn = await asyncpg.connect(DSN)
        try:
            await conn.execute("DROP SCHEMA IF EXISTS analytics CASCADE")
        finally:
            await conn.close()

    run(prepare())
    try:
        model = load_sample_model().model_copy(deep=True)
        for t in model.tables:
            if t.name == "products":
                t.schema_name = "analytics"
        out, rows = generate_and_fetch(
            spec(rows=["dim_product_category"], measures=["m_qty"]),
            model=model)
        assert '"analytics"."products"' in out.sql
        assert as_dict(rows) == {
            "外设": 13.0, "家具": 4.0, "电脑": 4.0, "配件": 31.0}
    finally:
        run(cleanup())


# ---------------------------------------------------------------------------
# 枚举下拉 / 下钻 / 只读执行链路
# ---------------------------------------------------------------------------
def test_dimension_enum_values(sample_db):
    gen = SqlGenerator(load_sample_model())

    async def q(sql, params):
        conn = await asyncpg.connect(DSN)
        try:
            return await conn.fetch(sql, *params)
        finally:
            await conn.close()

    d = gen.generate_distinct("dim_payment_method", 100)
    assert [r[0] for r in run(q(d.sql, d.params))] == \
        ["alipay", "card", "wechat"]
    d = gen.generate_distinct("dim_customer_region", 100)
    assert [r[0] for r in run(q(d.sql, d.params))] == \
        ["华东", "华北", "华南", "西南"]


def test_drill_appends_finer_level_and_executes(sample_db):
    model = load_sample_model()
    base = spec(rows=["dim_customer_region"], measures=["m_order_total"])
    drilled = apply_drill(model, base, "dim_customer_region")
    assert [g.dimension for g in drilled.rows] == [
        "dim_customer_region", "dim_customer_province"]
    _, rows = generate_and_fetch(drilled, model=model)
    got = as_dict(rows, n_keys=2)
    assert got[("华东", "上海")] == 2024.0
    assert got[("西南", "四川")] == 407.0
    assert sum(got.values()) == 32274.0


def test_execute_generated_full_pipeline(sample_db):
    """走 execute_generated：guard + READ ONLY 事务 + 结果整形。"""
    from app.execution import execute_generated

    async def q():
        pool = await asyncpg.create_pool(DSN, min_size=1, max_size=2)
        try:
            gen = SqlGenerator(load_sample_model())
            out = gen.generate(spec(rows=["dim_product_category"],
                                    measures=["m_order_total"]))
            return await execute_generated(pool, out, 200)
        finally:
            await pool.close()

    res = run(q())
    assert [c["key"] for c in res.columns] == [
        "dim_product_category", "m_order_total"]
    assert {r[0]: r[1] for r in res.rows} == {
        "外设": 15618.0, "家具": 4123.0, "电脑": 19724.0, "配件": 12533.0}
    assert res.truncated is False


def test_model_store_roundtrip(sample_db):
    """模型存储：init_schema 播种样例模型，save/get 往返一致。"""
    from app.modeling.store import ModelStore

    async def q():
        pool = await asyncpg.create_pool(DSN, min_size=1, max_size=2)
        try:
            store = ModelStore(pool)
            await store.init_schema()          # 幂等：已播种则不动
            model = await store.get()
            assert len(model.tables) == 5
            await store.save(model)            # 保存不应报参数错误
            store.invalidate()
            again = await store.get()
            return model, again
        finally:
            await pool.close()

    model, again = run(q())
    assert again.model_dump() == model.model_dump()


def test_all_typical_shapes_have_closed_params(sample_db):
    """典型形态：每个占位参数都被引用，字面量只以参数形式进入。"""
    shapes = [
        spec(rows=["dim_order_status"], measures=["m_order_total"],
             filters=[FilterSpec(kind="dimension", target="dim_order_channel",
                                 op="in", value=["web", "app"])]),
        spec(rows=["dim_product_category"],
             measures=["m_order_total", "m_qty"]),
        spec(rows=["dim_payment_method"],
             measures=["m_qty", "m_payment_amount"]),
        spec(rows=["dim_product_category"], measures=["m_pending_amount"]),
        spec(rows=["dim_customer_segment"], measures=["m_avg_order"],
             filters=[FilterSpec(kind="measure", target="m_avg_order",
                                 op="between", value=[100, 1000])]),
    ]
    for s in shapes:
        out, _ = generate_and_fetch(s)
        assert_params_closed(out)
        for p in out.params:
            if isinstance(p, str):
                # 字面量只以参数形式进入，不以 SQL 字符串字面量出现
                assert f"'{p}'" not in out.sql
