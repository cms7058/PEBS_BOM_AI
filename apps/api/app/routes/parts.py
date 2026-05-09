from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.bom import BOM, BOMNode, ComponentCategory, Part, PartAlias, PartImportDraft
from app.schemas import (
    PartAliasOut,
    PartDetailOut,
    PartImportConfirmOut,
    PartImportDraftOut,
    PartListOut,
    PartOut,
    PartPatch,
    PartReferenceOut,
)
from app.tenancy import current_tenant
from app.services.excel_parser import parse_spreadsheet

router = APIRouter(prefix="/parts", tags=["parts"])


def _draft_out(draft: PartImportDraft) -> PartImportDraftOut:
    return PartImportDraftOut.model_validate(draft)


def _pick(row: dict, names: list[str]) -> str | None:
    lower = {str(k).strip().lower(): v for k, v in row.items()}
    for name in names:
        if name.lower() in lower and str(lower[name.lower()]).strip():
            return str(lower[name.lower()]).strip()
    for key, value in row.items():
        k = str(key).lower()
        if any(name.lower() in k for name in names) and str(value).strip():
            return str(value).strip()
    return None


def _rows_to_draft_rows(raw_rows: list[dict], categories: dict[str, ComponentCategory]) -> list[dict]:
    by_name = {c.name_zh: c for c in categories.values()}
    rows: list[dict] = []
    for raw in raw_rows:
        name = _pick(raw, ["标准物料", "物料名称", "名称", "name_standard", "part_name", "品名"])
        if not name:
            continue
        cat_name = _pick(raw, ["类目", "分类", "category", "category_name"])
        category = by_name.get(cat_name or "") or categories.get(cat_name or "")
        cost_raw = _pick(raw, ["单价", "成本", "unit_cost", "price"])
        try:
            unit_cost = float(cost_raw) if cost_raw else None
        except ValueError:
            unit_cost = None
        rows.append(
            {
                "action": "create",
                "name_standard": name,
                "sku_internal": _pick(raw, ["内部 SKU", "SKU", "sku_internal", "内部料号"]),
                "part_number": _pick(raw, ["零件号", "料号", "型号", "part_number", "PN"]),
                "category_id": category.id if category else None,
                "category_name": category.name_zh if category else cat_name,
                "brand": _pick(raw, ["品牌", "brand"]),
                "supplier": _pick(raw, ["供应商", "supplier"]),
                "uom": _pick(raw, ["单位", "uom"]) or "EA",
                "unit_cost": unit_cost,
                "typical_lead_time": _pick(raw, ["货期", "lead_time", "typical_lead_time"]),
                "notes": _pick(raw, ["备注", "notes"]),
                "risk": None if category else "未识别类目",
            }
        )
    return rows


@router.get("", response_model=PartListOut)
async def list_parts(
    q: str | None = None,
    category_id: str | None = None,
    limit: int = 200,
    db: AsyncSession = Depends(get_db),
) -> PartListOut:
    tenant_id = current_tenant()
    stmt = select(Part).where(Part.tenant_id == tenant_id)
    if category_id:
        stmt = stmt.where(Part.category_id == category_id)
    needle = (q or "").strip()
    if needle:
        like = f"%{needle}%"
        stmt = stmt.where(
            or_(
                Part.name_standard.ilike(like),
                Part.part_number.ilike(like),
                Part.sku_internal.ilike(like),
                Part.brand.ilike(like),
                Part.supplier.ilike(like),
            )
        )
    stmt = stmt.order_by(Part.updated_at.desc(), Part.created_at.desc()).limit(max(1, min(limit, 1000)))
    rows = (await db.execute(stmt)).scalars().all()
    return PartListOut(items=[PartOut.model_validate(p) for p in rows], total=len(rows))


@router.get("/import-drafts/{draft_id}", response_model=PartImportDraftOut)
async def get_import_draft(
    draft_id: str,
    db: AsyncSession = Depends(get_db),
) -> PartImportDraftOut:
    tenant_id = current_tenant()
    draft = await db.get(PartImportDraft, draft_id)
    if not draft or draft.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Draft not found")
    return _draft_out(draft)


