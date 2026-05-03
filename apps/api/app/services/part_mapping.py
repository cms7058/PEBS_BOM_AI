from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bom import BOMNode, Part, PartAlias
from app.tenancy import current_tenant


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", "", value).lower()


def node_mapping_text(node: BOMNode) -> str:
    return " ".join(
        str(v)
        for v in (
            node.part_number,
            node.part_name,
            node.description,
            node.material,
            node.supplier,
            node.notes,
        )
        if v
    )


def score_part_for_node(node: BOMNode, part: Part, aliases: list[PartAlias]) -> tuple[float, str]:
    node_number = normalize_text(node.part_number)
    node_name = normalize_text(node.part_name)
    haystack = normalize_text(node_mapping_text(node))

    best = 0.0
    reasons: list[str] = []
    if node_number and normalize_text(part.part_number) == node_number:
        best = max(best, 0.96)
        reasons.append("零件号精确匹配")
    if node_name and normalize_text(part.name_standard) == node_name:
        best = max(best, 0.92)
        reasons.append("标准名称精确匹配")
    if part.part_number and normalize_text(part.part_number) in haystack:
        best = max(best, 0.86)
        reasons.append("节点文本包含标准零件号")
    if part.name_standard and normalize_text(part.name_standard) in haystack:
        best = max(best, 0.78)
        reasons.append("节点文本包含标准名称")

    for alias in aliases:
        raw_name = normalize_text(alias.raw_name)
        raw_number = normalize_text(alias.raw_part_number)
        if node_number and raw_number and raw_number == node_number:
            best = max(best, 0.94)
            reasons.append(f"历史别名零件号匹配：{alias.raw_part_number}")
        if node_name and raw_name and raw_name == node_name:
            best = max(best, 0.9)
            reasons.append(f"历史别名名称匹配：{alias.raw_name}")
        if raw_name and raw_name in haystack:
            best = max(best, 0.74)
            reasons.append(f"包含历史别名：{alias.raw_name}")

    return best, "；".join(reasons[:2]) or "文本相似"


async def suggest_parts_for_node(
    db: AsyncSession,
    node: BOMNode,
    *,
    tenant_id: str | None = None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    tenant = tenant_id or current_tenant()
    aliases = (
        await db.execute(select(PartAlias).where(PartAlias.tenant_id == tenant))
    ).scalars().all()
    aliases_by_part: dict[str, list[PartAlias]] = {}
    for alias in aliases:
        aliases_by_part.setdefault(alias.part_id, []).append(alias)

    parts = (
        await db.execute(select(Part).where(Part.tenant_id == tenant))
    ).scalars().all()
    scored: list[dict[str, Any]] = []
    for part in parts:
        score, reason = score_part_for_node(node, part, aliases_by_part.get(part.id, []))
        if score <= 0:
            continue
        scored.append({"part": part, "score": round(score, 3), "reason": reason})
    scored.sort(key=lambda item: item["score"], reverse=True)
    return scored[:limit]


async def add_alias_for_node(
    db: AsyncSession,
    *,
    part: Part,
    node: BOMNode,
    user_name: str,
    confidence: float = 1.0,
) -> PartAlias:
    tenant = part.tenant_id
    raw_name = (node.part_name or "").strip()
    raw_number = (node.part_number or "").strip() if node.part_number else None
    q = select(PartAlias).where(
        PartAlias.tenant_id == tenant,
        PartAlias.part_id == part.id,
        PartAlias.raw_name == raw_name,
    )
    if raw_number:
        q = q.where(
            or_(PartAlias.raw_part_number == raw_number, PartAlias.raw_part_number.is_(None))
        )
    existing = (await db.execute(q)).scalars().first()
    if existing:
        existing.status = "confirmed"
        existing.confidence = max(existing.confidence or 0, confidence)
        existing.confirmed_by = user_name
        existing.confirmed_at = datetime.utcnow()
        return existing
    alias = PartAlias(
        tenant_id=tenant,
        part_id=part.id,
        raw_name=raw_name,
        raw_part_number=raw_number,
        source_node_id=node.id,
        status="confirmed",
        confidence=confidence,
        confirmed_by=user_name,
        confirmed_at=datetime.utcnow(),
    )
    db.add(alias)
    return alias


def make_part_from_node(node: BOMNode, *, user_name: str, tenant_id: str | None = None) -> Part:
    return Part(
        tenant_id=tenant_id or current_tenant(),
        sku_internal=node.part_number,
        name_standard=node.part_name,
        part_number=node.part_number,
        category_id=node.category_id,
        brand=node.supplier,
        spec=dict(node.spec or {}),
        notes=node.notes,
        created_by=user_name,
    )
