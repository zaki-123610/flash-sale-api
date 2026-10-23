from typing import Annotated

from fastapi import Depends, Request
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.core.config import settings


def create_pool() -> ConnectionPool:
    return ConnectionPool(
        settings.database_url,
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        # prepare_threshold=None keeps psycopg compatible with PgBouncer (Neon's pooled endpoint).
        kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": None},
        check=ConnectionPool.check_connection,
        open=False,
    )


def get_pool(request: Request) -> ConnectionPool:
    return request.app.state.pool


DbPool = Annotated[ConnectionPool, Depends(get_pool)]
