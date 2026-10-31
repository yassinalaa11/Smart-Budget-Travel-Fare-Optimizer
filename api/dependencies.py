from typing import Annotated

import asyncpg
from fastapi import Depends, Request


async def get_db_pool(request: Request) -> asyncpg.Pool:
    return request.app.state.db_pool


DatabasePool = Annotated[asyncpg.Pool, Depends(get_db_pool)]
