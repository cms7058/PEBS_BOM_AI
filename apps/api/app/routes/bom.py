from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_db
from app.models.bom import BOM, BOMNode, BOMNodeEdit
from app.schemas import BOMListItem, BOMNodeOut, BOMOut
from app.services.audit import (
    FIELD_CREATE,
    FIELD_DELETE,
    FIELD_STYLE,
    label_of,
    record_edit,
    stringify,
)


def decode_user_name(x_user_name: str | None) -> str:
    raw = x_user_name or ""
    try:
        decoded = unquote(raw)
    except Exception:
        decoded = raw
    return decoded.strip()[:128] or "anonymous"

router = APIRouter(prefix="/boms", tags=["boms"])


# Whitelist of fields the table UI is allowed to edit inline.
EDITABLE_FIELDS = {
    "part_number",
    "part_name",
    "description",
    "quantity",
    "uom",
    "material",
    "weight",
    "supplier",
    "unit_cost",
    "notes",
}


class NodePatchIn(BaseModel):
    # All optional; only present keys get applied. Use Any to accept null
    # for nullable columns (e.g. clearing a supplier).
    part_number: Any | None = None
    part_name: Any | None = None
    description: Any | None = None
    quantity: Any | None = None
    uom: Any | None = None
    material: Any | None = None
    weight: Any | None = None
    supplier: Any | None = None
    unit_cost: Any | None = None
    notes: Any | None = None

    model_config = {"extra": "ignore"}


@router.get("", response_model=list[BOMListItem])
async def list_boms(db: AsyncSession = Depends(get_db)) -> list[BOMListItem]:
    q = (
        select(BOM.id, BOM.name, func.count(BOMNode.id).label("n"))
        .join(BOMNode, BOMNode.bom_id == BOM.id, isouter=True)
        .group_by(BOM.id)
        .order_by(BOM.created_at.desc())
    )
    rows = (await db.execute(q)).all()
    return [BOMListItem(id=r.id, name=r.name, node_count=r.n or 0) for r in rows]


@router.get("/{bom_id}", response_model=BOMOut)
async def get_bom(bom_id: str, db: AsyncSession = Depends(get_db)) -> BOMOut:
    q = select(BOM).where(BOM.id == bom_id).options(selectinload(BOM.nodes))
    bom = (await db.execute(q)).scalar_one_or_none()
    if not bom:
        raise HTTPException(404, "BOM not found")
    nodes = [BOMNodeOut.model_validate(n) for n in sorted(bom.nodes, key=lambda x: x.sort_order)]
    return BOMOut(id=bom.id, name=bom.name, source_file_id=bom.source_file_id, nodes=nodes)


async def _apply_table_patch(
    db: AsyncSession,
    node: BOMNode,
    body: dict[str, Any],
    user_name: str,
    source: str = "table",
) -> int:
    """Mutate `node` from `body` (whitelisted keys) and write audit rows.
    Returns the number of audit rows written. Caller commits."""
    written = 0
    label = label_of(node)
    for k, v in body.items():
        if k not in EDITABLE_FIELDS:
            continue
        if k in {"quantity", "weight", "unit_cost"} and v not in (None, ""):
            try:
                v = float(v)
            except (TypeError, ValueError):
                raise HTTPException(400, f"{k} must be a number")
        if v == "":
            v = None
        old = getattr(node, k)
        if old == v:
            continue
        setattr(node, k, v)
        await record_edit(
            db,
            bom_id=node.bom_id,
            node_id=node.id,
            node_label=label,
            field=k,
            old_value=old,
            new_value=v,
            user_name=user_name,
            source=source,
        )
        written += 1
    return written


@router.patch("/{bom_id}/nodes/{node_id}", response_model=BOMNodeOut)
async def patch_node(
    bom_id: str,
    node_id: str,
    body: dict[str, Any],
    db: AsyncSession = Depends(get_db),
    x_user_name: str | None = Header(default=None, alias="X-User-Name"),
) -> BOMNodeOut:
    """Apply a partial update to one BOM node from the inline-edit table."""
    q = select(BOMNode).where(BOMNode.id == node_id, BOMNode.bom_id == bom_id)
    node = (await db.execute(q)).scalar_one_or_none()
    if not node:
        raise HTTPException(404, "Node not found")

    user_name = decode_user_name(x_user_name)
    written = await _apply_table_patch(db, node, body, user_name, source="table")
    if written == 0:
        raise HTTPException(400, "No editable fields supplied or no values changed")

    await db.commit()
    await db.refresh(node)
    return BOMNodeOut.model_validate(node)


