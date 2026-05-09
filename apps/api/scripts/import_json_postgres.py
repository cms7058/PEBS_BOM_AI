from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy import JSON, insert
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.models import Base

TABLE_ORDER = [
    "uploaded_files",
    "subscription_plans",
    "tenants",
    "app_users",
    "email_verification_codes",
    "feature_flags",
    "payment_orders",
    "component_categories",
    "brand_entries",
    "boms",
    "parts",
    "bom_nodes",
    "part_aliases",
    "part_import_drafts",
    "bom_node_edits",
]


def _coerce_row(table_name: str, row: dict) -> dict:
    table = Base.metadata.tables[table_name]
    coerced = {}
    for key, value in row.items():
        if key not in table.c:
            continue
        column = table.c[key]
        if isinstance(column.type, JSON) and isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                pass
        coerced[key] = value
    return coerced


async def import_json(input_path: Path, database_url: str) -> None:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    tables: dict[str, list[dict]] = payload.get("tables", {})
    engine = create_async_engine(database_url, pool_pre_ping=True)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for table_name in TABLE_ORDER:
            rows = tables.get(table_name, [])
            if not rows or table_name not in Base.metadata.tables:
                continue
            table = Base.metadata.tables[table_name]
            values = [_coerce_row(table_name, row) for row in rows]
            await conn.execute(insert(table), values)

    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Import PEBS BOM JSON export into PostgreSQL.")
    parser.add_argument("--in", dest="input", default="data/sqlite-export.json", help="Input JSON path")
    parser.add_argument(
        "--database-url",
        default=settings.database_url,
        help="Target database URL, defaults to DATABASE_URL",
    )
    args = parser.parse_args()

    asyncio.run(import_json(Path(args.input), args.database_url))
    print(f"Imported {args.input} into target database")


if __name__ == "__main__":
    main()
