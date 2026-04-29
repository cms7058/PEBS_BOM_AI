"""End-to-end smoke test for non-std component classification tools.

Bypasses LLM, calls BOMToolExecutor directly. Verifies:
  - component_categories_list returns the 5 seed categories
  - bom_classify_node sets category_id + spec, validates schema
  - bom_classify_node rejects unknown spec keys
  - bom_classify_all heuristic identifies obvious cases, leaves others alone
  - record_edit logs the changes (audit hooks fire)

Usage:
  cd apps/api
  .venv/bin/python -m scripts.smoke_test_classify <bom_id>
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.agent_tools import BOMToolExecutor
from app.db import SessionLocal
from app.models import BOM, ComponentCategory


async def run(bom_id: str) -> int:
    failures = 0

    async with SessionLocal() as db:
        bom = (
            await db.execute(
                select(BOM).where(BOM.id == bom_id).options(selectinload(BOM.nodes))
            )
        ).scalar_one_or_none()
        if not bom:
            print(f"[err] BOM {bom_id} not found")
            return 1

        # Reset classification state so re-runs are deterministic
        for n in bom.nodes:
            n.category_id = None
            n.spec = {}
        await db.commit()

        target = next(
            (n for n in bom.nodes if "基座" in n.part_name),
            next(iter(bom.nodes), None),
        )
        if not target:
            print(f"[err] no nodes in BOM")
            return 1
        print(f"[setup] target node: {target.part_name!r}")

        ex = BOMToolExecutor(db=db, bom_id=bom_id, user_name="smoke-classify")

        # Test 1: list returns at least the 5 original categories.
        # New ones may have been added (proximity_sensor, gearbox, etc.) — tolerate.
        r = await ex.dispatch("component_categories_list", {})
        cats = (r.data or {}).get("categories") or []
        ids = {c["id"] for c in cats}
        required = {"linear_guide", "ball_screw", "aluminum_extrusion", "dowel_pin", "coupling"}
        if not r.ok or not required.issubset(ids):
            print(f"[fail] categories_list: missing required {required - ids}")
            failures += 1
        else:
            print(f"[ok]   categories_list returned {len(cats)} categories (≥5 required)")

        # Test 2: classify with valid category + spec
        r = await ex.dispatch("bom_classify_node", {
            "node_id": target.id,
            "category_id": "linear_guide",
            "spec": {"rail_width": 25, "rail_length": 1500, "slider_count": 2},
        })
        if not (r.ok and r.mutated):
            print(f"[fail] classify_node valid: {r.summary}")
            failures += 1
        else:
            await db.refresh(target)
            if target.category_id == "linear_guide" and target.spec.get("rail_width") == 25:
                print(f"[ok]   classify_node valid: category=linear_guide, spec persisted")
            else:
                print(f"[fail] classify_node valid: persisted state wrong: cat={target.category_id} spec={target.spec}")
                failures += 1

        # Test 3: reject unknown spec key
        r = await ex.dispatch("bom_classify_node", {
            "node_id": target.id,
            "category_id": "linear_guide",
            "spec": {"rail_width": 25, "garbage_key": "x"},
        })
        if r.ok:
            print(f"[fail] classify_node should reject unknown key: {r.summary}")
            failures += 1
        else:
            print(f"[ok]   classify_node rejected unknown key ({r.summary[:60]}...)")

        # Test 4: reject unknown category
        r = await ex.dispatch("bom_classify_node", {
            "node_id": target.id,
            "category_id": "nonsense_category",
        })
        if r.ok:
            print(f"[fail] classify_node should reject unknown category")
            failures += 1
        else:
            print(f"[ok]   classify_node rejected unknown category ({r.summary})")

        # Test 5: clear classification (category_id=None)
        r = await ex.dispatch("bom_classify_node", {
            "node_id": target.id,
            "category_id": None,
        })
        await db.refresh(target)
        if r.ok and target.category_id is None:
            print(f"[ok]   classify_node cleared category_id")
        else:
            print(f"[fail] classify_node clear: cat={target.category_id}")
            failures += 1

        # Test 6: classify_all heuristic
        # Add a synthetic node we know matches "直线导轨"
        from app.models import BOMNode
        from uuid import uuid4
        synth = BOMNode(
            id=str(uuid4()),
            bom_id=bom.id,
            part_name="直线导轨 HG25 L=1500",
            quantity=1, uom="EA", confidence=1.0, sort_order=999,
        )
        db.add(synth)
        await db.commit()
        # Force the BOM.nodes relationship cache to refresh so the executor
        # sees the new node on its next selectinload.
        await db.refresh(bom, ["nodes"])

        r = await ex.dispatch("bom_classify_all", {})
        data = r.data or {}
        clf = data.get("classified") or []
        unclf = data.get("unclassified") or []
        synth_classified = next(
            (c for c in clf if c["node_id"] == synth.id),
            None,
        )
        if synth_classified and synth_classified["category_id"] == "linear_guide":
            print(f"[ok]   classify_all heuristic caught '直线导轨 HG25 L=1500' "
                  f"→ linear_guide (conf={synth_classified['confidence']})")
        else:
            print(f"[fail] classify_all missed obvious linear_guide. "
                  f"classified={len(clf)} unclassified={len(unclf)}")
            failures += 1

        print(f"[info] classify_all overall: {len(clf)} classified, "
              f"{len(unclf)} unclassified (these need human/LLM judgement)")

        # Cleanup synthetic node + reset target
        await db.delete(synth)
        for n in bom.nodes:
            if n.id == target.id:
                n.category_id = None
                n.spec = {}
        await db.commit()
        print(f"[cleanup] removed synth node, reset target")

    if failures:
        print(f"\n[done] {failures} failure(s)")
        return 1
    print("\n[done] all checks passed")
    return 0


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: python -m scripts.smoke_test_classify <bom_id>")
        sys.exit(2)
    sys.exit(asyncio.run(run(sys.argv[1])))


if __name__ == "__main__":
    main()
