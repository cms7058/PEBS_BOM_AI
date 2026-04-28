"""Endpoints for inspecting and re-applying the part-number hierarchy rule."""

from __future__ import annotations

from urllib.parse import unquote

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_db
from app.models.bom import BOM
from app.services.audit import label_of, record_edit
from app.services.part_number_hierarchy import apply_rule, detect_rule

router = APIRouter(prefix="/boms", tags=["hierarchy"])


class DetectOut(BaseModel):
    separator: str
    confidence: float
    sample_chains: list[list[str]]
    orphan_count: int
    candidate_separators: list[str] = ["-", ".", "_", "/", "·"]


class ApplyIn(BaseModel):
    separator: str


class ApplyOut(BaseModel):
    top_level: int
    linked: int
    orphans: int
    no_partnumber: int


@router.get("/{bom_id}/hierarchy", response_model=DetectOut)
async def get_hierarchy(bom_id: str, db: AsyncSession = Depends(get_db)) -> DetectOut:
    q = select(BOM).where(BOM.id == bom_id).options(selectinload(BOM.nodes))
    bom = (await db.execute(q)).scalar_one_or_none()
    if not bom:
        raise HTTPException(404, "BOM not found")
    rule = detect_rule(bom.nodes)
    return DetectOut(
        separator=rule.separator,
        confidence=rule.confidence,
        sample_chains=rule.sample_chains,
        orphan_count=rule.orphan_count,
    )


@router.post("/{bom_id}/hierarchy/apply", response_model=ApplyOut)
async def apply_hierarchy(
    bom_id: str,
    body: ApplyIn,
    db: AsyncSession = Depends(get_db),
    x_user_name: str | None = Header(default=None, alias="X-User-Name"),
) -> ApplyOut:
    q = select(BOM).where(BOM.id == bom_id).options(selectinload(BOM.nodes))
    bom = (await db.execute(q)).scalar_one_or_none()
    if not bom:
        raise HTTPException(404, "BOM not found")
    if not body.separator:
        raise HTTPException(400, "separator required")

    # Decode user name (frontend URL-encodes for non-ASCII).
    raw = x_user_name or ""
    try:
        decoded = unquote(raw)
    except Exception:
        decoded = raw
    user_name = decoded.strip()[:128] or "anonymous"

    # Snapshot parent_id/level before mutation so we can audit per-node changes.
    before = {n.id: (n.parent_id, n.level) for n in bom.nodes}
    summary = apply_rule(bom.nodes, body.separator)

    n_audited = 0
    for node in bom.nodes:
        old_parent, old_level = before.get(node.id, (None, None))
        if old_parent != node.parent_id:
            await record_edit(
                db,
                bom_id=bom.id,
                node_id=node.id,
                node_label=label_of(node),
                field="parent_id",
                old_value=old_parent,
                new_value=node.parent_id,
                user_name=user_name,
                source="hierarchy",
            )
            n_audited += 1
        if old_level != node.level:
            await record_edit(
                db,
                bom_id=bom.id,
                node_id=node.id,
                node_label=label_of(node),
                field="level",
                old_value=old_level,
                new_value=node.level,
                user_name=user_name,
                source="hierarchy",
            )
            n_audited += 1

    await db.commit()
    return ApplyOut(**summary)
