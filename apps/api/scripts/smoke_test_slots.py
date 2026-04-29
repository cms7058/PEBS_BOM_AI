"""End-to-end smoke test for the per-slot agent tools.

Bypasses the LLM layer — calls BOMToolExecutor directly so we can verify
   bom_describe_node / bom_set_slot / bom_set_slot_by_rule
end-to-end (DB write → reload → frontend-visible style.slots shape) without
waiting on the model and without spending tokens.

Usage:
  cd apps/api
  .venv/bin/python -m scripts.smoke_test_slots <bom_id>
"""

from __future__ import annotations

import asyncio
import json
import sys

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.agent_tools import BOMToolExecutor
from app.db import SessionLocal
from app.models.bom import BOM


async def run(bom_id: str) -> int:
    failures = 0

    async with SessionLocal() as db:
        bom = (
            await db.execute(
                select(BOM)
                .where(BOM.id == bom_id)
                .options(selectinload(BOM.nodes))
            )
        ).scalar_one_or_none()
        if not bom:
            print(f"[err] BOM {bom_id} not found")
            return 1

        # Pick the first L1 node as the test target ('基座' for the user's BOM).
        target = next(
            (n for n in sorted(bom.nodes, key=lambda x: x.sort_order) if n.level == 1),
            None,
        )
        if not target:
            print(f"[err] no L1 node found in BOM {bom_id}")
            return 1
        print(f"[setup] target node: {target.part_name!r} id={target.id}")
        original_style = dict(target.style or {})

        # Make the test idempotent regardless of any pre-existing slot state
        # (e.g. residue from real agent usage in the browser).
        for n in bom.nodes:
            n.style = {}
        await db.commit()

        ex = BOMToolExecutor(db=db, bom_id=bom_id, user_name="smoke")

        # ───── Test 1: bom_describe_node returns 8 slots + bound_fields ─────
        r = await ex.dispatch("bom_describe_node", {"node_id": target.id})
        if not r.ok:
            print(f"[fail] describe_node: {r.summary}")
            failures += 1
        else:
            slots = (r.data or {}).get("slots") or []
            slot_ids = [s["id"] for s in slots]
            expected = {"header", "title", "qty", "metric", "trend", "progress", "badge", "card"}
            if set(slot_ids) != expected:
                print(f"[fail] describe_node: slot ids = {slot_ids}, expected {expected}")
                failures += 1
            else:
                print(f"[ok]   describe_node returned {len(slots)} slots + "
                      f"{len((r.data or {}).get('bound_fields') or [])} bound fields")

        # ───── Test 2: bom_set_slot — change progress color to red ─────
        r = await ex.dispatch("bom_set_slot", {
            "node_id": target.id,
            "slot": "progress",
            "attrs": {"color": "#F46649"},
        })
        if not (r.ok and r.mutated):
            print(f"[fail] set_slot progress.color: {r.summary}")
            failures += 1
        else:
            await db.refresh(target)
            color = (target.style or {}).get("slots", {}).get("progress", {}).get("color")
            if color != "#F46649":
                print(f"[fail] progress.color persisted as {color!r}, expected '#F46649'")
                failures += 1
            else:
                print(f"[ok]   set_slot progress.color → #F46649 persisted")

        # ───── Test 3: bom_set_slot — bind metric to supplier ─────
        r = await ex.dispatch("bom_set_slot", {
            "node_id": target.id,
            "slot": "metric",
            "attrs": {"bound": "supplier"},
        })
        if not (r.ok and r.mutated):
            print(f"[fail] set_slot metric.bound: {r.summary}")
            failures += 1
        else:
            await db.refresh(target)
            bound = (target.style or {}).get("slots", {}).get("metric", {}).get("bound")
            if bound != "supplier":
                print(f"[fail] metric.bound persisted as {bound!r}, expected 'supplier'")
                failures += 1
            else:
                print(f"[ok]   set_slot metric.bound → supplier persisted")

        # ───── Test 4: bom_set_slot — text override on badge ─────
        r = await ex.dispatch("bom_set_slot", {
            "node_id": target.id,
            "slot": "badge",
            "attrs": {"text": "测试", "color": "#1783FF"},
        })
        if not (r.ok and r.mutated):
            print(f"[fail] set_slot badge: {r.summary}")
            failures += 1
        else:
            await db.refresh(target)
            badge = (target.style or {}).get("slots", {}).get("badge", {})
            if badge.get("text") != "测试" or badge.get("color") != "#1783FF":
                print(f"[fail] badge persisted wrong: {badge!r}")
                failures += 1
            else:
                print(f"[ok]   set_slot badge.text='测试' badge.color='#1783FF' persisted")

        # ───── Test 5: clear via null — remove progress.color ─────
        r = await ex.dispatch("bom_set_slot", {
            "node_id": target.id,
            "slot": "progress",
            "attrs": {"color": None},
        })
        if not (r.ok and r.mutated):
            print(f"[fail] clear progress.color: {r.summary}")
            failures += 1
        else:
            await db.refresh(target)
            slots_after = (target.style or {}).get("slots", {})
            if "progress" in slots_after and "color" in slots_after.get("progress", {}):
                print(f"[fail] progress.color not cleared: {slots_after}")
                failures += 1
            else:
                print(f"[ok]   set_slot progress.color → null cleared")

        # ───── Test 6: by_rule — set badge='L1' on all level=1 nodes ─────
        r = await ex.dispatch("bom_set_slot_by_rule", {
            "filter": {"level": 1},
            "slot": "badge",
            "attrs": {"text": "L1", "color": "#60C42D"},
        })
        if not r.ok:
            print(f"[fail] set_slot_by_rule: {r.summary}")
            failures += 1
        else:
            await db.refresh(bom)
            l1_nodes = [n for n in bom.nodes if n.level == 1]
            with_badge = [
                n for n in l1_nodes
                if (n.style or {}).get("slots", {}).get("badge", {}).get("text") == "L1"
            ]
            print(f"[ok]   set_slot_by_rule applied to {len(with_badge)}/{len(l1_nodes)} L1 nodes")
            if len(with_badge) != len(l1_nodes):
                failures += 1

        # ───── Test 7: invalid slot is rejected ─────
        r = await ex.dispatch("bom_set_slot", {
            "node_id": target.id,
            "slot": "nonsense",
            "attrs": {"color": "red"},
        })
        if r.ok:
            print(f"[fail] invalid slot accepted: {r.summary}")
            failures += 1
        else:
            print(f"[ok]   invalid slot rejected ({r.summary})")

        # ───── Cleanup: restore original style on test target ─────
        target.style = original_style
        l1_nodes = [n for n in bom.nodes if n.level == 1]
        for n in l1_nodes:
            s = dict(n.style or {})
            slots = dict(s.get("slots") or {})
            slots.pop("badge", None)
            if slots:
                s["slots"] = slots
            else:
                s.pop("slots", None)
            n.style = s
        await db.commit()
        print(f"[cleanup] restored {target.part_name!r} + cleared L1 badges")

    if failures:
        print(f"\n[done] {failures} failure(s)")
        return 1
    print("\n[done] all checks passed")
    return 0


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: python -m scripts.smoke_test_slots <bom_id>")
        sys.exit(2)
    sys.exit(asyncio.run(run(sys.argv[1])))


if __name__ == "__main__":
    main()
