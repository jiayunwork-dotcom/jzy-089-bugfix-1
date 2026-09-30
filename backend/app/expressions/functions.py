"""表达式沙箱允许使用的安全函数白名单。

每个登记项描述：
- arity: 固定参数个数，或用 (min, max) 表示区间，max 可为 None（变长）
- emit(call, args_sql, params) -> str：如何把函数调用翻译成 SQL
  （字面量在沙箱层已经变成参数占位符，这里不接触任何用户字符串）

这里不做任何 Python 侧的求值，全部职责只是"语法树 -> 参数化 SQL 片段"。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple


@dataclass(frozen=True)
class SafeFunction:
    name: str
    arity: Tuple[int, Optional[int]]  # (最小参数数, 最大参数数)
    emit: Callable[[List[str], List[object]], str]


def _plain(sql_name: str, arity: Tuple[int, Optional[int]] = (1, 1)):
    def emit(args: List[str], params: List[object]) -> str:
        return f"{sql_name}({', '.join(args)})"
    return emit


def _if(args: List[str], params: List[object]) -> str:
    return f"CASE WHEN {args[0]} THEN {args[1]} ELSE {args[2]} END"


def _concat(args: List[str], params: List[object]) -> str:
    return "(" + " || ".join(args) + ")"


def _substring(args: List[str], params: List[object]) -> str:
    # SUBSTRING(s FROM start [FOR length])
    if len(args) == 2:
        return f"SUBSTRING({args[0]} FROM {args[1]})"
    return f"SUBSTRING({args[0]} FROM {args[1]} FOR {args[2]})"


def _date_trunc(args: List[str], params: List[object]) -> str:
    # 日期部分（'year' 等）在调用处已校验为白名单枚举并以参数传入
    return f"DATE_TRUNC({args[0]}, {args[1]})"


def _now(args: List[str], params: List[object]) -> str:
    return "CURRENT_TIMESTAMP"


def _current_date(args: List[str], params: List[object]) -> str:
    return "CURRENT_DATE"


def _datediff(args: List[str], params: List[object]) -> str:
    # DATEDIFF(part, a, b) -> (b::date - a::date) 整数天；part 仅允许 'day'
    return f"({args[2]}::date - {args[1]}::date)"


# 数值 / 条件 / 空值 / 字符串 / 日期，全部是数据库内置纯函数，
# 没有任何一条能触达文件系统、网络或系统命令。
REGISTRY = {
    f.name: f
    for f in [
        SafeFunction("IF", (3, 3), _if),
        SafeFunction("COALESCE", (1, None), _plain("COALESCE")),
        SafeFunction("NULLIF", (2, 2), _plain("NULLIF")),
        SafeFunction("ABS", (1, 1), _plain("ABS")),
        SafeFunction("ROUND", (1, 2), _plain("ROUND")),
        SafeFunction("FLOOR", (1, 1), _plain("FLOOR")),
        SafeFunction("CEIL", (1, 1), _plain("CEIL")),
        SafeFunction("POWER", (2, 2), _plain("POWER")),
        SafeFunction("MOD", (2, 2), _plain("MOD")),
        SafeFunction("UPPER", (1, 1), _plain("UPPER")),
        SafeFunction("LOWER", (1, 1), _plain("LOWER")),
        SafeFunction("LENGTH", (1, 1), _plain("LENGTH")),
        SafeFunction("TRIM", (1, 1), _plain("TRIM")),
        SafeFunction("CONCAT", (1, None), _concat),
        SafeFunction("SUBSTRING", (2, 3), _substring),
        SafeFunction("LEFT", (2, 2), _plain("LEFT")),
        SafeFunction("RIGHT", (2, 2), _plain("RIGHT")),
        SafeFunction("DATE_TRUNC", (2, 2), _date_trunc),
        SafeFunction("DATEDIFF", (3, 3), _datediff),
        SafeFunction("NOW", (0, 0), _now),
        SafeFunction("CURRENT_DATE", (0, 0), _current_date),
    ]
}

# DATE_TRUNC 的日期部分白名单，作为字面量参数传入
DATE_PARTS = {
    "year", "quarter", "month", "week", "day", "hour", "minute", "second",
}

# 常量白名单
NAMED_CONSTANTS = {"TRUE", "FALSE", "NULL"}
