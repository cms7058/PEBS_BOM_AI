"""Idempotent migration: create the brand_entries table.

Run after pulling step 3.5. The seeded ComponentCategory table already
exists from migrate_categories.py. This script only adds the brand layer.

Usage:
  cd apps/api
  .venv/bin/python -m scripts.migrate_brands
"""

from __future__ import annotations

import asyncio

from sqlalchemy import inspect

from app.db import engine
from app.models import Base


async def run() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        tables = await conn.run_sync(
            lambda sync_conn: set(inspect(sync_conn).get_table_names())
        )
        if "brand_entries" in tables:
            print("[ok] brand_entries table present")
        else:
            print("[err] brand_entries table missing after create_all")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
