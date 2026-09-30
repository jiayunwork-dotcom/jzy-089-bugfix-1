"""生成 SQL 的只读守卫（纵深防御的第二道：第一道是"我们自己生成"）。

即使将来内核扩展引入新表达式，这里也保证发往数据库的只能是：
- 单条 SELECT 语句（WITH ... SELECT 也允许，本内核不生成）；
- 不含任何写操作 / DDL / 会话控制关键字；
- 不含语句分隔符（防多语句）。

检查时会跳过字符串字面量与引号标识符内部，避免列名注释误伤。
"""
from __future__ import annotations

import re

FORBIDDEN_KEYWORDS = {
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE",
    "GRANT", "REVOKE", "COPY", "CALL", "DO", "EXECUTE", "EXEC", "MERGE",
    "REPLACE", "VACUUM", "REINDEX", "CLUSTER", "COMMENT", "REFRESH",
    "LOCK", "PREPARE", "DEALLOCATE", "DISCARD", "SET", "RESET",
    "BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "CHECKPOINT",
    "SECURITY", "LANGUAGE",
}

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class UnsafeQueryError(ValueError):
    pass


def assert_select_only(sql: str) -> None:
    stripped = _strip_literals_and_comments(sql)
    tokens = _WORD.findall(stripped)
    if not tokens:
        raise UnsafeQueryError("空查询")
    first = tokens[0].upper()
    if first not in ("SELECT", "WITH"):
        raise UnsafeQueryError(f"只允许 SELECT 查询，实际以 {first} 开头")
    if first == "WITH" and not _with_is_select_only(tokens):
        raise UnsafeQueryError("WITH 查询中只允许 SELECT")
    for tok in tokens[1:]:
        if tok.upper() in FORBIDDEN_KEYWORDS:
            raise UnsafeQueryError(f"查询包含被禁止的关键字：{tok.upper()}")
    if ";" in stripped.rstrip():
        raise UnsafeQueryError("查询不允许包含多条语句（';'）")


def _with_is_select_only(tokens: list[str]) -> bool:
    # 粗校验：WITH 后必须能找到 SELECT 作为实际查询体
    return any(t.upper() == "SELECT" for t in tokens[1:])


def _strip_literals_and_comments(sql: str) -> str:
    """把字符串/转义字符串/引用标识符内容与注释替换成空格，长度保持。"""
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        if ch == "'":
            out.append("'"); i += 1
            while i < n:
                if sql[i] == "'" and (i + 1 >= n or sql[i + 1] != "'"):
                    out.append("'"); i += 1
                    break
                out.append(" " if sql[i] != "\n" else "\n"); i += 1
        elif ch == '"':
            out.append('"'); i += 1
            while i < n:
                if sql[i] == '"':
                    out.append('"'); i += 1
                    break
                out.append(" " if sql[i] != "\n" else "\n"); i += 1
        elif ch == "-" and nxt == "-":
            while i < n and sql[i] != "\n":
                out.append(" "); i += 1
        elif ch == "/" and nxt == "*":
            i += 2
            while i + 1 < n and not (sql[i] == "*" and sql[i + 1] == "/"):
                out.append(" " if sql[i] != "\n" else "\n"); i += 1
            i += 2
        else:
            out.append(ch); i += 1
    return "".join(out)
