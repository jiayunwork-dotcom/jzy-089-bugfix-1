"""只读守卫：写操作 / DDL / 多语句一律拒绝。"""
from __future__ import annotations

import pytest

from app.execution import UnsafeQueryError, assert_select_only

SELECT = 'SELECT "orders"."status", SUM("orders"."total_amount") FROM "orders" GROUP BY 1'


@pytest.mark.parametrize("sql", [
    SELECT,
    "WITH x AS (SELECT 1 AS a) SELECT a FROM x",
    "SELECT 'INSERT' AS word",          # 字面量里的关键字不算
    'SELECT "update" FROM t',           # 引号标识符里的不算
    "SELECT 1 -- DROP TABLE x\nWHERE 1=1",
])
def test_accepts_select(sql):
    assert_select_only(sql)


@pytest.mark.parametrize("sql", [
    "INSERT INTO orders VALUES (1)",
    "UPDATE orders SET status='x'",
    "DELETE FROM orders",
    "DROP TABLE orders",
    "ALTER TABLE orders ADD COLUMN x int",
    "CREATE TABLE x (a int)",
    "TRUNCATE orders",
    "GRANT SELECT ON orders TO bob",
    "SELECT 1; DROP TABLE orders",
    "SELECT 1; SELECT 2",
    "COPY orders FROM '/etc/passwd'",
    "CALL do_something()",
    "SET statement_timeout = 0",
    "MERGE INTO orders o USING s ON 1=1 WHEN MATCHED THEN DELETE",
])
def test_rejects_non_select(sql):
    with pytest.raises(UnsafeQueryError):
        assert_select_only(sql)


def test_empty_rejected():
    with pytest.raises(UnsafeQueryError):
        assert_select_only("")
