from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import unquote
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_db
from app.models.bom import BOM, BOMNode, BOMNodeEdit, ComponentCategory, Part
from app.schemas import (
    BOMListItem,
    BOMNodeOut,
    BOMOut,
    MappingScanItemOut,
    MappingScanOut,
    MappingStatusOut,
    PartOut,
    PartSuggestionOut,
    RiskScanItemOut,
    RiskScanOut,
    RiskTagOut,
    SuggestionReferenceOut,
)
from app.services.audit import (
    FIELD_CATEGORY,
    FIELD_CREATE,
    FIELD_DELETE,
    FIELD_PART_MAPPING,
    FIELD_SPEC,
    FIELD_STYLE,
    label_of,
    record_edit,
    stringify,
)
from app.services.part_mapping import add_alias_for_node, make_part_from_node, suggest_parts_for_node
from app.services.risk import evaluate_node_risks, severity_counts
from app.tenancy import current_tenant


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


class NodeCreateIn(BaseModel):
    parent_id: str | None = None
    part_name: str = "新子节点"
    part_number: str | None = None
    quantity: float = 1.0
    uom: str = "EA"
    material: str | None = None
    description: str | None = None
    supplier: str | None = None
    unit_cost: float | None = None
    notes: str | None = None

    model_config = {"extra": "ignore"}


class MappingConfirmIn(BaseModel):
    part_id: str


class MappingCreateIn(BaseModel):
    name_standard: str | None = None
    sku_internal: str | None = None
    part_number: str | None = None
    brand: str | None = None
    notes: str | None = None


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


def _part_out(part: Part) -> PartOut:
    return PartOut.model_validate(part)


def _suggestion_ref(item: dict[str, Any]) -> SuggestionReferenceOut | None:
    ref = item.get("reference")
    if not ref:
        return None
    return SuggestionReferenceOut(
        bom_id=ref["bom_id"],
        bom_name=ref.get("bom_name"),
        node_id=ref["node_id"],
        node_label=ref["node_label"],
    )


@router.get("/{bom_id}/nodes/{node_id}/mapping", response_model=MappingStatusOut)
async def get_node_mapping(
    bom_id: str,
    node_id: str,
    db: AsyncSession = Depends(get_db),
) -> MappingStatusOut:
    node = (
        await db.execute(select(BOMNode).where(BOMNode.id == node_id, BOMNode.bom_id == bom_id))
    ).scalar_one_or_none()
    if not node:
        raise HTTPException(404, "Node not found")
    suggestions = await suggest_parts_for_node(db, node)
    return MappingStatusOut(
        node_id=node.id,
        status=node.mapping_status or ("confirmed" if node.part_id else "unmapped"),
        mapped_part=_part_out(node.part) if node.part else None,
        suggestions=[
            PartSuggestionOut(
                part=_part_out(item["part"]),
                score=item["score"],
                reason=item["reason"],
                reference=_suggestion_ref(item),
            )
            for item in suggestions
        ],
    )


@router.get("/{bom_id}/mapping/scan", response_model=MappingScanOut)
async def scan_bom_mapping(
    bom_id: str,
    limit: int = 80,
    db: AsyncSession = Depends(get_db),
) -> MappingScanOut:
    nodes = (
        await db.execute(
            select(BOMNode)
            .where(BOMNode.bom_id == bom_id)
            .options(selectinload(BOMNode.part))
            .order_by(BOMNode.level, BOMNode.sort_order, BOMNode.part_number, BOMNode.part_name)
        )
    ).scalars().all()
    if not nodes:
        exists = (await db.execute(select(BOM.id).where(BOM.id == bom_id))).scalar_one_or_none()
        if not exists:
            raise HTTPException(404, "BOM not found")

    confirmed_count = 0
    unmapped_count = 0
    candidate_count = 0
    items: list[MappingScanItemOut] = []

    for node in nodes:
        status = node.mapping_status or ("confirmed" if node.part_id else "unmapped")
        suggestions: list[PartSuggestionOut] = []
        mapped_part = _part_out(node.part) if node.part else None
        if status == "confirmed" and node.part_id:
            confirmed_count += 1
        else:
            unmapped_count += 1
            raw_suggestions = await suggest_parts_for_node(db, node, limit=3)
            suggestions = [
                PartSuggestionOut(
                    part=_part_out(item["part"]),
                    score=float(item["score"]),
                    reason=str(item["reason"]),
                    reference=_suggestion_ref(item),
                )
                for item in raw_suggestions
            ]
            if suggestions:
                candidate_count += 1
        if len(items) < max(1, min(limit, 300)) and (status != "confirmed" or suggestions):
            items.append(
                MappingScanItemOut(
                    node_id=node.id,
                    node_label=node.part_name or node.part_number or node.id[:8],
                    status=status,
                    mapped_part=mapped_part,
                    suggestions=suggestions,
                )
            )

    return MappingScanOut(
        bom_id=bom_id,
        total_nodes=len(nodes),
        confirmed_count=confirmed_count,
        unmapped_count=unmapped_count,
        candidate_count=candidate_count,
        items=items,
    )


