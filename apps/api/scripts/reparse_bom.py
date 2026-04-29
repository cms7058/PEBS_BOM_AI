"""Re-parse a BOM's source CAD file and fix level/parent_id in place.

Why this exists:
  Earlier versions of step_parser.py mis-tokenized NAUO entities exported by
  some CAD packages (e.g. SolidWorks 2013 uses space-padded args:
  `NEXT_ASSEMBLY_USAGE_OCCURRENCE ( 'NAUO1', ' ', ' ', #2811, #1756, $ )`).
  As a result, BOMs uploaded before that fix have all nodes at level=0 with
  parent_id=NULL — a flat list instead of an assembly tree.

  Re-uploading would create a new BOM and lose any agent-driven edits made on
  the existing one. This script re-runs the (current, fixed) parser on the
  original file, matches new nodes to existing nodes by source_ref.ref (the
  STEP PD ref is stable), and updates parent_id + level + sort_order.

Usage:
  cd apps/api
  .venv/bin/python -m scripts.reparse_bom <bom_id>
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import SessionLocal
from app.models.bom import BOM, BOMNode, UploadedFile
from app.services.iges_parser import iges_nodes_to_dicts, parse_iges
from app.services.step_parser import parse_step, step_nodes_to_dicts
from app.services.storage import store


async def reparse(bom_id: str) -> None:
    async with SessionLocal() as db:  # type: AsyncSession
        bom = (
            await db.execute(
                select(BOM)
                .where(BOM.id == bom_id)
                .options(selectinload(BOM.nodes))
            )
        ).scalar_one_or_none()
        if not bom:
            print(f"[err] BOM {bom_id} not found")
            sys.exit(1)
        if not bom.source_file_id:
            print(f"[err] BOM {bom_id} has no source_file_id; can't reparse")
            sys.exit(1)

        uploaded = (
            await db.execute(
                select(UploadedFile).where(UploadedFile.id == bom.source_file_id)
            )
        ).scalar_one_or_none()
        if not uploaded:
            print(f"[err] UploadedFile {bom.source_file_id} not found")
            sys.exit(1)

        suffix = (uploaded.filename or "").lower().rsplit(".", 1)[-1]
        is_iges = suffix in {"iges", "igs"}
        is_step = suffix in {"step", "stp", "stpz"}
        if not (is_iges or is_step):
            print(f"[err] Source file '{uploaded.filename}' is not a CAD file")
            sys.exit(1)

        # Read raw bytes back from storage.
        try:
            data = store.get(uploaded.object_key)
        except Exception as exc:  # noqa: BLE001
            print(f"[err] Could not read source blob {uploaded.object_key}: {exc}")
            sys.exit(1)

        if is_iges:
            cad_nodes = parse_iges(data)
            new_dicts = iges_nodes_to_dicts(cad_nodes)
        else:
            cad_nodes = parse_step(data)
            new_dicts = step_nodes_to_dicts(cad_nodes)

        if not new_dicts:
            print("[err] reparse produced 0 nodes")
            sys.exit(1)

        # Match strategy: source_ref.ref (the original STEP PD ref) is stable
        # across reparses. Build ref → existing-node-id map.
        ref_to_existing: dict[int, BOMNode] = {}
        for node in bom.nodes:
            sref = node.source_ref or {}
            if sref.get("type") in ("step_pd", "iges_subfigure") and isinstance(
                sref.get("ref"), int
            ):
                ref_to_existing[sref["ref"]] = node

        # First pass: collect new nodes by their original index into new_dicts;
        # also map new index → existing BOMNode (if matched by ref).
        new_idx_to_existing: dict[int, BOMNode] = {}
        for i, nd in enumerate(new_dicts):
            ref = (nd.get("source_ref") or {}).get("ref")
            if isinstance(ref, int) and ref in ref_to_existing:
                new_idx_to_existing[i] = ref_to_existing[ref]

        if len(new_idx_to_existing) != len(new_dicts):
            print(
                f"[warn] reparse produced {len(new_dicts)} nodes but only "
                f"{len(new_idx_to_existing)} matched existing rows by ref. "
                "Unmatched nodes will be skipped (not inserted)."
            )

        # Second pass: update level + sort_order + parent_id.
        updated = 0
        for i, nd in enumerate(new_dicts):
            existing = new_idx_to_existing.get(i)
            if not existing:
                continue
            old = (existing.level, existing.parent_id, existing.sort_order)
            existing.level = nd["level"]
            existing.sort_order = nd["sort_order"]
            pidx = nd.get("_parent_index")
            new_parent_node = (
                new_idx_to_existing.get(pidx) if pidx is not None else None
            )
            existing.parent_id = new_parent_node.id if new_parent_node else None
            new = (existing.level, existing.parent_id, existing.sort_order)
            if old != new:
                updated += 1
                print(
                    f"  ✓ {existing.part_name}: L{old[0]}→L{new[0]}, "
                    f"parent={(old[1] or '-')[:8]}→{(new[1] or '-')[:8]}"
                )

        await db.commit()
        print(f"\n[done] updated {updated}/{len(new_dicts)} nodes for BOM {bom_id}")


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: python -m scripts.reparse_bom <bom_id>")
        sys.exit(2)
    asyncio.run(reparse(sys.argv[1]))


if __name__ == "__main__":
    main()
