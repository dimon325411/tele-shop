"""Fixtures for tests against real PostgreSQL / Redis.

Enabled by environment variables (docker compose sets them for the ``tests`` service):
  TEST_DATABASE_URL        e.g. postgresql+psycopg://market:market@postgres:5432/market_test
  TEST_DATABASE_ADMIN_URL  a database used to (re)create the test database
  TEST_REDIS_URL           e.g. redis://redis:6379/15
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DB_URL = os.environ.get("TEST_DATABASE_URL")
ADMIN_URL = os.environ.get("TEST_DATABASE_ADMIN_URL")
REDIS_URL = os.environ.get("TEST_REDIS_URL")


def _split(sql: str) -> list[str]:
    lines = [line for line in sql.splitlines() if not line.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


@pytest.fixture
async def sql_repo():
    if not DB_URL or not ADMIN_URL:
        pytest.skip("TEST_DATABASE_URL / TEST_DATABASE_ADMIN_URL not set")
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.storage.sql import SqlRepository, make_engine

    db_name = DB_URL.rsplit("/", 1)[1]
    admin = create_async_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.exec_driver_sql(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
        await conn.exec_driver_sql(f'CREATE DATABASE "{db_name}"')
    await admin.dispose()

    engine = make_engine(DB_URL)
    async with engine.begin() as conn:
        for migration in sorted((ROOT / "migrations/sql").glob("*.sql")):
            for stmt in _split(migration.read_text(encoding="utf-8")):
                await conn.exec_driver_sql(stmt)
    repo = SqlRepository(engine)
    yield repo
    await engine.dispose()


@pytest.fixture
async def redis_client():
    if not REDIS_URL:
        pytest.skip("TEST_REDIS_URL not set")
    import redis.asyncio as aioredis

    client = aioredis.from_url(REDIS_URL, decode_responses=True)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()
