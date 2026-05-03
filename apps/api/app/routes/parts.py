from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.bom import ComponentCategory, Part
from app.schemas import PartListOut, PartOut, PartPatch
from app.tenancy import current_tenant

router = APIRouter(prefix="/parts", tags=["parts"])


@router.get("", response_model=PartListOut)
async def list_parts(
    q: str | None = None,
    limit: int = 200,
    db: AsyncSession = Depends(get_db),
) -> PartListOut:
    tenant_id = current_tenant()
    stmt = select(Part).where(Part.tenant_id == tenant_id)
    needle = (q or "").strip()
    if needle:
        like = f"%{needle}%"
        stmt = stmt.where(
            or_(
                Part.name_standard.ilike(like),
                Part.part_number.ilike(like),
                Part.sku_internal.ilike(like),
                Part.brand.ilike(like),
            )
        )
    stmt = stmt.order_by(Part.updated_at.desc(), Part.created_at.desc()).limit(max(1, min(limit, 1000)))
    rows = (await db.execute(stmt)).scalars().all()
    return PartListOut(items=[PartOut.model_validate(p) for p in rows], total=len(rows))


@router.patch("/{part_id}", response_model=PartOut)
async def update_part(
    part_id: str,
    patch: PartPatch,
    db: AsyncSession = Depends(get_db),
) -> PartOut:
    tenant_id = current_tenant()
    part = await db.get(Part, part_id)
    if not part or part.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Part not found")

    data = patch.model_dump(exclude_unset=True)
    for field in ["sku_internal", "part_number", "brand", "notes"]:
        if field in data:
            value = data[field]
            setattr(part, field, value.strip() if isinstance(value, str) and value.strip() else None)

    if "category_id" in data:
        category_id = (data["category_id"] or "").strip() if isinstance(data["category_id"], str) else None
        if category_id:
            category = await db.get(ComponentCategory, category_id)
            if not category:
                raise HTTPException(status_code=400, detail="类目不存在")
            part.category_id = category_id
        else:
            part.category_id = None

    if "name_standard" in data:
        name = (data["name_standard"] or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="标准物料名不能为空")
        part.name_standard = name

    await db.commit()
    updated = (
        await db.execute(select(Part).where(Part.id == part_id))
    ).scalar_one()
    return PartOut.model_validate(updated)
