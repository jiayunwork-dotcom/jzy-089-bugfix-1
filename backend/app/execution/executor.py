"""查询执行与结果整形。

执行侧护栏（与 SQL 内核的参数化互为纵深）：
- guard 断言只允许单条 SELECT；
- 事务显式 SET TRANSACTION READ ONLY；
- statement_timeout 限制拖库；
- 行数硬上限。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List

from .. import config
from ..sqlgen import GeneratedQuery
from .guard import assert_select_only

if TYPE_CHECKING:  # 仅类型检查时依赖 asyncpg
    import asyncpg


@dataclass
class QueryResult:
    columns: List[Dict[str, str]]
    rows: List[List[Any]]
    row_count: int
    truncated: bool

    def to_dict(self) -> dict:
        return {
            "columns": self.columns,
            "rows": [[_json_safe(v) for v in row] for row in self.rows],
            "row_count": self.row_count,
            "truncated": self.truncated,
        }


async def execute_generated(pool: "asyncpg.Pool",
                            query: GeneratedQuery,
                            limit: int) -> QueryResult:
    assert_select_only(query.sql)
    effective_limit = min(max(int(limit), 1), config.MAX_ROWS)

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SET TRANSACTION READ ONLY")
            await conn.execute(
                f"SET LOCAL statement_timeout = {config.STATEMENT_TIMEOUT_MS}")
            stmt = await conn.prepare(query.sql)
            records = await stmt.fetch(*query.params)
            # 列元数据必须在连接释放前取出（连接归还池后 stmt 即失效）
            attributes = stmt.get_attributes()

    columns = [
        {"key": c.name.strip('"'), "name": _label(c.name, query),
         "data_type": _type_name(c.type.name if c.type else None)}
        for c in attributes
    ]
    rows = [list(rec.values()) for rec in records[:effective_limit]]
    return QueryResult(
        columns=columns,
        rows=rows,
        row_count=len(rows),
        truncated=len(records) > effective_limit,
    )


def _label(column_name: str, query: GeneratedQuery) -> str:
    key = column_name.strip('"')
    for c in query.columns:
        if c.key == key:
            return c.label
    return key


_TYPE_MAP = {
    "int2": "integer", "int4": "integer", "int8": "integer",
    "float4": "number", "float8": "number", "numeric": "number",
    "text": "text", "varchar": "text", "bpchar": "text",
    "date": "date", "timestamp": "date", "timestamptz": "date",
    "bool": "boolean",
}


def _type_name(pg_type: str | None) -> str:
    if not pg_type:
        return "text"
    base = pg_type.split("(")[0].lower()
    return _TYPE_MAP.get(base, "text")


def _json_safe(value: Any) -> Any:
    # asyncpg 已返回 date/datetime/Decimal；日期转 ISO 字符串，Decimal 转 float
    import datetime
    from decimal import Decimal
    if isinstance(value, (datetime.date, datetime.datetime, datetime.time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value
