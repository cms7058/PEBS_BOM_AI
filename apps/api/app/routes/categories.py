"""Component categories — non-tenant taxonomy of non-std mechanical
components. Read-only from the frontend (seeded via scripts).

The agent has component_categories_list as a chat tool; this REST endpoint
is for the SelectionConfiguratorModal which needs the full schema (parameter
definitions, common_brands) up-front to render the picker + form.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import ComponentCategory

router = APIRouter(prefix="/component-categories", tags=["component-categories"])


def _to_dict(c: ComponentCategory) -> dict[str, Any]:
    return {
        "id": c.id,
        "parent_id": c.parent_id,
        "name_zh": c.name_zh,
        "name_en": c.name_en,
        "description": c.description,
        "parameters": c.parameters or [],
        "common_brands": c.common_brands or [],
        "typical_use": c.typical_use,
        "related_gb": c.related_gb,
        "sort_order": c.sort_order,
    }


@router.get("")
async def list_categories(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    rows = (
        await db.execute(
            select(ComponentCategory).order_by(ComponentCategory.sort_order)
        )
    ).scalars().all()
    return {"categories": [_to_dict(c) for c in rows]}
