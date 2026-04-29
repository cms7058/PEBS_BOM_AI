"""Idempotent migration: add 5 nullable MBOM-prep columns to bom_nodes.

These columns aren't used yet — they're scaffolding for the eventual
PBOM→MBOM feature (assembly operation sequencing, fixtures, standard
times, consumables-per-operation). Adding them now means future MBOM
features ship without disturbing production customer data.

Columns added (all NULL on existing rows, backwards compatible):
  · operation_seq        INTEGER  — operation sequence # (10/20/30…)
  · operation_desc       TEXT     — human description of the step
  · fixture_ref          VARCHAR  — jig/fixture id
  · consumed_by_op       INTEGER  — operation_seq this row is consumed by
  · standard_time_min    FLOAT    — rated cycle time in minutes

Usage:
  cd apps/api
  .venv/bin/python -m scripts.migrate_mbom_fields
"""

from __future__ import annotations

import asyncio

from sqlalchemy import inspect, text

from app.db import engine

# (column_name, ddl_type) — SQLite-friendly types matching the SQLAlchemy
# column definitions on BOMNode. Postgres would accept the same DDL.
_NEW_COLUMNS: list[tuple[str, str]] = [
    ("operation_seq",     "INTEGER"),
    ("operation_desc",    "TEXT"),
    ("fixture_ref",       "VARCHAR(128)"),
    ("consumed_by_op",    "INTEGER"),
    ("standard_time_min", "FLOAT"),
]


async def run() -> None:
    async with engine.begin() as conn:
        existing = await conn.run_sync(
            lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("bom_nodes")}
        )
        added = 0
        for col, ddl in _NEW_COLUMNS:
            if col in existing:
                print(f"[skip] bom_nodes.{col} already present")
                continue
            await conn.execute(
                text(f"ALTER TABLE bom_nodes ADD COLUMN {col} {ddl}")
            )
            print(f"[ok]   added bom_nodes.{col} ({ddl})")
            added += 1

    if added == 0:
        print("\n[done] all columns already present, nothing to do")
    else:
        print(f"\n[done] added {added} columns to bom_nodes")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
