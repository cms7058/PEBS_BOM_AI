"""Brand knowledge base — REST endpoints for the frontend to query without
going through the chat agent.

The agent has its own brand_* tools for chat-driven CRUD; these endpoints
let the BOM workspace UI (e.g. SelectionContextCard on node click) pull
recommendations directly without paying the LLM round-trip latency.

All endpoints are tenant-scoped via app.tenancy.current_tenant().
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import BrandEntry, ComponentCategory
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
