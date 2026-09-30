"""整模型保存前的静态校验：关联字段、维度/度量/计算字段表达式。"""
from __future__ import annotations

from typing import List

from .catalog import validate_calc_expression
from .schema import ModelSpec
from ..expressions import ExpressionError


def validate_model(model: ModelSpec) -> List[dict]:
    errors: List[dict] = []

    table_names = {t.name for t in model.tables}
    col_map = {t.name: {c.name for c in t.columns} for t in model.tables}

    # 关联：两张表、两个字段都必须存在
    for rel in model.relations:
        for side_table, side_col in ((rel.left_table, rel.left_column),
                                     (rel.right_table, rel.right_column)):
            if side_table not in table_names:
                errors.append({"scope": f"relation:{rel.id}",
                               "message": f"关联引用了不存在的表 {side_table}"})
            elif side_col not in col_map[side_table]:
                errors.append({"scope": f"relation:{rel.id}",
                               "message": f"表 {side_table} 上不存在字段 {side_col}"})

    def fail(scope: str, e: ExpressionError):
        errors.append({"scope": scope, "message": e.message,
                       "line": e.line, "col": e.col, "end_col": e.end_col})

    # 维度表达式
    for d in model.dimensions:
        if d.expression and d.table in table_names:
            res = validate_calc_expression(model, d.table, d.expression)
            if not res["valid"]:
                errors.append({"scope": f"dimension:{d.id}",
                               **res["error"]})

    # 度量的基表达式与过滤表达式
    for m in model.measures:
        if m.table not in table_names:
            errors.append({"scope": f"measure:{m.id}",
                           "message": f"度量引用了不存在的表 {m.table}"})
            continue
        if m.column and m.column not in col_map[m.table]:
            errors.append({"scope": f"measure:{m.id}",
                           "message": f"表 {m.table} 上不存在字段 {m.column}"})
        for attr in ("expression", "filter"):
            source = getattr(m, attr)
            if source:
                res = validate_calc_expression(model, m.table, source)
                if not res["valid"]:
                    errors.append({"scope": f"measure:{m.id}.{attr}",
                                   **res["error"]})

    # 计算字段
    for c in model.calculated_fields:
        if c.table not in table_names:
            errors.append({"scope": f"calculated:{c.id}",
                           "message": f"计算字段引用了不存在的表 {c.table}"})
            continue
        for jt in c.join_tables:
            if jt not in table_names:
                errors.append({"scope": f"calculated:{c.id}",
                               "message": f"join_tables 引用了不存在的表 {jt}"})
        res = validate_calc_expression(
            model, c.table, c.expression, c.join_tables)
        if not res["valid"]:
            errors.append({"scope": f"calculated:{c.id}", **res["error"]})

    # 层级：成员必须是同序排列的维度
    for h in model.hierarchies:
        for i, did in enumerate(h.dimension_ids):
            try:
                d = model.dimension(did)
            except KeyError:
                errors.append({"scope": f"hierarchy:{h.id}",
                               "message": f"层级引用了不存在的维度 {did}"})
                continue
            if d.hierarchy_id != h.id or d.level_index != i:
                errors.append({
                    "scope": f"hierarchy:{h.id}",
                    "message": f"维度 {did} 的层级声明与层级 {h.id} 第 {i} 级不一致",
                })
    return errors
