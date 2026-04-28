from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_db
from app.models.bom import BOM
from app.services.exporter import export_bom_xlsx

router = APIRouter(prefix="/export", tags=["export"])


@router.get("/{bom_id}.xlsx")
async def export_xlsx(bom_id: str, db: AsyncSession = Depends(get_db)):
    q = select(BOM).where(BOM.id == bom_id).options(selectinload(BOM.nodes))
    bom = (await db.execute(q)).scalar_one_or_none()
    if not bom:
        raise HTTPException(404, "BOM not found")
    data = export_bom_xlsx(bom.name, bom.nodes)
    filename = f"{bom.name or 'bom'}.xlsx".replace("/", "_")
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
