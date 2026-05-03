"""Component categories — non-tenant taxonomy of non-std mechanical
components. Seeded by scripts and extendable from the material library.

The agent has component_categories_list as a chat tool; this REST endpoint
is for the SelectionConfiguratorModal which needs the full schema (parameter
definitions, common_brands) up-front to render the picker + form.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import ComponentCategory
from app.schemas import ComponentCategoryCreate

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


def _slug_from_name(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", name.strip().lower()).strip("_")
    if not slug:
        slug = f"custom_{uuid.uuid4().hex[:8]}"
    return slug[:48]


@router.get("")
async def list_categories(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    rows = (
        await db.execute(
            select(ComponentCategory).order_by(ComponentCategory.sort_order)
        )
    ).scalars().all()
    return {"categories": [_to_dict(c) for c in rows]}


@router.post("", status_code=201)
async def create_category(
    body: ComponentCategoryCreate,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    name_zh = body.name_zh.strip()
    if not name_zh:
        raise HTTPException(status_code=400, detail="类目名称不能为空")

    if body.parent_id:
        parent = await db.get(ComponentCategory, body.parent_id)
        if not parent:
            raise HTTPException(status_code=400, detail="父级类目不存在")

    existing = (
        await db.execute(
            select(ComponentCategory).where(ComponentCategory.name_zh == name_zh)
        )
    ).scalar_one_or_none()
    if existing:
        return _to_dict(existing)

    base = _slug_from_name(body.name_en or name_zh)
    category_id = base
    suffix = 2
    while await db.get(ComponentCategory, category_id):
        category_id = f"{base[:55]}_{suffix}"
        suffix += 1

    max_sort = await db.scalar(select(func.max(ComponentCategory.sort_order)))
    cat = ComponentCategory(
        id=category_id,
        parent_id=body.parent_id,
        name_zh=name_zh,
        name_en=(body.name_en or category_id).strip() or category_id,
        description=(body.description or "").strip() or None,
        parameters=[],
        common_brands=[],
        sort_order=(max_sort or 0) + 10,
    )
    db.add(cat)
    await db.commit()
    await db.refresh(cat)
    return _to_dict(cat)
