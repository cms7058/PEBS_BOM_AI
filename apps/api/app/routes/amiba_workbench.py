"""阿米巴模式工作台：平台登录 + 产品建项目 + 多人多线程计时 + 提交工时回传。

需求1/3：用户用 阿米巴用户名 + 平台令牌 登入 BOM(平台登录)，按阿米巴产品建 BOM
项目并开始计时；团队成员（来自阿米巴）各自领任务、独立计时；提交时汇总总人工工时，
按工价换算人工成本，回传到阿米巴对应产品。
"""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import BOM, AmibaPlatformSession, BomProject, BomTask, BOMNode

router = APIRouter(prefix="/amiba", tags=["amiba-workbench"])

SCOPE_LABELS = ["结构件 BOM", "标准件/紧固件 BOM", "辅料与表面处理", "工艺路线与定额", "余料/替代料核对"]


# ---------------- 平台登录（需求3）----------------

class PlatformLoginIn(BaseModel):
    amiba_endpoint: str
    username: str
    platform_token: str
    tool: str = "bom"


@router.post("/platform-login")
async def platform_login(body: PlatformLoginIn, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    url = body.amiba_endpoint.rstrip("/") + "/api/platform-auth/verify"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json={"username": body.username, "token": body.platform_token, "tool": body.tool})
        data = resp.json() if resp.status_code // 100 == 2 else {}
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"无法连接阿米巴平台：{exc}") from exc

    if not data.get("valid"):
        raise HTTPException(status_code=401, detail=data.get("reason") or "平台令牌核验未通过")

    session_token = "bps_" + secrets.token_hex(16)
    sess = AmibaPlatformSession(
        session_token=session_token,
        username=data.get("username") or body.username,
        display_name=data.get("displayName"),
        amiba_user_id=data.get("userId"),
        amiba_endpoint=body.amiba_endpoint,
        tool=body.tool,
        paid_plan=data.get("paidPlan"),
    )
    db.add(sess)
    await db.commit()
    return {
        "ok": True,
        "session_token": session_token,
        "username": sess.username,
        "display_name": sess.display_name,
        "paid_plan": sess.paid_plan,
        "enterprises": data.get("enterprises", []),
    }


# ---------------- 产品建项目 + 任务分配（需求1）----------------

class TeamMember(BaseModel):
    username: str
    displayName: str | None = None


class ProjectIn(BaseModel):
    enterprise_id: str
    enterprise_name: str | None = None
    product_id: str
    part_no: str | None = None
    product_name: str | None = None
    amiba_endpoint: str
    connector_token: str | None = None
    created_by_username: str | None = None
    team: list[TeamMember] = []


def _task_dict(t: BomTask) -> dict[str, Any]:
    elapsed = t.active_seconds + (int((datetime.utcnow() - t.running_since).total_seconds()) if t.running_since else 0)
    return {
        "id": t.id,
        "assignee_username": t.assignee_username,
        "assignee_display": t.assignee_display,
        "scope": t.scope,
        "status": t.status,
        "running": t.running_since is not None,
        "elapsed_seconds": elapsed,
    }


async def _project_dict(db: AsyncSession, p: BomProject) -> dict[str, Any]:
    tasks = (await db.execute(select(BomTask).where(BomTask.project_id == p.id))).scalars().all()
    total = sum(_task_dict(t)["elapsed_seconds"] for t in tasks)
    return {
        "id": p.id,
        "bom_id": p.bom_id,
        "mode": p.mode,
        "enterprise_id": p.enterprise_id,
        "enterprise_name": p.enterprise_name,
        "product_id": p.amiba_product_id,
        "part_no": p.part_no,
        "product_name": p.product_name,
        "labor_rate": p.labor_rate,
        "started_at": p.started_at.isoformat() if p.started_at else None,
        "submitted_at": p.submitted_at.isoformat() if p.submitted_at else None,
        "status": p.status,
        "total_seconds": total,
        "man_hours": round(total / 3600, 2),
        "labor_cost": round(total / 3600 * p.labor_rate, 2),
        "tasks": [_task_dict(t) for t in tasks],
    }


