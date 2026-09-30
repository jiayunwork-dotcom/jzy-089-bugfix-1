"""FastAPI 应用入口：组装路由、连接池、模型存储。

前端构建产物由 nginx 托管并把 /api 反代到本服务；
本地开发时开 CORS 供 Vite dev server 直连。
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import config
from .modeling.store import ModelStore
from .routes import ROUTERS

logger = logging.getLogger("dragquery")


async def _connect_with_retry(dsn: str, attempts: int = 30, delay: float = 1.0):
    import asyncpg
    last = None
    for i in range(attempts):
        try:
            return await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=10)
        except Exception as e:  # 数据库容器可能尚未就绪
            last = e
            logger.info("等待数据库就绪 (%s/%s): %s", i + 1, attempts, e)
            await asyncio.sleep(delay)
    raise RuntimeError(f"数据库连接失败：{last}")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    pool = await _connect_with_retry(config.DATABASE_URL)
    app.state.pool = pool
    app.state.store = ModelStore(pool)
    await app.state.store.init_schema()
    try:
        yield
    finally:
        await pool.close()


def create_app() -> FastAPI:
    app = FastAPI(title="DragQuery 自助查询", version="1.0.0",
                  lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for router in ROUTERS:
        app.include_router(router)

    @app.get("/api/health")
    async def health():
        return {"ok": True}

    return app


app = create_app()
