from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.bom import AppUser, BOM, BOMNode, UploadedFile
from app.routes.admin import require_active_user
from app.schemas import UploadResponse
from app.services.bom_normalizer import normalize_spreadsheet_to_bom
from app.services.excel_parser import parse_spreadsheet
from app.services.hierarchy import assign_parents
from app.services.iges_parser import iges_nodes_to_dicts, parse_iges
from app.services.part_number_hierarchy import apply_rule, detect_rule
from app.services.step_parser import parse_step, step_nodes_to_dicts
from app.services.storage import store

router = APIRouter(prefix="/upload", tags=["upload"])


SUPPORTED_SPREADSHEETS = {"xlsx", "xls", "xlsm", "csv"}
SUPPORTED_STEP = {"step", "stp", "stpz"}
SUPPORTED_IGES = {"iges", "igs"}
SUPPORTED_CAD = SUPPORTED_STEP | SUPPORTED_IGES


@router.post("/spreadsheet", response_model=UploadResponse)
async def upload_spreadsheet(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: AppUser = Depends(require_active_user),
) -> UploadResponse:
    if user.bom_import_limit is not None and user.bom_import_count >= user.bom_import_limit:
        raise HTTPException(403, "内测 BOM 导入次数已用完，请联系管理员")
    if not file.filename:
        raise HTTPException(400, "Missing filename")
    suffix = file.filename.lower().rsplit(".", 1)[-1]
    if suffix not in SUPPORTED_SPREADSHEETS:
        raise HTTPException(400, f"Unsupported file type: {suffix}")

    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")

    # 1. Store raw file
    store.ensure_bucket()
    object_key = f"uploads/{uuid4()}/{file.filename}"
    store.put(object_key, data, file.content_type or "application/octet-stream")

    uploaded = UploadedFile(
        filename=file.filename,
        content_type=file.content_type or "application/octet-stream",
        object_key=object_key,
        size_bytes=len(data),
    )
    db.add(uploaded)
    await db.flush()

    # 2. Parse + LLM normalize
    raw = parse_spreadsheet(file.filename, data)
    try:
        normalized = await normalize_spreadsheet_to_bom(raw, file.filename)
    except Exception as exc:
        raise HTTPException(500, f"LLM 规范化失败: {exc}") from exc

    # 3. Persist BOM
    bom = BOM(name=normalized["bom_name"], source_file_id=uploaded.id)
    db.add(bom)
    await db.flush()

    node_objs: list[BOMNode] = []
    for n in normalized["nodes"]:
        node_objs.append(
            BOMNode(
                bom_id=bom.id,
                level=n["level"],
                part_number=n.get("part_number"),
                part_name=n["part_name"],
                description=n.get("description"),
                quantity=n["quantity"],
                uom=n["uom"],
                material=n.get("material"),
                weight=n.get("weight"),
                supplier=n.get("supplier"),
                unit_cost=n.get("unit_cost"),
                notes=n.get("notes"),
                confidence=n["confidence"],
                source_ref=n.get("source_ref"),
                sort_order=n["sort_order"],
            )
        )

    # 4. Hierarchy:
    #    - If part numbers look structured, use them (overrides LLM-inferred levels)
    #    - Otherwise fall back to assign_parents (by level transitions)
    rule = detect_rule(node_objs)
    if rule.confidence >= 0.5:
        apply_rule(node_objs, rule.separator)
    else:
        assign_parents(node_objs)

    db.add_all(node_objs)
    user.bom_import_count += 1
    await db.commit()

    return UploadResponse(
        bom_id=bom.id,
        file_id=uploaded.id,
        name=bom.name,
        node_count=len(node_objs),
    )


