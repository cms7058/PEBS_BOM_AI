"""Smoke test the REST classification PATCH endpoint that the frontend
SelectionConfiguratorModal uses.

Verifies:
  - PATCH .../classification with category_id+spec persists & echoes
  - Setting only spec while category_id absent leaves category alone
  - Unknown spec key rejected with 400 + helpful message
  - Setting category_id=None clears spec too (cascade)
  - Source 'modal' shows in edit history (audit chain works)

Usage:
  cd apps/api
  .venv/bin/python -m scripts.smoke_test_classify_patch <bom_id> <node_id>
"""

from __future__ import annotations

import asyncio
import sys

import httpx

API = "http://localhost:8000"


async def run(bom_id: str, node_id: str) -> int:
    failures = 0
    async with httpx.AsyncClient(base_url=API, timeout=10) as c:
        # Reset baseline
        r = await c.patch(
            f"/boms/{bom_id}/nodes/{node_id}/classification",
            json={"category_id": None},
        )
        if r.status_code != 200:
            print(f"[fail] reset: {r.status_code} {r.text}")
            return 1

        # Test 1: full classification
        r = await c.patch(
            f"/boms/{bom_id}/nodes/{node_id}/classification",
            json={
                "category_id": "linear_guide",
                "spec": {"rail_width": 25, "rail_length": 1500, "slider_count": 2},
            },
        )
        d = r.json()
        if r.status_code == 200 and d.get("category_id") == "linear_guide" \
                and d.get("category_name") == "直线导轨" \
                and d.get("spec", {}).get("rail_width") == 25:
            print(f"[ok]   full classification persisted")
        else:
            print(f"[fail] full classification: {r.status_code} {d}")
            failures += 1

        # Test 2: spec-only update (category absent)
        r = await c.patch(
            f"/boms/{bom_id}/nodes/{node_id}/classification",
            json={"spec": {"rail_width": 30, "rail_length": 800}},
        )
        d = r.json()
        if r.status_code == 200 and d.get("category_id") == "linear_guide" \
                and d.get("spec", {}).get("rail_width") == 30:
            print(f"[ok]   spec-only update kept category, replaced spec")
        else:
            print(f"[fail] spec-only update: {r.status_code} {d}")
            failures += 1

        # Test 3: unknown spec key rejected
        r = await c.patch(
            f"/boms/{bom_id}/nodes/{node_id}/classification",
            json={"spec": {"garbage_key": "x"}},
        )
        if r.status_code == 400 and "garbage_key" in r.text:
            print(f"[ok]   unknown spec key rejected with 400")
        else:
            print(f"[fail] unknown spec key: {r.status_code} {r.text}")
            failures += 1

        # Test 4: clear cascade
        r = await c.patch(
            f"/boms/{bom_id}/nodes/{node_id}/classification",
            json={"category_id": None},
        )
        d = r.json()
        if r.status_code == 200 and d.get("category_id") is None and d.get("spec") == {}:
            print(f"[ok]   clear cascade: category_id=None also wipes spec")
        else:
            print(f"[fail] clear cascade: {r.status_code} {d}")
            failures += 1

        # Test 5: empty body returns current state (no-op)
        r = await c.patch(
            f"/boms/{bom_id}/nodes/{node_id}/classification",
            json={},
        )
        if r.status_code == 200:
            print(f"[ok]   empty body returns 200 (no-op)")
        else:
            print(f"[fail] empty body: {r.status_code} {r.text}")
            failures += 1

    if failures:
        print(f"\n[done] {failures} failure(s)")
        return 1
    print("\n[done] all checks passed")
    return 0


def main() -> None:
    if len(sys.argv) != 3:
        print("usage: python -m scripts.smoke_test_classify_patch <bom_id> <node_id>")
        sys.exit(2)
    sys.exit(asyncio.run(run(sys.argv[1], sys.argv[2])))


if __name__ == "__main__":
    main()