@router.get("/{bom_id}/risks", response_model=RiskScanOut)
async def scan_bom_risks(
    bom_id: str,
    db: AsyncSession = Depends(get_db),
) -> RiskScanOut:
    """Per-BOM rule-based risk scan. Cheap to call repeatedly — purely
    derives from existing fields, no LLM, no external calls.
    """
    nodes = (
        await db.execute(
            select(BOMNode)
            .where(BOMNode.bom_id == bom_id)
            .options(selectinload(BOMNode.part))
            .order_by(BOMNode.level, BOMNode.sort_order)
        )
    ).scalars().all()
    if not nodes:
        exists = (await db.execute(select(BOM.id).where(BOM.id == bom_id))).scalar_one_or_none()
        if not exists:
            raise HTTPException(404, "BOM not found")

    items: list[RiskScanItemOut] = []
    all_tag_lists = []
    for node in nodes:
        tags = evaluate_node_risks(node, node.part)
        all_tag_lists.append(tags)
        if not tags:
            continue
        items.append(
            RiskScanItemOut(
                node_id=node.id,
                node_label=node.part_name or node.part_number or node.id[:8],
                tags=[RiskTagOut(code=t.code, severity=t.severity, message=t.message) for t in tags],
            )
        )

    return RiskScanOut(
        bom_id=bom_id,
        total_nodes=len(nodes),
        flagged_nodes=len(items),
        severity_counts=severity_counts(all_tag_lists),
        items=items,
    )


