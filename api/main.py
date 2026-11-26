from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

import asyncpg
from fastapi import FastAPI

from api.routers.predictions import router as predictions_router
from api.routers.price_history import router as price_history_router
from api.routers.recommendations import router as recommendations_router
from ml.registry import ModelRegistry


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required to start the API")
    database_url = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    app.state.db_pool = await asyncpg.create_pool(database_url, min_size=1, max_size=5)
    app.state.model_registry = ModelRegistry()
    try:
        yield
    finally:
        await app.state.db_pool.close()


app = FastAPI(title="SmartFare API", version="0.2.0", lifespan=lifespan)
app.include_router(price_history_router, prefix="/api/v1")
app.include_router(recommendations_router, prefix="/api/v1")
app.include_router(predictions_router, prefix="/api/v1")


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
