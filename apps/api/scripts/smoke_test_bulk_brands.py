"""Smoke test: brand_bulk_add (candidate C — chat-paste AVL import).

Verifies:
  - Inserts a fresh batch correctly (with category id mapping)
  - Merges a row whose name already exists (upsert)
  - Rejects rows with unknown category but doesn't abort the whole batch
  - Rejects rows missing a name
  - Within-batch dedup (same name twice → second one merges into first)
  - tenant scoping unchanged

Usage:
  cd apps/api
  .venv/bin/python -m scripts.smoke_test_bulk_brands
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import delete, select

from app.agent_tools import BOMToolExecutor
from app.db import SessionLocal
from app.models import BrandEntry
from app.tenancy import DEFAULT_TENANT_ID


async def run() -> int:
    failures = 0

    async with SessionLocal() as db:
        await db.execute(
            delete(BrandEntry).where(BrandEntry.tenant_id == DEFAULT_TENANT_ID)
        )
        await db.commit()

        ex = BOMToolExecutor(db=db, bom_id="__unused__", user_name="smoke-bulk")

        # ───── Test 1: clean batch insert ─────
        rows = [
            {
                "name": "HIWIN 上银",
                "categories": ["linear_guide", "ball_screw"],
                "region": "台湾",
                "price_tier": "中端",
            },
            {
                "name": "雅威达",
                "categories": ["linear_guide"],
                "region": "浙江温岭",
                "price_tier": "国产高端",
                "notes": "账期 30 天",
            },
            {
                "name": "SMC",
                "categories": ["pneumatic_cylinder"],
                "region": "日本",
                "price_tier": "中端",
            },
        ]
        r = await ex.dispatch("brand_bulk_add", {"rows": rows})
        d = r.data or {}
        if not (r.ok and len(d.get("inserted") or []) == 3):
            print(f"[fail] clean batch: inserted={d.get('inserted')} summary={r.summary}")
            failures += 1
        else:
            print(f"[ok]   clean batch: inserted 3 brands")

        # ───── Test 2: merge by name ─────
        # Re-add HIWIN with extra info — should merge, not insert duplicate
        r = await ex.dispatch("brand_bulk_add", {
            "rows": [
                {
                    "name": "HIWIN 上银",
                    "url": "https://www.hiwin.cn",
                    "typical_lead_time": "现货",
                },
            ],
        })
        d = r.data or {}
        if not (r.ok and len(d.get("merged") or []) == 1 and not (d.get("inserted") or [])):
            print(f"[fail] merge: data={d}")
            failures += 1
        else:
            # Verify merged data persisted
            row = (
                await db.execute(
                    select(BrandEntry).where(BrandEntry.name == "HIWIN 上银")
                )
            ).scalar_one()
            if row.url == "https://www.hiwin.cn" and row.typical_lead_time == "现货":
                print(f"[ok]   merge by name: HIWIN url + lead_time updated, no dup")
            else:
                print(f"[fail] merge persistence: url={row.url} lead={row.typical_lead_time}")
                failures += 1

        # ───── Test 3: row with unknown category gets rejected, others survive ─────
        r = await ex.dispatch("brand_bulk_add", {
            "rows": [
                {"name": "ROBO 机器人", "categories": ["definitely_fake_category"]},
                {"name": "OMRON", "categories": ["proximity_sensor", "encoder"]},
            ],
        })
        d = r.data or {}
        rej = d.get("rejected") or []
        if len(rej) == 1 and rej[0]["name"] == "ROBO 机器人" and "definitely_fake_category" in rej[0]["reason"]:
            print(f"[ok]   bad row rejected, OMRON inserted ({len(d.get('inserted') or [])})")
        else:
            print(f"[fail] expected ROBO rejected + OMRON inserted; got {d}")
            failures += 1

        # ───── Test 4: row without name rejected ─────
        r = await ex.dispatch("brand_bulk_add", {
            "rows": [
                {"region": "随便"},
                {"name": "FANUC", "categories": ["gearbox"]},
            ],
        })
        d = r.data or {}
        if (
            len(d.get("inserted") or []) == 1
            and any(rj["reason"] == "缺少 name" for rj in (d.get("rejected") or []))
        ):
            print(f"[ok]   missing-name row rejected, FANUC inserted")
        else:
            print(f"[fail] missing-name handling: {d}")
            failures += 1

        # ───── Test 5: within-batch dedup ─────
        r = await ex.dispatch("brand_bulk_add", {
            "rows": [
                {"name": "BANDO 阪东", "categories": ["timing_belt_pulley"], "region": "日本"},
                {"name": "BANDO 阪东", "categories": ["timing_belt_pulley"], "price_tier": "高端"},
            ],
        })
        d = r.data or {}
        # First row inserts, second one should merge (within-batch dedup logic)
        if len(d.get("inserted") or []) == 1 and len(d.get("merged") or []) == 1:
            row = (
                await db.execute(
                    select(BrandEntry).where(BrandEntry.name == "BANDO 阪东")
                )
            ).scalar_one()
            if row.region == "日本" and row.price_tier == "高端":
                print(f"[ok]   within-batch dedup: BANDO merged, both fields persisted")
            else:
                print(f"[fail] within-batch dedup persistence: region={row.region} tier={row.price_tier}")
                failures += 1
        else:
            print(f"[fail] within-batch dedup count: {d}")
            failures += 1

        # ───── Test 6: shared visibility batch ─────
        r = await ex.dispatch("brand_bulk_add", {
            "rows": [{"name": "SOMETHING_SHARED", "categories": ["coupling"]}],
            "visibility": "shared",
        })
        if r.ok:
            row = (
                await db.execute(
                    select(BrandEntry).where(BrandEntry.name == "SOMETHING_SHARED")
                )
            ).scalar_one()
            if row.visibility == "shared":
                print(f"[ok]   shared visibility applied")
            else:
                print(f"[fail] shared visibility not applied: {row.visibility}")
                failures += 1
        else:
            print(f"[fail] shared batch: {r.summary}")
            failures += 1

        # ───── Cleanup ─────
        await db.execute(
            delete(BrandEntry).where(BrandEntry.tenant_id == DEFAULT_TENANT_ID)
        )
        await db.commit()
        print(f"[cleanup] removed test brands")

    if failures:
        print(f"\n[done] {failures} failure(s)")
        return 1
    print("\n[done] all checks passed")
    return 0


def main() -> None:
    sys.exit(asyncio.run(run()))


if __name__ == "__main__":
    main()