@router.post("/{bom_id}/nodes/{node_id}/mapping/confirm", response_model=BOMNodeOut)
async def confirm_node_mapping(
    bom_id: str,
    node_id: str,
    body: MappingConfirmIn,
    db: AsyncSession = Depends(get_db),
    x_user_name: str | None = Header(default=None, alias="X-User-Name"),
) -> BOMNodeOut:
    tenant_id = current_tenant()
    node = (
        await db.execute(select(BOMNode).where(BOMNode.id == node_id, BOMNode.bom_id == bom_id))
    ).scalar_one_or_none()
    if not node:
        raise HTTPException(404, "Node not found")
    part = (
        await db.execute(select(Part).where(Part.id == body.part_id, Part.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if not part:
        raise HTTPException(404, "Part not found")
    old = node.part_id
    user_name = decode_user_name(x_user_name)
    node.part_id = part.id
    node.mapping_status = "confirmed"
    part.usage_count = (part.usage_count or 0) + 1
    part.last_used_at = datetime.utcnow()
    await add_alias_for_node(db, part=part, node=node, user_name=user_name)
    await record_edit(
        db,
        bom_id=bom_id,
        node_id=node.id,
        node_label=label_of(node),
        field=FIELD_PART_MAPPING,
        old_value=old,
        new_value=f"{part.name_standard} ({part.id[:8]})",
        user_name=user_name,
        source="agent",
    )
    await db.commit()
    await db.refresh(node)
    return BOMNodeOut.model_validate(node)


@router.post("/{bom_id}/nodes/{node_id}/mapping/create", response_model=BOMNodeOut)
async def create_part_from_mapping(
    bom_id: str,
    node_id: str,
    body: MappingCreateIn,
    db: AsyncSession = Depends(get_db),
    x_user_name: str | None = Header(default=None, alias="X-User-Name"),
) -> BOMNodeOut:
    node = (
        await db.execute(select(BOMNode).where(BOMNode.id == node_id, BOMNode.bom_id == bom_id))
    ).scalar_one_or_none()
    if not node:
        raise HTTPException(404, "Node not found")
    user_name = decode_user_name(x_user_name)
    part = make_part_from_node(node, user_name=user_name)
    if body.name_standard:
        part.name_standard = body.name_standard.strip()
    if body.sku_internal:
        part.sku_internal = body.sku_internal.strip()
    if body.part_number:
        part.part_number = body.part_number.strip()
    if body.brand:
        part.brand = body.brand.strip()
    if body.notes:
        part.notes = body.notes.strip()
    db.add(part)
    await db.flush()
    old = node.part_id
    node.part_id = part.id
    node.mapping_status = "confirmed"
    part.usage_count = 1
    part.last_used_at = datetime.utcnow()
    await add_alias_for_node(db, part=part, node=node, user_name=user_name)
    await record_edit(
        db,
        bom_id=bom_id,
        node_id=node.id,
        node_label=label_of(node),
        field=FIELD_PART_MAPPING,
        old_value=old,
        new_value=f"新建 {part.name_standard} ({part.id[:8]})",
        user_name=user_name,
        source="agent",
    )
    await db.commit()
    await db.refresh(node)
    return BOMNodeOut.model_validate(node)


@router.post("/{bom_id}/nodes", response_model=BOMNodeOut)
async def create_node(
    bom_id: str,
    body: NodeCreateIn,
    db: AsyncSession = Depends(get_db),
    x_user_name: str | None = Header(default=None, alias="X-User-Name"),
) -> BOMNodeOut:
    """Create a BOM node from direct UI actions such as graph buttons."""
    q = select(BOM).where(BOM.id == bom_id).options(selectinload(BOM.nodes))
    bom = (await db.execute(q)).scalar_one_or_none()
    if not bom:
        raise HTTPException(404, "BOM not found")

    parent: BOMNode | None = None
    if body.parent_id:
        parent = next((n for n in bom.nodes if n.id == body.parent_id), None)
        if parent is None:
            raise HTTPException(404, "Parent node not found")

    name = (body.part_name or "").strip() or "新子节点"
    max_sort = max((n.sort_order for n in bom.nodes), default=-1)
    node = BOMNode(
        id=str(uuid4()),
        bom_id=bom.id,
        parent_id=body.parent_id,
        level=(parent.level + 1) if parent else 0,
        part_name=name,
        part_number=body.part_number,
        quantity=float(body.quantity or 1),
        uom=body.uom or "EA",
        material=body.material,
        description=body.description,
        supplier=body.supplier,
        unit_cost=body.unit_cost,
        notes=body.notes,
        sort_order=max_sort + 1,
        confidence=1.0,
    )
    db.add(node)
    await db.flush()
    await record_edit(
        db,
        bom_id=bom.id,
        node_id=node.id,
        node_label=label_of(node),
        field=FIELD_CREATE,
        old_value=None,
        new_value=(
            f"{node.part_name} (level={node.level}"
            + (f", parent={body.parent_id[:8]}" if body.parent_id else "")
            + ")"
        ),
        user_name=decode_user_name(x_user_name),
        source="graph",
    )
    await db.commit()
    await db.refresh(node)
    return BOMNodeOut.model_validate(node)


@router.delete("/{bom_id}/nodes/{node_id}")
async def delete_node(
    bom_id: str,
    node_id: str,
    cascade: bool = False,
    db: AsyncSession = Depends(get_db),
    x_user_name: str | None = Header(default=None, alias="X-User-Name"),
) -> dict[str, Any]:
    """Delete one node, optionally including its subtree."""
    q = select(BOM).where(BOM.id == bom_id).options(selectinload(BOM.nodes))
    bom = (await db.execute(q)).scalar_one_or_none()
    if not bom:
        raise HTTPException(404, "BOM not found")

    target = next((n for n in bom.nodes if n.id == node_id), None)
    if not target:
        raise HTTPException(404, "Node not found")

    children = [n for n in bom.nodes if n.parent_id == node_id]
    if children and not cascade:
        raise HTTPException(400, "Node has children; pass cascade=true to delete subtree")

    to_delete: list[str] = []
    stack = [node_id]
    while stack:
        cur = stack.pop()
        to_delete.append(cur)
        stack.extend(n.id for n in bom.nodes if n.parent_id == cur)

    user_name = decode_user_name(x_user_name)
    for n in [n for n in bom.nodes if n.id in to_delete]:
        await record_edit(
            db,
            bom_id=bom.id,
            node_id=n.id,
            node_label=label_of(n),
            field=FIELD_DELETE,
            old_value=f"{n.part_name} (level={n.level}, qty={n.quantity}{n.uom})",
            new_value=None,
            user_name=user_name,
            source="graph",
        )
    for n in list(bom.nodes):
        if n.id in to_delete:
            await db.delete(n)

    await db.commit()
    return {"deleted": len(to_delete)}


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


class ClassificationPatch(BaseModel):
    """Body for PATCH .../classification.

    Either field can be omitted to leave it unchanged. To CLEAR a field
    explicitly, pass `category_id=None` or `spec={}`.
    """
    category_id: str | None = None
    spec: dict[str, Any] | None = None
    # Sentinel pattern: treat the FastAPI default of "field absent in JSON"
    # as "don't touch", and explicit null as "clear". Pydantic v2 distinguishes
    # via __pydantic_fields_set__.
    model_config = {"extra": "forbid"}


@router.patch("/{bom_id}/nodes/{node_id}/classification", response_model=BOMNodeOut)
async def patch_node_classification(
    bom_id: str,
    node_id: str,
    body: ClassificationPatch,
    db: AsyncSession = Depends(get_db),
    x_user_name: str | None = Header(default=None, alias="X-User-Name"),
) -> BOMNodeOut:
    """Set a node's category_id and/or structured spec.

    Mirrors the agent's bom_classify_node tool but bypasses the LLM round-
    trip — used by the SelectionConfiguratorModal where the user picks
    category and parameters explicitly via UI.
    """
    q = select(BOMNode).where(BOMNode.id == node_id, BOMNode.bom_id == bom_id)
    node = (await db.execute(q)).scalar_one_or_none()
    if not node:
        raise HTTPException(404, "Node not found")

    fields_set = body.model_fields_set
    user_name = decode_user_name(x_user_name)
    label = label_of(node)
    written = 0

    if "category_id" in fields_set:
        new_cat = body.category_id  # may be None (clear)
        # Validate category exists if non-null
        if new_cat is not None:
            cat = (
                await db.execute(
                    select(ComponentCategory).where(ComponentCategory.id == new_cat)
                )
            ).scalar_one_or_none()
            if cat is None:
                raise HTTPException(400, f"Unknown category_id: {new_cat}")
        if node.category_id != new_cat:
            await record_edit(
                db, bom_id=bom_id, node_id=node.id, node_label=label,
                field=FIELD_CATEGORY,
                old_value=node.category_id, new_value=new_cat,
                user_name=user_name, source="modal",
            )
            node.category_id = new_cat
            written += 1
            # Clearing category_id should also wipe spec (it's no longer
            # interpretable without a schema).
            if new_cat is None and node.spec:
                await record_edit(
                    db, bom_id=bom_id, node_id=node.id, node_label=label,
                    field=FIELD_SPEC,
                    old_value=dict(node.spec or {}), new_value={},
                    user_name=user_name, source="modal",
                )
                node.spec = {}
                written += 1

    if "spec" in fields_set:
        new_spec = dict(body.spec or {})
        # Validate spec keys against the (effective) category's parameter schema
        effective_cat_id = (
            body.category_id if "category_id" in fields_set else node.category_id
        )
        if effective_cat_id and new_spec:
            cat = (
                await db.execute(
                    select(ComponentCategory).where(ComponentCategory.id == effective_cat_id)
                )
            ).scalar_one_or_none()
            if cat is not None:
                allowed = {p.get("name") for p in (cat.parameters or [])}
                unknown = [k for k in new_spec.keys() if k not in allowed]
                if unknown:
                    raise HTTPException(
                        400,
                        f"spec contains unknown keys for {cat.name_zh}: {unknown}. "
                        f"Allowed: {sorted(allowed)}",
                    )
        old_spec = dict(node.spec or {})
        if old_spec != new_spec:
            await record_edit(
                db, bom_id=bom_id, node_id=node.id, node_label=label,
                field=FIELD_SPEC,
                old_value=old_spec, new_value=new_spec,
                user_name=user_name, source="modal",
            )
            node.spec = new_spec
            written += 1

    if written == 0:
        # No-op patch — return current state without bumping anything.
        await db.refresh(node)
        return BOMNodeOut.model_validate(node)

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
    FIELD_CATEGORY: "类目",
    FIELD_SPEC: "规格参数",
    "operation_seq": "工序号",
    "operation_desc": "工序说明",
    "fixture_ref": "工装编号",
    "consumed_by_op": "所属工序",
    "standard_time_min": "标准工时(min)",
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
