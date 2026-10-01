"""计算字段表达式的白名单沙箱（电子表格风格语法）。

语法（手写 tokenizer + Pratt parser，不依赖 Python 语法）：
    expr    := or_expr
    or_expr := and_expr (OR and_expr)*
    and_expr:= not_expr (AND not_expr)*
    not_expr:= NOT not_expr | cmp
    cmp     := add ((= <> != < <= > >= | IN | NOT IN | BETWEEN a AND b) add)?
    add     := mul ((+ -) mul)*
    mul     := unary ((* / %) unary)*
    unary   := (- +) unary | primary
    primary := 数字 | 字符串 | TRUE/FALSE/NULL | 字段名
             | 函数名(args) | '(' expr ')'

安全边界：
1. 不是 token 白名单 / 函数白名单里的东西一律拒绝；没有属性访问、下标、
   lambda、import 等任何逃生路径；
2. 字段名只能解析到模型已存在的物理列或已保存的计算字段；
3. 字面量全部变成参数占位符 $n，永不拼进 SQL；
4. 错误带行列位置（line/col/end_col，0-based 列偏移），保存时即可校验。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from . import functions as funcs


def quote_ident(name: str) -> str:
    """把标识符包成双引号引用（内部双引号转义）。模型登记的名字受校验器约束，
    转义只是纵深防御。"""
    return '"' + str(name).replace('"', '""') + '"'


def quote_ref(schema: Optional[str], table: Optional[str],
              column: Optional[str]) -> str:
    """物理列引用：模式以模型登记为准，生成全限定名。

    schema/table 为 None 的少数场景（测试桩）退回较短形式。
    """
    parts = [p for p in (schema, table, column) if p]
    return ".".join(quote_ident(p) for p in parts)


class ExpressionError(Exception):
    def __init__(self, message: str, line: int = 1, col: int = 0,
                 end_col: Optional[int] = None):
        super().__init__(message)
        self.message = message
        self.line = line          # 1-based
        self.col = col            # 0-based
        self.end_col = end_col

    def to_dict(self) -> dict:
        return {"message": self.message, "line": self.line,
                "col": self.col, "end_col": self.end_col}


@dataclass
class FieldRef:
    key: str
    table: Optional[str]
    column: Optional[str] = None
    kind: str = "column"           # "column" | "calc"
    schema_name: Optional[str] = None  # 物理列所属模式（None 时退回不限定）
    calc_sql: Optional[str] = None
    calc_params: tuple = ()
    calc_refs: tuple = ()
    calc_join_tables: tuple = ()


@dataclass
class CompiledExpression:
    sql: str
    params: List[object] = field(default_factory=list)
    referenced_keys: List[str] = field(default_factory=list)


# ----------------------------------------------------------------------
# Tokenizer
# ----------------------------------------------------------------------
@dataclass
class _Tok:
    kind: str                     # num/str/ident/op/lp/rp/comma/end
    value: object
    line: int
    col: int
    end_col: int


_TOKEN_RE = re.compile(
    r"""
      (?P<ws>\s+)
    | (?P<num>\d+\.\d+|\d+)
    | (?P<str>'(?:[^']|'')*')
    | (?P<ident>[A-Za-z_][A-Za-z0-9_]*)
    | (?P<op><=|>=|<>|!=|[-+*/%=<>(),])
    """,
    re.VERBOSE,
)


def _tokenize(source: str) -> List[_Tok]:
    toks: List[_Tok] = []
    i, n = 0, len(source)
    line = 1
    line_start = 0
    while i < n:
        m = _TOKEN_RE.match(source, i)
        if not m:
            raise ExpressionError(
                f"无法识别的字符：{source[i]!r}", line, i - line_start)
        kind = m.lastgroup
        text = m.group()
        col = i - line_start
        if kind == "ws":
            if "\n" in text:
                line += text.count("\n")
                line_start = i + text.rfind("\n") + 1
        elif kind == "num":
            toks.append(_Tok("num", float(text) if "." in text else int(text),
                             line, col, col + len(text)))
        elif kind == "str":
            val = text[1:-1].replace("''", "'")
            toks.append(_Tok("str", val, line, col, col + len(text)))
        elif kind == "ident":
            toks.append(_Tok("ident", text, line, col, col + len(text)))
        else:
            if text == "(":
                toks.append(_Tok("lp", text, line, col, col + 1))
            elif text == ")":
                toks.append(_Tok("rp", text, line, col, col + 1))
            elif text == ",":
                toks.append(_Tok("comma", text, line, col, col + 1))
            else:
                toks.append(_Tok("op", text, line, col, col + 1))
        i = m.end()
    toks.append(_Tok("end", None, line, n - line_start, n - line_start))
    return toks


# ----------------------------------------------------------------------
# Parser / compiler（递归下降；编译即产出参数化 SQL 片段）
# ----------------------------------------------------------------------
class _Compiler:
    def __init__(self, source: str, available: Dict[str, FieldRef],
                 params: List[object]):
        self.source = source
        self.toks = _tokenize(source)
        self.pos = 0
        self.available = available
        self.params = params
        self.referenced: List[str] = []
        self.compiling: Set[str] = set()

    # ---- 基础工具 ----
    @property
    def cur(self) -> _Tok:
        return self.toks[self.pos]

    def advance(self) -> _Tok:
        t = self.toks[self.pos]
        self.pos += 1
        return t

    def expect(self, kind: str, what: str) -> _Tok:
        t = self.cur
        if t.kind != kind:
            raise ExpressionError(f"此处应当是 {what}", t.line, t.col, t.end_col)
        return self.advance()

    def at_kw(self, *words: str) -> bool:
        t = self.cur
        return t.kind == "ident" and t.value.upper() in words

    def take_kw(self, word: str) -> bool:
        if self.at_kw(word):
            self.advance()
            return True
        return False

    def placeholder(self, value: object, tok: _Tok) -> str:
        self.params.append(value)
        return f"${len(self.params)}"

    # ---- 递归下降 ----
    def parse(self) -> str:
        sql = self.parse_or()
        if self.cur.kind != "end":
            t = self.cur
            raise ExpressionError("表达式在此之后还有多余内容",
                                  t.line, t.col, t.end_col)
        return sql

    def parse_or(self) -> str:
        left = self.parse_and()
        parts = [left]
        while self.take_kw("OR"):
            parts.append(self.parse_and())
        return self._join("OR", parts)

    def parse_and(self) -> str:
        left = self.parse_not()
        parts = [left]
        while self.take_kw("AND"):
            parts.append(self.parse_not())
        return self._join("AND", parts)

    @staticmethod
    def _join(kw: str, parts: List[str]) -> str:
        if len(parts) == 1:
            return parts[0]
        return "(" + f" {kw} ".join(parts) + ")"

    def parse_not(self) -> str:
        if self.take_kw("NOT"):
            t = self.toks[self.pos - 1]
            inner = self.parse_not()
            return f"(NOT {inner})"
        return self.parse_cmp()

    def parse_cmp(self) -> str:
        left = self.parse_add()
        t = self.cur
        neg = False
        if self.take_kw("NOT"):
            neg = True
            t = self.cur
        if self.at_kw("IN"):
            self.advance()
            self.expect("lp", "(")
            vals = [self.parse_add()]
            while self.cur.kind == "comma":
                self.advance()
                vals.append(self.parse_add())
            self.expect("rp", ")")
            kw = "NOT IN" if neg else "IN"
            return f"({left} {kw} ({', '.join(vals)}))"
        if neg and not self.at_kw("IN"):
            raise ExpressionError("NOT 后面只能跟 IN", t.line, t.col, t.end_col)
        if self.at_kw("BETWEEN"):
            self.advance()
            lo = self.parse_add()
            if not self.take_kw("AND"):
                raise ExpressionError("BETWEEN 需要 AND", self.cur.line,
                                      self.cur.col, self.cur.end_col)
            hi = self.parse_add()
            prefix = "NOT " if neg else ""
            return f"({left} {prefix}BETWEEN {lo} AND {hi})"
        if neg:
            return left  # NOT 单独出现不合语法，但这里不可达
        if t.kind == "op" and t.value in ("=", "<>", "!=", "<", "<=", ">", ">="):
            self.advance()
            right = self.parse_add()
            sym = "<>" if t.value == "!=" else t.value
            return f"({left} {sym} {right})"
        return left

    def parse_add(self) -> str:
        left = self.parse_mul()
        while self.cur.kind == "op" and self.cur.value in ("+", "-"):
            op = self.advance().value
            right = self.parse_mul()
            left = f"{left} {op} {right}"
        return left

    def parse_mul(self) -> str:
        left = self.parse_unary()
        while self.cur.kind == "op" and self.cur.value in ("*", "/", "%"):
            op = self.advance().value
            right = self.parse_unary()
            left = f"{left} {op} {right}"
        return left

    def parse_unary(self) -> str:
        if self.cur.kind == "op" and self.cur.value in ("+", "-"):
            t = self.advance()
            return f"(-{self.parse_unary()})" if t.value == "-" \
                else self.parse_unary()
        return self.parse_primary()

    def parse_primary(self) -> str:
        t = self.cur
        if t.kind == "num":
            self.advance()
            return self.placeholder(t.value, t)
        if t.kind == "str":
            self.advance()
            return self.placeholder(t.value, t)
        if t.kind == "lp":
            self.advance()
            inner = self.parse_or()
            self.expect("rp", ")")
            return f"({inner})"
        if t.kind == "ident":
            return self.parse_ident_or_call()
        raise ExpressionError("此处应当是字段、字面量、函数或括号表达式",
                              t.line, t.col, t.end_col)

    def parse_ident_or_call(self) -> str:
        t = self.advance()
        word = t.value.upper()

        if word in ("TRUE", "FALSE"):
            return self.placeholder(word == "TRUE", t)
        if word == "NULL":
            return "NULL"

        # 函数调用（含白名单校验）
        if self.cur.kind == "lp":
            if word == "DATE_TRUNC":
                return self._parse_date_trunc(t)
            fn = funcs.REGISTRY.get(word)
            if fn is None:
                raise ExpressionError(f"未在白名单中的函数：{t.value}",
                                      t.line, t.col, t.end_col)
            args = self._parse_args()
            lo, hi = fn.arity
            if not (lo <= len(args) <= (hi if hi is not None else 10 ** 9)):
                want = f"{lo}" if hi == lo else (
                    f"{lo}~{hi}" if hi is not None else f"至少 {lo}")
                raise ExpressionError(
                    f"函数 {word} 需要 {want} 个参数，实得 {len(args)}",
                    t.line, t.col, t.end_col)
            return fn.emit(args, self.params)

        # 字段引用
        ref = self.available.get(t.value.lower())
        if ref is None:
            raise ExpressionError(f"未知字段：{t.value}",
                                  t.line, t.col, t.end_col)
        if t.value.lower() not in self.referenced:
            self.referenced.append(t.value.lower())
        if ref.kind == "column":
            return quote_ref(ref.schema_name, ref.table, ref.column)
        if t.value.lower() in self.compiling:
            raise ExpressionError(f"计算字段存在循环引用：{t.value}",
                                  t.line, t.col, t.end_col)
        self.compiling.add(t.value.lower())
        try:
            return self._inline_calc(ref)
        finally:
            self.compiling.discard(t.value.lower())

    def _parse_args(self) -> List[str]:
        self.expect("lp", "(")
        args: List[str] = []
        if self.cur.kind != "rp":
            args.append(self.parse_or())
            while self.cur.kind == "comma":
                self.advance()
                args.append(self.parse_or())
        self.expect("rp", ")")
        return args

    def _parse_date_trunc(self, name_tok: _Tok) -> str:
        self.expect("lp", "(")
        part_tok = self.cur
        if part_tok.kind != "str" or part_tok.value.lower() not in funcs.DATE_PARTS:
            raise ExpressionError(
                "DATE_TRUNC 第一参数必须是 year/quarter/month/week/day 之一",
                part_tok.line, part_tok.col, part_tok.end_col)
        self.advance()
        part_param = self.placeholder(part_tok.value, part_tok)
        self.expect("comma", ",")
        date_arg = self.parse_or()
        self.expect("rp", ")")
        return funcs.REGISTRY["DATE_TRUNC"].emit([part_param, date_arg],
                                                 self.params)

    def _inline_calc(self, ref: FieldRef) -> str:
        import re as _re
        used: Dict[int, int] = {}
        old_params = list(ref.calc_params)

        def repl(m):
            old = int(m.group(1))
            if old not in used:
                used[old] = len(self.params) + 1
                self.params.append(old_params[old - 1])
            return f"${used[old]}"

        return _re.sub(r"\$(\d+)", repl, ref.calc_sql or "")


# ----------------------------------------------------------------------
# 公共 API
# ----------------------------------------------------------------------
def compile_expression(source: str, available: Dict[str, FieldRef],
                       params: Optional[List[object]] = None
                       ) -> CompiledExpression:
    params = params if params is not None else []
    c = _Compiler(source, available, params)
    sql = c.parse()
    return CompiledExpression(sql=sql, params=params,
                              referenced_keys=list(c.referenced))


def collect_names(source: str, available: Dict[str, FieldRef]) -> List[str]:
    """只解析标识符引用（不内联计算字段、不产生 SQL），用于依赖排序。"""
    try:
        toks = _tokenize(source)
    except ExpressionError:
        # 让正式编译阶段报更完整的错误
        compile_expression(source, available)
        raise
    found: List[str] = []
    # 找出函数名 token（后面紧跟 '('）
    func_positions = set()
    for i, tok in enumerate(toks[:-1]):
        if tok.kind == "ident" and toks[i + 1].kind == "lp":
            func_positions.add(i)
    for i, tok in enumerate(toks):
        if tok.kind != "ident" or i in func_positions:
            continue
        word = tok.value.upper()
        if word in ("TRUE", "FALSE", "NULL", "AND", "OR", "NOT", "IN",
                    "BETWEEN"):
            continue
        key = tok.value.lower()
        if key not in available:
            raise ExpressionError(f"未知字段：{tok.value}",
                                  tok.line, tok.col, tok.end_col)
        if key not in found:
            found.append(key)
    return found


def validate(source: str, available: Dict[str, FieldRef]) -> dict:
    try:
        compiled = compile_expression(source, available)
    except ExpressionError as e:
        return {"valid": False, "error": e.to_dict()}
    return {"valid": True, "referenced": compiled.referenced_keys}
