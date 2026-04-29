"""End-to-end smoke test for brand_entries (step 3.5).

Verifies:
  - brand_add inserts + dedups by (tenant, name)
  - brand_add rejects unknown categories
  - brand_list filters by category and is tenant-scoped
  - brand_recommend orders by trust tier (private > shared > community)
  - brand_recommend returns fallback_brands when KB empty
  - brand_update modifies fields, brand_remove deletes
  - cross-tenant private rows are invisible

Usage:
  cd apps/api
  .venv/bin/python -m scripts.smoke_test_brands
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
        # Clean slate for the default tenant; this is dev-only data.
        await db.execute(
            delete(BrandEntry).where(BrandEntry.tenant_id == DEFAULT_TENANT_ID)
        )
        # Also clear a fake "tenant_b" we'll use for cross-tenant test
        await db.execute(delete(BrandEntry).where(BrandEntry.tenant_id == "tenant_b"))
        await db.commit()

        # NOTE: BOMToolExecutor takes a bom_id but the brand tools don't
        # actually touch a BOM. Pass a sentinel that's never queried.
        # We use dispatch() which would call _bom() only for BOM ops.
        ex = BOMToolExecutor(db=db, bom_id="__unused__", user_name="smoke-brand")

        # ───── Test 1: brand_add valid ─────
        r = await ex.dispatch("brand_add", {
            "name": "雅威达",
            "categories": ["linear_guide"],
            "region": "浙江温岭",
            "price_tier": "中端",
            "typical_lead_time": "现货",
            "notes": "账期 30 天",
        })
        if not r.ok or not r.data:
            print(f"[fail] brand_add valid: {r.summary}")
            failures += 1
            return failures
        yawd_id = r.data["id"]
        print(f"[ok]   brand_add: 雅威达 ({yawd_id[:8]})")

        # ───── Test 2: brand_add dedup (same name → merge) ─────
        r = await ex.dispatch("brand_add", {
            "name": "雅威达",
            "url": "https://www.amt-cn.com",
        })
        if not (r.ok and r.data and r.data.get("merged")):
            print(f"[fail] brand_add dedup: expected merged=True, got {r.summary}")
            failures += 1
        else:
            print(f"[ok]   brand_add dedup: merged into existing 雅威达")

        # ───── Test 3: brand_add reject unknown category ─────
        r = await ex.dispatch("brand_add", {
            "name": "某虚假品牌",
            "categories": ["definitely_not_a_real_category"],
        })
        if r.ok:
            print(f"[fail] brand_add should reject unknown category")
            failures += 1
        else:
            print(f"[ok]   brand_add rejected unknown category")

        # ───── Test 4: add a 2nd brand, list & filter ─────
        r = await ex.dispatch("brand_add", {
            "name": "HIWIN 上银",
            "categories": ["linear_guide", "ball_screw"],
            "region": "台湾",
            "price_tier": "中端",
        })
        hiwin_id = r.data["id"]

        r = await ex.dispatch("brand_list", {})
        all_brands = (r.data or {}).get("brands") or []
        if len(all_brands) != 2:
            print(f"[fail] brand_list: expected 2 brands, got {len(all_brands)}")
            failures += 1
        else:
            print(f"[ok]   brand_list: returned 2 private brands")

        r = await ex.dispatch("brand_list", {"category_id": "ball_screw"})
        bs_brands = (r.data or {}).get("brands") or []
        if len(bs_brands) != 1 or bs_brands[0]["name"] != "HIWIN 上银":
            print(f"[fail] brand_list filter ball_screw: {bs_brands}")
            failures += 1
        else:
            print(f"[ok]   brand_list filter ball_screw → only HIWIN")

        # ───── Test 5: cross-tenant isolation ─────
        # Insert a private brand on tenant_b — should NOT show in default's recommend
        from app.models import BrandEntry as BE
        from uuid import uuid4
        secret = BE(
            id=str(uuid4()),
            tenant_id="tenant_b",
            name="不该被看到",
            categories=["linear_guide"],
            visibility="private",
        )
        db.add(secret)
        await db.commit()

        r = await ex.dispatch("brand_recommend", {"category_id": "linear_guide"})
        recs = (r.data or {}).get("recommendations") or []
        names = [b["name"] for b in recs]
        if "不该被看到" in names:
            print(f"[fail] cross-tenant private brand leaked: {names}")
            failures += 1
        else:
            print(f"[ok]   cross-tenant isolation: tenant_b's private brand NOT visible")

        # ───── Test 6: shared brand from another tenant IS visible ─────
        public = BE(
            id=str(uuid4()),
            tenant_id="tenant_b",
            name="社区共享品牌",
            categories=["linear_guide"],
            visibility="shared",
        )
        db.add(public)
        await db.commit()

        r = await ex.dispatch("brand_recommend", {"category_id": "linear_guide"})
        recs = (r.data or {}).get("recommendations") or []
        community = [b for b in recs if b.get("trust") == "community"]
        if not community or community[0]["name"] != "社区共享品牌":
            print(f"[fail] community shared brand should be visible: {recs}")
            failures += 1
        else:
            print(f"[ok]   community shared brand visible (trust=community)")

        # ───── Test 7: trust ordering — private first ─────
        # default tenant's 雅威达 (private) should rank ABOVE community brand
        r = await ex.dispatch("brand_recommend", {"category_id": "linear_guide"})
        recs = (r.data or {}).get("recommendations") or []
        names_in_order = [b["name"] for b in recs]
        try:
            i_private = names_in_order.index("雅威达")
            i_community = names_in_order.index("社区共享品牌")
            if i_private < i_community:
                print(f"[ok]   trust ordering: private 雅威达 ranks above community share")
            else:
                print(f"[fail] trust ordering: private should be first, got {names_in_order}")
                failures += 1
        except ValueError:
            print(f"[fail] expected names not all present: {names_in_order}")
            failures += 1

        # ───── Test 8: empty-KB fallback returns common_brands ─────
        r = await ex.dispatch("brand_recommend", {"category_id": "dowel_pin"})
        data = r.data or {}
        if (data.get("recommendations") or []):
            print(f"[fail] dowel_pin should have no entries yet")
            failures += 1
        elif not (data.get("fallback_brands") or []):
            print(f"[fail] empty KB should return fallback_brands from category")
            failures += 1
        else:
            print(
                f"[ok]   empty KB fallback: {len(data['fallback_brands'])} "
                f"common brands returned for dowel_pin"
            )

        # ───── Test 9: brand_update ─────
        r = await ex.dispatch("brand_update", {
            "id": yawd_id,
            "price_tier": "高端",
            "notes": "账期 60 天 (升级)",
        })
        if not r.ok or not r.mutated:
            print(f"[fail] brand_update: {r.summary}")
            failures += 1
        else:
            entry = (
                await db.execute(
                    select(BrandEntry).where(BrandEntry.id == yawd_id)
                )
            ).scalar_one()
            if entry.price_tier == "高端" and "60 天" in (entry.notes or ""):
                print(f"[ok]   brand_update: 雅威达 → 高端")
            else:
                print(f"[fail] brand_update did not persist correctly")
                failures += 1

        # ───── Test 10: brand_update on another tenant's row is rejected ─────
        r = await ex.dispatch("brand_update", {
            "id": secret.id,
            "name": "试图改别人的",
        })
        if r.ok:
            print(f"[fail] should not be able to update other tenant's brand")
            failures += 1
        else:
            print(f"[ok]   brand_update rejected cross-tenant write")

        # ───── Test 11: brand_remove ─────
        r = await ex.dispatch("brand_remove", {"id": hiwin_id})
        if not (r.ok and r.mutated):
            print(f"[fail] brand_remove: {r.summary}")
            failures += 1
        else:
            r = await ex.dispatch("brand_list", {})
            after = (r.data or {}).get("brands") or []
            if any(b["id"] == hiwin_id for b in after):
                print(f"[fail] brand_remove did not actually delete")
                failures += 1
            else:
                print(f"[ok]   brand_remove: HIWIN gone from list")

        # ───── Cleanup ─────
        await db.execute(
            delete(BrandEntry).where(BrandEntry.tenant_id == DEFAULT_TENANT_ID)
        )
        await db.execute(delete(BrandEntry).where(BrandEntry.tenant_id == "tenant_b"))
        await db.commit()
        print(f"[cleanup] removed all test brands")

    if failures:
        print(f"\n[done] {failures} failure(s)")
        return 1
    print("\n[done] all checks passed")
    return 0


def main() -> None:
    sys.exit(asyncio.run(run()))


if __name__ == "__main__":
    main()
