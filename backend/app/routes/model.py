"""建模相关路由：读取/保存模型、表达式保存前校验、数据库自省、维度枚举值。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..expressions import ExpressionError
from ..modeling.catalog import validate_calc_expression
from ..modeling.loader import model_from_dict
from ..modeling.validator import validate_model

router = APIRouter(prefix="/api", tags=["model"])


class ExpressionIn(BaseModel):
    table: str
    expression: str
    join_tables: list[str] = Field(default_factory=list)


class ModelIn(BaseModel):
    model: dict


def store(request: Request):
    return request.app.state.store


def pool(request: Request):
    return request.app.state.pool


@router.get("/model")
async def get_model(request: Request):
    model = await store(request).get()
    return model.model_dump()


@router.put("/model")
async def put_model(body: ModelIn, request: Request):
    model = model_from_dict(body.model)
    errors = validate_model(model)
    if errors:
        raise HTTPException(status_code=400, detail={"errors": errors})
    await store(request).save(model)
    return {"ok": True}


@router.post("/validate-expression")
async def validate_expression(body: ExpressionIn, request: Request):
    model = await store(request).get()
    if not model.has_table(body.table):
        raise HTTPException(400, detail=f"未知表：{body.table}")
    try:
        result = validate_calc_expression(
            model, body.table, body.expression, body.join_tables)
    except ExpressionError as e:
        return {"valid": False, "error": e.to_dict()}
    return result


@router.get("/dimensions/{dim_id}/values")
async def dimension_values(dim_id: str, request: Request, limit: int = 500):
    model = await store(request).get()
    try:
        d = model.dimension(dim_id)
    except KeyError:
        raise HTTPException(404, detail=f"未知维度：{dim_id}")
    from ..sqlgen.generator import SqlGenerator
    gen = SqlGenerator(model)
    generated = gen.generate_distinct(dim_id, min(int(limit), 1000))
    async with pool(request).acquire() as conn:
        async with conn.transaction():
            await conn.execute("SET TRANSACTION READ ONLY")
            rows = await conn.fetch(generated.sql, *generated.params)
    return {"values": [r[0] for r in rows if r[0] is not None]}


@router.get("/introspect/tables")
async def introspect_tables(request: Request, schema_name: str = "biz"):
    """自省物理库：供建模界面"导入表"时选择。"""
    rows = await pool(request).fetch(
        """
        SELECT c.table_name, c.column_name, c.data_type
        FROM information_schema.columns c
        WHERE c.table_schema = $1
        ORDER BY c.table_name, c.ordinal_position
        """,
        schema_name,
    )
    tables: dict = {}
    type_map = {
        "integer": "integer", "bigint": "integer", "smallint": "integer",
        "numeric": "number", "double precision": "number", "real": "number",
        "character varying": "text", "text": "text", "character": "text",
        "date": "date", "timestamp with time zone": "date",
        "timestamp without time zone": "date", "boolean": "boolean",
    }
    for r in rows:
        tables.setdefault(r["table_name"], []).append(
            {"name": r["column_name"],
             "data_type": type_map.get(r["data_type"], "text")})
    return {"schema": schema_name, "tables": tables}
