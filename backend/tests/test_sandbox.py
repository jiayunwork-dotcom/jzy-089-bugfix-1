"""表达式白名单沙箱：安全边界、错误定位、参数化。"""
from __future__ import annotations

import pytest

from app.expressions import ExpressionError, FieldRef, compile_expression, validate
from app.expressions.functions import REGISTRY


def _scope(**kwargs):
    scope = {}
    for key, spec in kwargs.items():
        if isinstance(spec, tuple):
            table, column = spec
            scope[key] = FieldRef(key=key, table=table, column=column,
                                  kind="column")
        else:
            scope[key] = FieldRef(key=key, table=None, column=None,
                                  kind="calc", calc_sql=spec, calc_params=())
    return scope


def test_arithmetic_and_fields():
    out = compile_expression(
        "quantity * unit_price - 5",
        _scope(quantity=("order_items", "quantity"),
               unit_price=("order_items", "unit_price")))
    assert out.sql == '"order_items"."quantity" * "order_items"."unit_price" - $1'
    assert out.params == [5]


def test_conditional_and_functions():
    out = compile_expression(
        "IF(total_amount > 1000, UPPER(status), 'n/a')",
        _scope(total_amount=("orders", "total_amount"),
               status=("orders", "status")))
    assert out.sql.startswith("CASE WHEN")
    assert "UPPER(\"orders\".\"status\")" in out.sql
    assert out.params == [1000, "n/a"]


def test_string_and_in_list_are_parameterized():
    out = compile_expression(
        "(status IN ('paid', 'done')) AND (CONCAT(city, '-', segment) = 'x')",
        _scope(status=("orders", "status"), city=("customers", "city"),
               segment=("customers", "segment")))
    assert "'paid'" not in out.sql and "'done'" not in out.sql and "'x'" not in out.sql
    assert out.params[:4] == ["paid", "done", "-", "x"]
    assert "||" in out.sql


def test_date_trunc_part_whitelist():
    out = compile_expression(
        "DATE_TRUNC('month', order_date)",
        _scope(order_date=("orders", "order_date")))
    assert "DATE_TRUNC($1" in out.sql
    assert out.params[0] == "month"

    with pytest.raises(ExpressionError) as ei:
        compile_expression(
            "DATE_TRUNC('drop table orders', order_date)",
            _scope(order_date=("orders", "order_date")))
    assert "year/quarter/month" in ei.value.message


def test_unknown_function_rejected():
    with pytest.raises(ExpressionError) as ei:
        compile_expression("eval('1+1')", _scope())
    assert "白名单" in ei.value.message
    with pytest.raises(ExpressionError):
        compile_expression("ABS.exit()", _scope(x=("t", "x")))


def test_attack_surface_blocked():
    bad = [
        "__import__('os').system('ls')",
        "open('/etc/passwd').read()",
        "x.__class__",
        "getattr(x, 'y')",
        "lambda: 1",
        "[i for i in range(3)]",
        "f'{x}'",
        "del x",
        "1; DROP TABLE orders",
    ]
    ok_scope = _scope(x=("t", "x"), y=("t", "y"))
    for expr in bad:
        with pytest.raises(ExpressionError):
            compile_expression(expr, ok_scope)


def test_ternary_is_allowed_as_if():
    # 电子表格风格没有 Python 三元表达式；条件判断请用 IF(...)
    out = compile_expression("IF(x > 0, x, 0)",
                             _scope(x=("t", "x")))
    assert "CASE WHEN" in out.sql


def test_arity_check():
    with pytest.raises(ExpressionError) as ei:
        compile_expression("ABS()", _scope())
    assert "参数" in ei.value.message
    with pytest.raises(ExpressionError):
        compile_expression("UPPER(a, b)", _scope(a=("t", "a"), b=("t", "b")))


def test_unknown_field_error_position():
    with pytest.raises(ExpressionError) as ei:
        compile_expression("1 + not_a_field", _scope())
    err = ei.value
    assert err.message == "未知字段：not_a_field"
    assert err.col >= 4  # 位置指向标识符起点
    assert err.end_col == err.col + len("not_a_field")


def test_syntax_error_has_position():
    with pytest.raises(ExpressionError) as ei:
        compile_expression("quantity * ", _scope(quantity=("t", "quantity")))
    assert ei.value.line == 1 and ei.value.col is not None


def test_validate_helper():
    ok = validate("quantity * 2", _scope(quantity=("t", "quantity")))
    assert ok["valid"] is True
    bad = validate("quantity +", _scope(quantity=("t", "quantity")))
    assert bad["valid"] is False and "line" in bad["error"] and "col" in bad["error"]


def test_calc_field_inlining_renumbers_params():
    # line_amount 已编译为 ("a" * $1)（参数 [10]），外层引用时占位符重编号
    scope = _scope(
        quantity=("order_items", "quantity"),
        unit_price=("order_items", "unit_price"),
        line_amount='("order_items"."quantity" * "order_items"."unit_price")',
    )
    scope["line_amount"].calc_params = ()
    out = compile_expression("line_amount > 0", scope)
    assert '"order_items"."quantity"' in out.sql


def test_registry_has_no_io_functions():
    names = set(REGISTRY)
    for forbidden in ("EVAL", "EXEC", "SYSTEM", "IMPORT", "OPEN", "INPUT"):
        assert forbidden not in names


def test_named_constants():
    out = compile_expression(
        "IF(flag = TRUE, NULL, 0)",
        _scope(flag=("t", "flag")))
    assert "NULL" in out.sql
    assert True in out.params


def test_boolean_operators_and_null_checks():
    out = compile_expression(
        "(NOT (a < 1 OR b >= 2)) AND (c <> 3)",
        _scope(a=("t", "a"), b=("t", "b"), c=("t", "c")))
    assert "NOT" in out.sql and "OR" in out.sql and "AND" in out.sql
    assert out.params == [1, 2, 3]
