"""Idempotent migration: add category_id/spec to bom_nodes + create
component_categories table.

Project doesn't use Alembic — `Base.metadata.create_all()` runs on app
startup but it only CREATES new tables, it doesn't ALTER existing ones to
add columns. So this script handles the ALTER itself.

SQLite-only (matches current dev setup). For Postgres later, the same
SQL works since SQLite ALTER TABLE syntax for ADD COLUMN is a strict
subset of Postgres'.

Usage:
  cd apps/api
  .venv/bin/python -m scripts.migrate_categories
"""

from __future__ import annotations

import asyncio

from sqlalchemy import inspect, text

from app.db import engine
from app.models import Base


async def run() -> None:
    # 1) Create any new tables (component_categories) declared on Base.metadata
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 2) ALTER bom_nodes to add category_id + spec if missing
    async with engine.begin() as conn:
        cols = await conn.run_sync(
            lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("bom_nodes")}
        )
        if "category_id" not in cols:
            await conn.execute(
                text(
                    "ALTER TABLE bom_nodes ADD COLUMN category_id VARCHAR(64) "
                    "REFERENCES component_categories(id)"
                )
            )
            print("[ok] added bom_nodes.category_id")
        else:
            print("[skip] bom_nodes.category_id already present")

        if "spec" not in cols:
            # SQLite stores JSON as TEXT; default '{}' so existing rows stay valid.
            await conn.execute(
                text("ALTER TABLE bom_nodes ADD COLUMN spec TEXT DEFAULT '{}'")
            )
            print("[ok] added bom_nodes.spec (default '{}')")
        else:
            print("[skip] bom_nodes.spec already present")

    # 3) Confirm component_categories exists
    async with engine.begin() as conn:
        tables = await conn.run_sync(
            lambda sync_conn: set(inspect(sync_conn).get_table_names())
        )
        if "component_categories" in tables:
            print("[ok] component_categories table present")
        else:
            print("[err] component_categories table missing after create_all")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