@router.post("/projects")
async def create_project(body: ProjectIn, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    # 同一产品已有进行中的项目则直接复用，避免重复进入工作台时重建
    existing = (
        await db.execute(
            select(BomProject).where(
                BomProject.amiba_product_id == body.product_id, BomProject.status == "active"
            )
        )
    ).scalars().first()
    if existing:
        return await _project_dict(db, existing)

    # 建立实际 BOM 编制记录，落地页据此直接进入 /bom/{bom_id} 编制页
    product_label = body.product_name or body.part_no or "产品"
    bom = BOM(name=f"{product_label} · BOM")
    db.add(bom)
    await db.flush()

    # 自动建一个顶级产品节点，名称与阿米巴携带的产品一致（编制从该根节点往下展开）
    db.add(BOMNode(
        bom_id=bom.id, parent_id=None, level=0,
        part_number=body.part_no, part_name=product_label,
        quantity=1.0, uom="EA", sort_order=0,
    ))

    p = BomProject(
        mode="amiba",
        bom_id=bom.id,
        enterprise_id=body.enterprise_id,
        enterprise_name=body.enterprise_name,
        amiba_product_id=body.product_id,
        part_no=body.part_no,
        product_name=body.product_name,
        amiba_endpoint=body.amiba_endpoint,
        connector_token=body.connector_token,
        created_by_username=body.created_by_username,
        started_at=datetime.utcnow(),
    )
    db.add(p)
    await db.flush()

    team = body.team or [TeamMember(username=body.created_by_username or "me")]
    for i, m in enumerate(team):
        db.add(BomTask(
            project_id=p.id,
            assignee_username=m.username,
            assignee_display=m.displayName or m.username,
            scope=SCOPE_LABELS[i % len(SCOPE_LABELS)] if len(team) > 1 else "整份 BOM 编制",
        ))
    await db.commit()
    await db.refresh(p)
    return await _project_dict(db, p)


@router.get("/projects/{project_id}")
async def get_project(project_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    p = await db.get(BomProject, project_id)
    if not p:
        raise HTTPException(status_code=404, detail="项目不存在")
    return await _project_dict(db, p)


@router.get("/projects/by-bom/{bom_id}")
async def get_project_by_bom(bom_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """编制页据此判断该 BOM 是否属于某阿米巴项目，从而渲染项目工时横幅。"""
    p = (await db.execute(select(BomProject).where(BomProject.bom_id == bom_id))).scalars().first()
    if not p:
        return {"project": None}
    return {"project": await _project_dict(db, p)}


async def _task(db: AsyncSession, project_id: str, task_id: str) -> BomTask:
    t = await db.get(BomTask, task_id)
    if not t or t.project_id != project_id:
        raise HTTPException(status_code=404, detail="任务不存在")
    return t


@router.post("/projects/{project_id}/tasks/{task_id}/start")
async def start_task(project_id: str, task_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    t = await _task(db, project_id, task_id)
    if t.running_since is None:
        t.running_since = datetime.utcnow()
        t.status = "doing"
        t.updated_at = datetime.utcnow()
        await db.commit()
    return await get_project(project_id, db)


@router.post("/projects/{project_id}/tasks/{task_id}/stop")
async def stop_task(project_id: str, task_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    t = await _task(db, project_id, task_id)
    if t.running_since is not None:
        t.active_seconds += int((datetime.utcnow() - t.running_since).total_seconds())
        t.running_since = None
        t.updated_at = datetime.utcnow()
        await db.commit()
    return await get_project(project_id, db)


@router.post("/projects/{project_id}/tasks/{task_id}/done")
async def done_task(project_id: str, task_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    t = await _task(db, project_id, task_id)
    if t.running_since is not None:
        t.active_seconds += int((datetime.utcnow() - t.running_since).total_seconds())
        t.running_since = None
    t.status = "done"
    t.updated_at = datetime.utcnow()
    await db.commit()
    return await get_project(project_id, db)


# ---------------- 提交 + 工时回传（需求1/F）----------------

@router.post("/projects/{project_id}/submit")
async def submit_project(project_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    p = await db.get(BomProject, project_id)
    if not p:
        raise HTTPException(status_code=404, detail="项目不存在")
    if p.status == "submitted":
        return await _project_dict(db, p)

    tasks = (await db.execute(select(BomTask).where(BomTask.project_id == p.id))).scalars().all()
    members = []
    total = 0
    now = datetime.utcnow()
    for t in tasks:
        if t.running_since is not None:
            t.active_seconds += int((now - t.running_since).total_seconds())
            t.running_since = None
        total += t.active_seconds
        members.append({"username": t.assignee_username, "seconds": t.active_seconds})

    man_hours = round(total / 3600, 2)
    labor_cost = round(man_hours * p.labor_rate, 2)

    report_ok, report_err = False, None
    if p.amiba_endpoint and p.connector_token and p.amiba_product_id:
        url = p.amiba_endpoint.rstrip("/") + "/api/ingest/manhours"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {p.connector_token}"},
                    json={"productId": p.amiba_product_id, "manHours": man_hours, "laborCost": labor_cost, "members": members},
                )
            report_ok = resp.status_code // 100 == 2
            if not report_ok:
                report_err = f"HTTP {resp.status_code}"
        except httpx.HTTPError as exc:
            report_err = str(exc)

    p.status = "submitted"
    p.submitted_at = now
    await db.commit()
    await db.refresh(p)
    result = await _project_dict(db, p)
    result["report"] = {"ok": report_ok, "error": report_err, "man_hours": man_hours, "labor_cost": labor_cost}
    return result
