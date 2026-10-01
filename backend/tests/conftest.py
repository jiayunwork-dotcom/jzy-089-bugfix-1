import asyncio
import os
import sys

# 让 pytest 不依赖安装即可 import app.*
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from app.modeling.loader import load_sample_model
from app.sqlgen import (FilterSpec, GroupEntry, QuerySpec, SqlGenerator,
                        apply_drill)


@pytest.fixture(scope="session")
def model():
    return load_sample_model()


@pytest.fixture()
def gen(model):
    return SqlGenerator(model)


def q(rows=(), cols=(), measures=(), filters=(), limit=200) -> QuerySpec:
    return QuerySpec(
        rows=[GroupEntry(dimension=d) for d in rows],
        columns=[GroupEntry(dimension=d) for d in cols],
        measures=list(measures),
        filters=list(filters),
        limit=limit,
    )


# ======================================================================
# 真实连库测试（tests/test_execution_db.py 使用）
#
# 连接来源（按优先级）：
#   1. 环境变量 TEST_DATABASE_URL（CI / 已启动的 postgres:16-alpine）
#   2. 本机 postgres（docker compose up -d db 后默认映射到 55432）
#
# 两种方式都不可用时，本批用例整体 skip —— 它们守住的是"生成的 SQL 真
# 能在 Postgres 上跑出正确数字"，在没有数据库的环境里不做假断言。
# ======================================================================
SAMPLE_SCHEMA = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "db", "init", "01_sample_schema.sql",
)

_DB_CANDIDATES = [
    os.environ.get("TEST_DATABASE_URL"),
    "postgresql://drag:drag@localhost:55432/dragdb",
]


class Db:
    """同步封装：在一条专用事件循环上跑 asyncpg。"""

    def __init__(self, dsn: str):
        self.dsn = dsn
        self.loop = asyncio.new_event_loop()
        self.conn = self.loop.run_until_complete(self._connect())

    async def _connect(self):
        import asyncpg
        return await asyncpg.connect(self.dsn)

    def fetch(self, sql, *params):
        return self.loop.run_until_complete(self.conn.fetch(sql, *params))

    def fetchval(self, sql, *params):
        return self.loop.run_until_complete(
            self.conn.fetchval(sql, *params))

    def execute(self, sql, *params):
        return self.loop.run_until_complete(
            self.conn.execute(sql, *params))

    def close(self):
        self.loop.run_until_complete(self.conn.close())
        self.loop.close()


@pytest.fixture(scope="session")
def db_dsn():
    import asyncpg

    async def probe(dsn):
        try:
            c = await asyncio.wait_for(asyncpg.connect(dsn), timeout=3)
            await c.close()
            return True
        except Exception:
            return False

    loop = asyncio.new_event_loop()
    try:
        for dsn in _DB_CANDIDATES:
            if dsn and loop.run_until_complete(probe(dsn)):
                return dsn
    finally:
        loop.close()
    pytest.skip("没有可用的 Postgres（设置 TEST_DATABASE_URL 或先 "
                "`docker compose up -d db`）")


@pytest.fixture()
def sample_db(db_dsn):
    """每个用例重建样例库，返回已就绪的 Db（默认 search_path，刻意不含 biz）。

    生产路径靠生成 SQL 里的全限定名定位 biz 模式，而不是靠会话设置，
    因此这里绝不 SET search_path TO biz —— 一旦全限定名退化，用例立即红。
    """
    db = Db(db_dsn)
    with open(SAMPLE_SCHEMA, "r", encoding="utf-8") as f:
        db.execute(f.read())
    db.execute('SET search_path TO "$user", public')
    yield db
    db.close()
