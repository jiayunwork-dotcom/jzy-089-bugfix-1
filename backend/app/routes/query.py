"""查询路由：把拖拽意图翻译成 SQL 并执行。"""
from __future__ import annotations

import datetime
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..execution import UnsafeQueryError, execute_generated
from ..sqlgen import (FilterSpec, GroupEntry, QueryBuildError, QuerySpec,
                      SqlGenerator, apply_drill)

router = APIRouter(prefix="/api", tags=["query"])


class QueryIn(BaseModel):
    rows: list[GroupEntry] = Field(default_factory=list)
    columns: list[GroupEntry] = Field(default_factory=list)
    measures: list[str] = Field(default_factory=list)
    filters: list[FilterSpec] = Field(default_factory=list)
    limit: int = 200
    # 下钻：在该维度（必须已在行/列区）后追加其层级的更细一级
    drill: str | None = None
    execute: bool = True


def _json_safe(value):
    if isinstance(value, (datetime.date, datetime.datetime, datetime.time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


@router.post("/query")
async def run_query(body: QueryIn, request: Request):
    model = await request.app.state.store.get()
    spec = QuerySpec(rows=body.rows, columns=body.columns,
                     measures=body.measures, filters=body.filters,
                     limit=body.limit)
    try:
        if body.drill:
            spec = apply_drill(model, spec, body.drill)
        generated = SqlGenerator(model).generate(spec)
    except QueryBuildError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except UnsafeQueryError as e:
        # 内核只应生成 SELECT；命中说明内核有缺陷，按 500 暴露
        raise HTTPException(status_code=500, detail=f"SQL 安全校验失败：{e}")

    payload = {
        "sql": generated.sql,
        "params": [_json_safe(p) for p in generated.params],
        "joined_tables": generated.joined_tables,
        "fanout_tables": generated.fanout_tables,
        "columns": [c.__dict__ for c in generated.columns],
        "spec": spec.model_dump(),
    }
    if body.execute:
        result = await execute_generated(
            request.app.state.pool, generated, spec.limit)
        payload.update(result.to_dict())
    return payload


@router.post("/query/explain")
async def explain_query(body: QueryIn, request: Request):
    """只翻译不执行，方便核对"拖拽 -> SQL"。"""
    body = body.model_copy(update={"execute": False})
    return await run_query(body, request)