@router.post("/cad", response_model=UploadResponse)
async def upload_cad(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: AppUser = Depends(require_active_user),
) -> UploadResponse:
    """Parse a CAD file (STEP or IGES) and extract its assembly tree as a BOM.

    No geometry visualization — we only walk:
      - STEP: PRODUCT_DEFINITION + NEXT_ASSEMBLY_USAGE_OCCURRENCE
      - IGES: Subfigure Definition (308) + Subfigure Instance (408)
    """
    if user.bom_import_limit is not None and user.bom_import_count >= user.bom_import_limit:
        raise HTTPException(403, "内测 BOM 导入次数已用完，请联系管理员")
    if not file.filename:
        raise HTTPException(400, "Missing filename")
    suffix = file.filename.lower().rsplit(".", 1)[-1]
    if suffix not in SUPPORTED_CAD:
        raise HTTPException(
            400, f"Unsupported CAD type: {suffix}. Supported: {sorted(SUPPORTED_CAD)}"
        )

    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")

    is_iges = suffix in SUPPORTED_IGES
    fmt_label = "IGES" if is_iges else "STEP"
    default_ct = "application/iges" if is_iges else "application/step"

    # 1. Store raw file (so we can re-parse later if the parser improves)
    store.ensure_bucket()
    object_key = f"uploads/{uuid4()}/{file.filename}"
    store.put(object_key, data, file.content_type or default_ct)

    uploaded = UploadedFile(
        filename=file.filename,
        content_type=file.content_type or default_ct,
        object_key=object_key,
        size_bytes=len(data),
    )
    db.add(uploaded)
    await db.flush()

    # 2. Parse → flat node dict list with _parent_index pointers
    try:
        if is_iges:
            cad_nodes = parse_iges(data)
            node_dicts = iges_nodes_to_dicts(cad_nodes)
        else:
            cad_nodes = parse_step(data)
            node_dicts = step_nodes_to_dicts(cad_nodes)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"{fmt_label} 解析失败: {exc}") from exc

    # IGES often has only geometry → fall back to a single-root placeholder
    # so the user still gets a BOM record they can edit instead of a 422.
    if not node_dicts:
        if is_iges:
            base = file.filename.rsplit(".", 1)[0] or "IGES"
            node_dicts = [
                {
                    "level": 0,
                    "part_number": base,
                    "part_name": base,
                    "description": "IGES 文件未包含子图定义（Subfigure），仅含几何，自动生成单节点 BOM",
                    "quantity": 1.0,
                    "uom": "EA",
                    "material": None,
                    "weight": None,
                    "supplier": None,
                    "unit_cost": None,
                    "notes": None,
                    "confidence": 0.5,
                    "source_ref": {"type": "iges_geometry_only"},
                    "sort_order": 1,
                    "_parent_index": None,
                }
            ]
        else:
            raise HTTPException(
                422, "未在 STEP 文件中找到 PRODUCT 实体（文件可能不包含装配信息）"
            )

    # 3. Persist BOM
    bom_name = file.filename.rsplit(".", 1)[0] or f"{fmt_label} BOM"
    bom = BOM(name=bom_name, source_file_id=uploaded.id)
    db.add(bom)
    await db.flush()

    # First pass: create all BOMNode rows. We assign UUIDs *upfront* (instead
    # of letting SQLAlchemy's column default fire at INSERT) so the second
    # pass below can reference parent ids before the rows are flushed.
    node_objs: list[BOMNode] = []
    for n in node_dicts:
        node_objs.append(
            BOMNode(
                id=str(uuid4()),
                bom_id=bom.id,
                level=n["level"],
                part_number=n.get("part_number"),
                part_name=n["part_name"],
                description=n.get("description"),
                quantity=n["quantity"],
                uom=n["uom"],
                material=n.get("material"),
                weight=n.get("weight"),
                supplier=n.get("supplier"),
                unit_cost=n.get("unit_cost"),
                notes=n.get("notes"),
                confidence=n["confidence"],
                source_ref=n.get("source_ref"),
                sort_order=n["sort_order"],
            )
        )

    # Second pass: _parent_index → parent_id (now that ids are set)
    for i, n in enumerate(node_dicts):
        pidx = n.get("_parent_index")
        if pidx is not None:
            node_objs[i].parent_id = node_objs[pidx].id

    db.add_all(node_objs)
    user.bom_import_count += 1
    await db.commit()

    return UploadResponse(
        bom_id=bom.id,
        file_id=uploaded.id,
        name=bom.name,
        node_count=len(node_objs),
    )