class EditOut(BaseModel):
    id: str
    node_id: str
    node_label: str | None
    field: str
    field_label: str
    old_value: str | None
    new_value: str | None
    user_name: str
    source: str
    created_at: datetime


_FIELD_LABELS = {
    "part_number": "零件号",
    "part_name": "零件名",
    "description": "描述",
    "quantity": "数量",
    "uom": "单位",
    "material": "材料",
    "weight": "重量",
    "supplier": "供应商",
    "unit_cost": "单价",
    "notes": "备注",
    "parent_id": "父节点",
    "level": "层级",
    "sort_order": "排序",
    "style": "样式",
    FIELD_CREATE: "新增",
    FIELD_DELETE: "删除",
}


@router.get("/{bom_id}/edits", response_model=list[EditOut])
async def list_edits(
    bom_id: str,
    limit: int = 200,
    db: AsyncSession = Depends(get_db),
) -> list[EditOut]:
    """Return the audit log for this BOM, newest-first."""
    # Ensure BOM exists (404 if not).
    bom = (await db.execute(select(BOM.id).where(BOM.id == bom_id))).scalar_one_or_none()
    if not bom:
        raise HTTPException(404, "BOM not found")

    q = (
        select(BOMNodeEdit)
        .where(BOMNodeEdit.bom_id == bom_id)
        .order_by(BOMNodeEdit.created_at.desc())
        .limit(min(max(limit, 1), 1000))
    )
    rows = (await db.execute(q)).scalars().all()
    return [
        EditOut(
            id=r.id,
            node_id=r.node_id,
            node_label=r.node_label,
            field=r.field,
            field_label=_FIELD_LABELS.get(r.field, r.field),
            old_value=r.old_value,
            new_value=r.new_value,
            user_name=r.user_name,
            source=r.source,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.post("/{bom_id}/edits/{edit_id}/undo", response_model=BOMNodeOut)
async def undo_edit(
    bom_id: str,
    edit_id: str,
    db: AsyncSession = Depends(get_db),
    x_user_name: str | None = Header(default=None, alias="X-User-Name"),
) -> BOMNodeOut:
    """Reverse a single audit entry by writing the old value back.

    Only supports scalar field undos (the table-edit class). Create/delete/
    style/structural undos return 400 — those are richer operations.
    """
    edit = (
        await db.execute(
            select(BOMNodeEdit).where(
                BOMNodeEdit.id == edit_id, BOMNodeEdit.bom_id == bom_id
            )
        )
    ).scalar_one_or_none()
    if not edit:
        raise HTTPException(404, "Edit record not found")

    if edit.field not in EDITABLE_FIELDS:
        raise HTTPException(
            400,
            f"暂不支持撤销字段 '{edit.field}' 的修改（仅支持表格类字段）",
        )

    node = (
        await db.execute(
            select(BOMNode).where(BOMNode.id == edit.node_id, BOMNode.bom_id == bom_id)
        )
    ).scalar_one_or_none()
    if not node:
        raise HTTPException(404, "节点已不存在，无法撤销")

    # Coerce old_value (stored as string) back to the column type before writing.
    raw = edit.old_value
    new_v: Any
    if raw is None:
        new_v = None
    elif edit.field in {"quantity", "weight", "unit_cost"}:
        try:
            new_v = float(raw)
        except ValueError:
            raise HTTPException(400, f"原值 '{raw}' 无法解析为数字")
    else:
        new_v = raw

    user_name = decode_user_name(x_user_name)
    current = getattr(node, edit.field)

    if current == new_v:
        # Already at the target value (someone else may have undone it).
        return BOMNodeOut.model_validate(node)

    setattr(node, edit.field, new_v)
    await record_edit(
        db,
        bom_id=bom_id,
        node_id=node.id,
        node_label=label_of(node),
        field=edit.field,
        old_value=current,
        new_value=new_v,
        user_name=user_name,
        source="undo",
    )
    await db.commit()
    await db.refresh(node)
    return BOMNodeOut.model_validate(node)
