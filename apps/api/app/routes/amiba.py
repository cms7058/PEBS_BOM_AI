"""阿米巴动态智能体接入（PEBS BOM 作为子工具）。

接入闭环（Phase 1）：
  1. 阿米巴「工具接入」页生成连接器令牌，跳转到本系统 web `/register`，携带
     amiba_endpoint / amiba_token / enterprise_id / source。
  2. `/register` 落地页把参数 POST 到 `POST /amiba/connect`，本端落库一条
     AmibaConnector 记录。
  3. 本端随即回调阿米巴 `POST {amiba_endpoint}/api/connectors/hello`，用令牌做
     Bearer 鉴权，上报 BOM 的版本与能力清单，完成「能力发现」。

后续 Phase 2 会用这里保存的 endpoint + token，把 BOM 指标（bom_acc / quote_acc /
辅料定额等）回填到阿米巴对应 OTD 节点。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import AmibaConnector, BOMNode
from app.tenancy import current_tenant

router = APIRouter(prefix="/amiba", tags=["amiba"])

# 与阿米巴 tools-registry 里 bom 工具的 capabilities 对齐。
BOM_VERSION = "0.0.1"
BOM_CAPABILITIES = ["BOM 自动生成", "标准用量", "余料 BOM", "辅料定额", "G6 可视化"]

# 回填目标：BOM 能为哪个 OTD 节点的哪个 KPI 提供数据。
# bom_acc 用「关键字段完整度」作为 BOM 准确率的真实代理（来自本系统 BOM 数据）。
BOM_ACC_NODE = "process_bom"
BOM_ACC_KPI = "bom_acc"


class ConnectIn(BaseModel):
    amiba_endpoint: str
    amiba_token: str
    enterprise_id: str
    source: str = "bom"
    label: str | None = None


def _status_dict(c: AmibaConnector | None) -> dict[str, Any]:
    if c is None or not c.active:
        return {"connected": False}
    return {
        "connected": True,
        "enterprise_id": c.enterprise_id,
        "source": c.source,
        "amiba_endpoint": c.amiba_endpoint,
        "label": c.label,
        "capabilities": c.capabilities or [],
        "connected_at": c.connected_at.isoformat() if c.connected_at else None,
        "last_hello_at": c.last_hello_at.isoformat() if c.last_hello_at else None,
        "hello_ok": c.hello_ok,
        "hello_error": c.hello_error,
        "last_sync_at": c.last_sync_at.isoformat() if c.last_sync_at else None,
        "last_sync_summary": c.last_sync_summary,
    }


async def _compute_metrics(db: AsyncSession) -> dict[str, float] | None:
    """从本系统 BOM 数据算出可回填阿米巴的指标。

    - bom_acc：关键字段完整度均值（part_number / part_name / uom / spec /
      material / quantity>0），作为「BOM 准确率」的真实代理。
    - mapping_rate：BOM 行已匹配到物料主数据的比例（料维度短板信号）。
    """
    rows = (
        await db.execute(
            select(
                BOMNode.part_number,
                BOMNode.part_name,
                BOMNode.uom,
                BOMNode.spec,
                BOMNode.material,
                BOMNode.quantity,
                BOMNode.mapping_status,
            )
        )
    ).all()
    total = len(rows)
    if total == 0:
        return None

    def pct(pred) -> float:
        return round(100 * sum(1 for r in rows if pred(r)) / total, 1)

    def filled(v: Any) -> bool:
        return v is not None and str(v).strip() != ""

    field_pcts = [
        pct(lambda r: filled(r.part_number)),
        pct(lambda r: filled(r.part_name)),
        pct(lambda r: filled(r.uom)),
        pct(lambda r: filled(r.spec)),
        pct(lambda r: filled(r.material)),
        pct(lambda r: (r.quantity or 0) > 0),
    ]
    return {
        "bom_acc": round(sum(field_pcts) / len(field_pcts), 1),
        "mapping_rate": pct(lambda r: r.mapping_status in ("mapped", "confirmed")),
        "node_count": float(total),
    }


async def _sync_to_amiba(connector: AmibaConnector, db: AsyncSession) -> dict[str, Any]:
    """把 BOM 指标回填到阿米巴：KPI 回填（前后对比）+ 画像指标批次。"""
    metrics = await _compute_metrics(db)
    if metrics is None:
        return {"ok": False, "error": "BOM 暂无数据可同步"}

    base = connector.amiba_endpoint.rstrip("/")
    headers = {"Authorization": f"Bearer {connector.amiba_token}"}
    now = datetime.utcnow().isoformat()
    detail: list[str] = []
    applied = 0

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # 1) 拉回填目标，确认 process_bom/bom_acc 存在
            tg = await client.get(base + "/api/ingest/targets", headers=headers)
            targets = tg.json() if tg.status_code // 100 == 2 else {}
            kpi_keys = {
                (n.get("key"), k.get("key"))
                for n in targets.get("nodes", [])
                for k in n.get("kpis", [])
            }

            # 2) KPI 回填（驱动杜邦树 / 前后对比）
            updates = []
            if (BOM_ACC_NODE, BOM_ACC_KPI) in kpi_keys:
                updates.append(
                    {
                        "nodeKey": BOM_ACC_NODE,
                        "kpiKey": BOM_ACC_KPI,
                        "value": metrics["bom_acc"],
                        "capturedAt": now,
                    }
                )
            if updates:
                r = await client.post(
                    base + "/api/ingest",
                    headers=headers,
                    json={"source": "bom", "updates": updates},
                )
                if r.status_code // 100 == 2:
                    applied = r.json().get("applied", 0)
                    detail.append(f"KPI 回填 {applied} 项")
                else:
                    detail.append(f"KPI 回填失败 HTTP {r.status_code}")
            else:
                detail.append("目标无 process_bom/bom_acc，跳过 KPI 回填")

            # 3) 画像指标批次（料维度：物料匹配率），点亮「已上报数据」并喂 5M1E 画像
            batch = {
                "source": "bom",
                "batchId": "bom-auto",
                "metrics": [
                    {
                        "factor": "material",
                        "key": "bom_mapping_rate",
                        "label": "BOM 物料匹配率",
                        "value": metrics["mapping_rate"],
                        "unit": "%",
                        "benchmark": 95,
                        "source": "bom",
                        "capturedAt": now,
                    }
                ],
            }
            rb = await client.post(base + "/api/ingest", headers=headers, json=batch)
            if rb.status_code // 100 == 2:
                detail.append(f"画像指标 {rb.json().get('metrics', 0)} 项")
            else:
                detail.append(f"画像指标失败 HTTP {rb.status_code}")
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"回填失败：{exc}"}

    summary = (
        f"bom_acc={metrics['bom_acc']}% · 匹配率={metrics['mapping_rate']}% · "
        + "，".join(detail)
    )
    connector.last_sync_at = datetime.utcnow()
    connector.last_sync_summary = summary
    await db.commit()
    return {
        "ok": True,
        "bom_acc": metrics["bom_acc"],
        "mapping_rate": metrics["mapping_rate"],
        "node_count": int(metrics["node_count"]),
        "applied": applied,
        "summary": summary,
    }


async def _active_connector(db: AsyncSession, tenant: str) -> AmibaConnector | None:
    return (
        await db.execute(
            select(AmibaConnector)
            .where(AmibaConnector.tenant_id == tenant, AmibaConnector.active.is_(True))
            .order_by(AmibaConnector.connected_at.desc())
        )
    ).scalars().first()


async def _say_hello(connector: AmibaConnector) -> tuple[bool, str | None]:
    """回调阿米巴 /api/connectors/hello 做能力上报。返回 (ok, error)。"""
    url = connector.amiba_endpoint.rstrip("/") + "/api/connectors/hello"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {connector.amiba_token}"},
                json={"version": BOM_VERSION, "capabilities": connector.capabilities},
            )
        if resp.status_code // 100 == 2:
            return True, None
        detail = ""
        try:
            detail = resp.json().get("error", "")
        except Exception:  # noqa: BLE001 - 上游可能返回非 JSON
            detail = resp.text[:200]
        return False, f"hello 失败 HTTP {resp.status_code}: {detail}"
    except httpx.HTTPError as exc:
        return False, f"无法连接阿米巴：{exc}"


@router.post("/connect")
async def connect(body: ConnectIn, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    if not body.amiba_endpoint or not body.amiba_token or not body.enterprise_id:
        raise HTTPException(status_code=400, detail="缺少 amiba_endpoint / amiba_token / enterprise_id")

    tenant = current_tenant()
    # 单租户：一个 BOM 实例同一时间只保留一条有效接入，旧的置为失效。
    for old in (
        await db.execute(
            select(AmibaConnector).where(
                AmibaConnector.tenant_id == tenant, AmibaConnector.active.is_(True)
            )
        )
    ).scalars().all():
        old.active = False

    connector = AmibaConnector(
        tenant_id=tenant,
        enterprise_id=body.enterprise_id,
        source=body.source or "bom",
        amiba_endpoint=body.amiba_endpoint,
        amiba_token=body.amiba_token,
        label=body.label,
        capabilities=BOM_CAPABILITIES,
        connected_at=datetime.utcnow(),
        active=True,
    )
    db.add(connector)
    await db.flush()

    ok, err = await _say_hello(connector)
    connector.hello_ok = ok
    connector.hello_error = err
    connector.last_hello_at = datetime.utcnow()
    await db.commit()
    await db.refresh(connector)

    # 接入成功后立即回填一次，演示闭环即时可见。
    sync: dict[str, Any] | None = None
    if ok:
        sync = await _sync_to_amiba(connector, db)
        await db.refresh(connector)

    return {"ok": ok, "sync": sync, **_status_dict(connector)}


@router.get("/status")
async def status(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    tenant = current_tenant()
    connector = await _active_connector(db, tenant)
    return _status_dict(connector)


@router.post("/sync")
async def sync(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """手动触发一次指标回填（按钮「立即同步」/ 定时任务都可调用）。"""
    tenant = current_tenant()
    connector = await _active_connector(db, tenant)
    if connector is None:
        raise HTTPException(status_code=404, detail="尚未接入阿米巴")
    result = await _sync_to_amiba(connector, db)
    await db.refresh(connector)
    return result


@router.post("/resync")
async def resync(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """用已保存的令牌重新向阿米巴上报一次能力（心跳/重连）。"""
    tenant = current_tenant()
    connector = await _active_connector(db, tenant)
    if connector is None:
        raise HTTPException(status_code=404, detail="尚未接入阿米巴")
    ok, err = await _say_hello(connector)
    connector.hello_ok = ok
    connector.hello_error = err
    connector.last_hello_at = datetime.utcnow()
    await db.commit()
    await db.refresh(connector)
    return {"ok": ok, **_status_dict(connector)}


@router.delete("/connect")
async def disconnect(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    tenant = current_tenant()
    n = 0
    for c in (
        await db.execute(
            select(AmibaConnector).where(
                AmibaConnector.tenant_id == tenant, AmibaConnector.active.is_(True)
            )
        )
    ).scalars().all():
        c.active = False
        n += 1
    await db.commit()
    return {"ok": True, "disconnected": n}
