"""Brand knowledge base — REST endpoints for the frontend to query without
going through the chat agent.

The agent has its own brand_* tools for chat-driven CRUD; these endpoints
let the BOM workspace UI (e.g. SelectionContextCard on node click) pull
recommendations directly without paying the LLM round-trip latency.

All endpoints are tenant-scoped via app.tenancy.current_tenant().
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import BrandEntry, ComponentCategory
from app.schemas import BrandCreate
from app.tenancy import current_tenant

router = APIRouter(prefix="/brands", tags=["brands"])


def _to_dict(b: BrandEntry, trust: str) -> dict[str, Any]:
    return {
        "id": b.id,
        "name": b.name,
        "url": b.url,
        "region": b.region,
        "categories": b.categories or [],
        "price_tier": b.price_tier,
        "typical_lead_time": b.typical_lead_time,
        "notes": b.notes,
        "visibility": b.visibility,
        "upvotes": b.upvotes,
        "trust": trust,
    }


@router.get("")
async def list_brands(
    q: str | None = None,
    limit: int = 500,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    tenant = current_tenant()
    stmt = select(BrandEntry).where(
        (BrandEntry.tenant_id == tenant) | (BrandEntry.visibility == "shared")
    )
    needle = (q or "").strip()
    if needle:
        like = f"%{needle}%"
        stmt = stmt.where(
            or_(
                BrandEntry.name.ilike(like),
                BrandEntry.region.ilike(like),
                BrandEntry.notes.ilike(like),
            )
        )
    rows = (
        await db.execute(
            stmt.order_by(BrandEntry.name).limit(max(1, min(limit, 1000)))
        )
    ).scalars().all()

    brands = []
    for b in rows:
        trust = "private" if b.tenant_id == tenant and b.visibility == "private" else (
            "shared-by-you" if b.tenant_id == tenant else "community"
        )
        brands.append(_to_dict(b, trust))
    return {"brands": brands, "total": len(brands)}


@router.post("", status_code=201)
async def create_brand(
    body: BrandCreate,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    tenant = current_tenant()
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="品牌名称不能为空")

    categories: list[str] = []
    for category_id in body.categories:
        category_id = category_id.strip()
        if not category_id:
            continue
        if not await db.get(ComponentCategory, category_id):
            raise HTTPException(status_code=400, detail=f"类目不存在：{category_id}")
        if category_id not in categories:
            categories.append(category_id)

    existing = (
        await db.execute(
            select(BrandEntry).where(
                BrandEntry.tenant_id == tenant,
                BrandEntry.name == name,
            )
        )
    ).scalar_one_or_none()
    if existing:
        for category_id in categories:
            if category_id not in (existing.categories or []):
                existing.categories = [*(existing.categories or []), category_id]
        await db.commit()
        await db.refresh(existing)
        return _to_dict(existing, "private" if existing.visibility == "private" else "shared-by-you")

    entry = BrandEntry(
        tenant_id=tenant,
        name=name,
        categories=categories,
        region=(body.region or "").strip() or None,
        notes=(body.notes or "").strip() or None,
        source="ui",
        visibility="private",
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return _to_dict(entry, "private")


@router.get("/recommend")
async def recommend(
    category_id: str,
    region: str | None = None,
    price_tier: str | None = None,
    limit: int = 8,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Recommend brands for a category, ordered by trust tier.

    Mirrors the bom_recommend agent tool's logic exactly — same buckets,
    same fallback. Frontend uses this on node click.
    """
    tenant = current_tenant()

    rows = (
        await db.execute(
            select(BrandEntry).where(
                (BrandEntry.tenant_id == tenant) | (BrandEntry.visibility == "shared")
            )
        )
    ).scalars().all()

    def matches(b: BrandEntry) -> bool:
        if category_id not in (b.categories or []):
            return False
        if region and (b.region or "") != region:
            return False
        if price_tier and (b.price_tier or "") != price_tier:
            return False
        return not b.flagged

    filtered = [b for b in rows if matches(b)]

    own_private = [b for b in filtered if b.tenant_id == tenant and b.visibility == "private"]
    own_shared  = [b for b in filtered if b.tenant_id == tenant and b.visibility == "shared"]
    community   = [b for b in filtered if b.tenant_id != tenant and b.visibility == "shared"]

    for bucket in (own_private, own_shared, community):
        bucket.sort(key=lambda b: (-b.upvotes, b.name))

    ranked: list[dict] = []
    for b in own_private[:limit]:
        ranked.append(_to_dict(b, "private"))
    for b in own_shared[: max(0, limit - len(ranked))]:
        ranked.append(_to_dict(b, "shared-by-you"))
    for b in community[: max(0, limit - len(ranked))]:
        ranked.append(_to_dict(b, "community"))

    cat = (
        await db.execute(
            select(ComponentCategory).where(ComponentCategory.id == category_id)
        )
    ).scalar_one_or_none()

    return {
        "category_id": category_id,
        "category_name": cat.name_zh if cat else category_id,
        "recommendations": ranked,
        "fallback_brands": (cat.common_brands if cat else []) or [],
    }
