"""模型持久化：存在数据库 meta.model_definition 里。

首次启动若库里没有记录，则写入随服务自带的样例模型。
"""
from __future__ import annotations

import json
from typing import Optional

from .loader import load_sample_model, model_from_dict
from .schema import ModelSpec


class ModelStore:
    def __init__(self, pool):
        self._pool = pool
        self._cache: Optional[ModelSpec] = None

    async def init_schema(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "CREATE SCHEMA IF NOT EXISTS meta; "
                "CREATE TABLE IF NOT EXISTS meta.model_definition ("
                "  id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),"
                "  doc JSONB NOT NULL,"
                "  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()"
                ")"
            )
            row = await conn.fetchval(
                "SELECT doc FROM meta.model_definition WHERE id = 1")
            if row is None:
                sample = load_sample_model()
                await conn.execute(
                    "INSERT INTO meta.model_definition (id, doc) VALUES (1, $1::jsonb)",
                    json.dumps(sample.model_dump(), ensure_ascii=False),
                )

    async def get(self) -> ModelSpec:
        if self._cache is not None:
            return self._cache
        async with self._pool.acquire() as conn:
            raw = await conn.fetchval(
                "SELECT doc FROM meta.model_definition WHERE id = 1")
        if raw is None:
            self._cache = load_sample_model()
        else:
            self._cache = model_from_dict(json.loads(raw))
        return self._cache

    async def save(self, model: ModelSpec) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE meta.model_definition SET doc = $2::jsonb, "
                "updated_at = now() WHERE id = $1::integer",
                1, json.dumps(model.model_dump(), ensure_ascii=False),
            )
        self._cache = model

    def invalidate(self) -> None:
        self._cache = None
