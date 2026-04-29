"""Seed/refresh the component_categories table with the curated first batch.

Idempotent: re-running won't duplicate rows. By default we UPDATE existing
rows with the latest seed data so improvements to descriptions / parameter
schemas flow through. Pass --no-update to insert only.

Usage:
  cd apps/api
  .venv/bin/python -m scripts.seed_categories            # insert + update
  .venv/bin/python -m scripts.seed_categories --no-update  # insert only
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select

from app.db import SessionLocal
from app.models import ComponentCategory
from app.services.component_categories_seed import SEED_CATEGORIES


async def run(update_existing: bool = True) -> None:
    inserted = 0
    updated = 0
    skipped = 0

    async with SessionLocal() as db:
        for entry in SEED_CATEGORIES:
            existing = (
                await db.execute(
                    select(ComponentCategory).where(
                        ComponentCategory.id == entry["id"]
                    )
                )
            ).scalar_one_or_none()

            if existing is None:
                db.add(ComponentCategory(**entry))
                inserted += 1
                print(f"  + {entry['id']:24} ({entry['name_zh']})")
            elif update_existing:
                # Refresh editable fields; keep id and parent_id stable.
                for k in (
                    "name_zh", "name_en", "description", "parameters",
                    "common_brands", "typical_use", "related_gb", "sort_order",
                ):
                    if k in entry:
                        setattr(existing, k, entry[k])
                updated += 1
                print(f"  ~ {entry['id']:24} ({entry['name_zh']})")
            else:
                skipped += 1
                print(f"  · {entry['id']:24} (already present, skipped)")

        await db.commit()

    print(f"\n[done] inserted {inserted}, updated {updated}, skipped {skipped}")


def main() -> None:
    update = "--no-update" not in sys.argv
    asyncio.run(run(update_existing=update))


if __name__ == "__main__":
    main()