@router.post("/import-drafts/{draft_id}/confirm", response_model=PartImportConfirmOut)
async def confirm_import_draft(
    draft_id: str,
    db: AsyncSession = Depends(get_db),
) -> PartImportConfirmOut:
    tenant_id = current_tenant()
    draft = await db.get(PartImportDraft, draft_id)
    if not draft or draft.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Draft not found")
    if draft.status == "confirmed":
        return PartImportConfirmOut(draft=_draft_out(draft), created=[])

    created: list[Part] = []
    for row in draft.rows or []:
        name = (row.get("name_standard") or "").strip()
        if not name:
            continue
        part = Part(
            tenant_id=tenant_id,
            name_standard=name,
            sku_internal=(row.get("sku_internal") or "").strip() or None,
            part_number=(row.get("part_number") or "").strip() or None,
            category_id=(row.get("category_id") or "").strip() or None,
            brand=(row.get("brand") or "").strip() or None,
            supplier=(row.get("supplier") or "").strip() or None,
            uom=(row.get("uom") or "EA").strip() or "EA",
            unit_cost=row.get("unit_cost"),
            typical_lead_time=(row.get("typical_lead_time") or "").strip() or None,
            notes=(row.get("notes") or "").strip() or None,
            status="active",
            created_by=draft.created_by,
        )
        db.add(part)
        created.append(part)

    draft.status = "confirmed"
    draft.confirmed_at = datetime.utcnow()
    await db.commit()
    for part in created:
        await db.refresh(part)
    await db.refresh(draft)
    return PartImportConfirmOut(
        draft=_draft_out(draft),
        created=[PartOut.model_validate(p) for p in created],
    )


@router.post("/import-drafts/upload", response_model=PartImportDraftOut)
async def upload_import_draft(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> PartImportDraftOut:
    tenant_id = current_tenant()
    data = await file.read()
    parsed = parse_spreadsheet(file.filename or "import.xlsx", data)
    categories = {
        c.id: c for c in (await db.execute(select(ComponentCategory))).scalars().all()
    }
    rows = _rows_to_draft_rows(parsed["rows"], categories)
    if not rows:
        raise HTTPException(status_code=400, detail="未识别到可导入的标准物料行")
    draft = PartImportDraft(
        tenant_id=tenant_id,
        source_type="file",
        status="draft",
        rows=rows,
        created_by="file-import",
    )
    db.add(draft)
    await db.commit()
    await db.refresh(draft)
    return _draft_out(draft)


@router.get("/{part_id}", response_model=PartDetailOut)
async def get_part_detail(
    part_id: str,
    db: AsyncSession = Depends(get_db),
) -> PartDetailOut:
    tenant_id = current_tenant()
    part = await db.get(Part, part_id)
    if not part or part.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Part not found")

    ref_rows = (
        await db.execute(
            select(BOMNode, BOM)
            .join(BOM, BOM.id == BOMNode.bom_id)
            .where(BOMNode.part_id == part.id)
            .order_by(BOM.updated_at.desc(), BOMNode.level, BOMNode.sort_order)
            .limit(80)
        )
    ).all()
    alias_rows = (
        await db.execute(
            select(PartAlias)
            .where(PartAlias.part_id == part.id, PartAlias.tenant_id == tenant_id)
            .order_by(PartAlias.confirmed_at.desc(), PartAlias.created_at.desc())
            .limit(40)
        )
    ).scalars().all()
    return PartDetailOut(
        part=PartOut.model_validate(part),
        references=[
            PartReferenceOut(
                bom_id=bom.id,
                bom_name=bom.name,
                node_id=node.id,
                node_label=node.part_name,
                part_number=node.part_number,
                quantity=node.quantity,
                uom=node.uom,
                supplier=node.supplier,
                unit_cost=node.unit_cost,
            )
            for node, bom in ref_rows
        ],
        aliases=[
            PartAliasOut(
                raw_name=a.raw_name,
                raw_part_number=a.raw_part_number,
                confirmed_at=a.confirmed_at,
            )
            for a in alias_rows
        ],
    )


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
    for field in [
        "sku_internal",
        "part_number",
        "brand",
        "supplier",
        "typical_lead_time",
        "notes",
    ]:
        if field in data:
            value = data[field]
            setattr(part, field, value.strip() if isinstance(value, str) and value.strip() else None)

    if "uom" in data:
        uom = (data["uom"] or "").strip()
        part.uom = uom or "EA"

    if "unit_cost" in data:
        part.unit_cost = data["unit_cost"]

    if "status" in data:
        status = (data["status"] or "").strip() or "active"
        if status not in {"active", "inactive", "pending"}:
            raise HTTPException(status_code=400, detail="状态只能是 active / inactive / pending")
        part.status = status

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
