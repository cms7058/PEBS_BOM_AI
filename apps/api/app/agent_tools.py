"""Agent tool definitions + executors.

Tools mutate the BOM graph on the user's behalf. Each execution is wrapped in
a DB transaction and logged as a ChangeSet entry for audit / undo (todo: P3).

P2 behavior: tools apply directly. A preview/confirm flow is planned for P3
where destructive ops (delete cascade, bulk restyle) will require user OK.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.llm.base import ToolDef
from app.models.bom import BOM, BOMNode
from app.services.audit import (
    FIELD_CREATE,
    FIELD_DELETE,
    FIELD_STYLE,
    label_of,
    record_edit,
)


# ---------- Tool schemas (Anthropic-compatible JSONSchema) ----------

TOOLS: list[ToolDef] = [
    ToolDef(
        name="bom_list_nodes",
        description=(
            "列出当前 BOM 的所有节点（或按条件过滤）。用于回答问题前先了解结构。"
            "filter 是可选的，支持按 part_name/part_number/material 模糊匹配。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "filter": {
                    "type": "object",
                    "description": "可选过滤条件",
                    "properties": {
                        "name_contains": {"type": "string"},
                        "part_number_contains": {"type": "string"},
                        "material_contains": {"type": "string"},
                        "level": {"type": "integer"},
                    },
                },
            },
        },
    ),
    ToolDef(
        name="bom_add_node",
        description=(
            "在 BOM 图中新增一个节点。若提供 parent_id 则作为其子节点，否则作为顶层。"
            "level 会根据 parent 自动推断，不用传。"
        ),
        input_schema={
            "type": "object",
            "required": ["part_name"],
            "properties": {
                "parent_id": {"type": ["string", "null"], "description": "父节点 ID，null=顶层"},
                "part_name": {"type": "string"},
                "part_number": {"type": ["string", "null"]},
                "quantity": {"type": "number", "default": 1},
                "uom": {"type": "string", "default": "EA"},
                "material": {"type": ["string", "null"]},
                "description": {"type": ["string", "null"]},
                "supplier": {"type": ["string", "null"]},
                "unit_cost": {"type": ["number", "null"]},
                "notes": {"type": ["string", "null"]},
            },
        },
    ),
    ToolDef(
        name="bom_delete_node",
        description="删除一个节点。cascade=true 同时删除所有子节点；false 时若有子节点则拒绝删除。",
        input_schema={
            "type": "object",
            "required": ["node_id"],
            "properties": {
                "node_id": {"type": "string"},
                "cascade": {"type": "boolean", "default": False},
            },
        },
    ),
    ToolDef(
        name="bom_update_node",
        description=(
            "更新节点字段。【关键】只在 args 中包含真正要改的字段，其余字段一律省略。"
            "禁止用 null 来表示『不修改』——『不修改』就是不传这个字段。"
            "字符串字段必须传字符串值（如 part_number='001'，不要写成 1 或 null）。"
            "如确需清空某个字段，把该字段名加入 clear_fields 数组（例如 clear_fields=['notes']）。"
        ),
        input_schema={
            "type": "object",
            "required": ["node_id"],
            "additionalProperties": False,
            "properties": {
                "node_id": {"type": "string"},
                "part_name": {"type": "string"},
                "part_number": {"type": "string"},
                "quantity": {"type": "number"},
                "uom": {"type": "string"},
                "material": {"type": "string"},
                "description": {"type": "string"},
                "supplier": {"type": "string"},
                "unit_cost": {"type": "number"},
                "notes": {"type": "string"},
                "clear_fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "明确要清空的字段名列表（仅在用户要求清空时使用）。",
                },
            },
        },
    ),
    ToolDef(
        name="bom_restyle_node",
        description=(
            "修改单个节点在 G6 图中的视觉样式。style 字段会合并到节点的 style JSON 中。"
            "常用键: fill (填充色 hex), stroke (边框色), lineWidth, labelFill, "
            "radius, opacity。传 null 清除该键。"
        ),
        input_schema={
            "type": "object",
            "required": ["node_id", "style"],
            "properties": {
                "node_id": {"type": "string"},
                "style": {"type": "object", "description": "G6 样式键值对"},
            },
        },
    ),
    ToolDef(
        name="bom_restyle_by_rule",
        description=(
            "按规则批量改样式。例如“所有外购件改红色描边”: "
            'filter={"notes_contains":"外购"}, style={"stroke":"#d93025","lineWidth":2}'
        ),
        input_schema={
            "type": "object",
            "required": ["filter", "style"],
            "properties": {
                "filter": {
                    "type": "object",
                    "properties": {
                        "name_contains": {"type": "string"},
                        "part_number_contains": {"type": "string"},
                        "material_contains": {"type": "string"},
                        "notes_contains": {"type": "string"},
                        "level": {"type": "integer"},
                    },
                },
                "style": {"type": "object"},
            },
        },
    ),
    ToolDef(
        name="bom_move_node",
        description="把节点挂到另一个父节点下（改变层级关系）。new_parent_id=null 变顶层。",
        input_schema={
            "type": "object",
            "required": ["node_id"],
            "properties": {
                "node_id": {"type": "string"},
                "new_parent_id": {"type": ["string", "null"]},
            },
        },
    ),
]


# ---------- Executor ----------

@dataclass
class ToolResult:
    ok: bool
    summary: str
    data: Any = None
    mutated: bool = False  # whether BOM changed (frontend needs to reload)


class BOMToolExecutor:
    def __init__(self, db: AsyncSession, bom_id: str, user_name: str = "agent"):
        self.db = db
        self.bom_id = bom_id
        # Carried into every audit row written from this conversation.
        self.user_name = user_name or "agent"

    async def _bom(self) -> BOM:
        q = select(BOM).where(BOM.id == self.bom_id).options(selectinload(BOM.nodes))
        bom = (await self.db.execute(q)).scalar_one_or_none()
        if not bom:
            raise ValueError(f"BOM {self.bom_id} not found")
        return bom

    async def dispatch(self, name: str, args: dict[str, Any]) -> ToolResult:
        method = getattr(self, f"_t_{name}", None)
        if method is None:
            return ToolResult(ok=False, summary=f"未知工具: {name}")
        try:
            return await method(args)
        except Exception as exc:
            await self.db.rollback()
            return ToolResult(ok=False, summary=f"执行失败: {exc}")

    # ----- read -----

    async def _t_bom_list_nodes(self, args: dict[str, Any]) -> ToolResult:
        bom = await self._bom()
        nodes = sorted(bom.nodes, key=lambda n: n.sort_order)
        f = args.get("filter") or {}
        nc = (f.get("name_contains") or "").lower()
        pc = (f.get("part_number_contains") or "").lower()
        mc = (f.get("material_contains") or "").lower()
        lv = f.get("level")

        def match(n: BOMNode) -> bool:
            if nc and nc not in (n.part_name or "").lower():
                return False
            if pc and pc not in (n.part_number or "").lower():
                return False
            if mc and mc not in (n.material or "").lower():
                return False
            if lv is not None and n.level != lv:
                return False
            return True

        out = [
            {
                "id": n.id,
                "parent_id": n.parent_id,
                "level": n.level,
                "part_number": n.part_number,
                "part_name": n.part_name,
                "quantity": n.quantity,
                "uom": n.uom,
                "material": n.material,
                "notes": n.notes,
            }
            for n in nodes
            if match(n)
        ]
        return ToolResult(ok=True, summary=f"返回 {len(out)} 个节点", data={"nodes": out})

    # ----- write -----

    async def _t_bom_add_node(self, args: dict[str, Any]) -> ToolResult:
        bom = await self._bom()
        parent_id = args.get("parent_id")
        parent: BOMNode | None = None
        if parent_id:
            parent = next((n for n in bom.nodes if n.id == parent_id), None)
            if parent is None:
                return ToolResult(ok=False, summary=f"父节点 {parent_id} 不存在")
        level = (parent.level + 1) if parent else 0
        max_sort = max((n.sort_order for n in bom.nodes), default=-1)

        node = BOMNode(
            id=str(uuid4()),
            bom_id=bom.id,
            parent_id=parent_id,
            level=level,
            part_name=args["part_name"],
            part_number=args.get("part_number"),
            quantity=float(args.get("quantity") or 1),
            uom=args.get("uom") or "EA",
            material=args.get("material"),
            description=args.get("description"),
            supplier=args.get("supplier"),
            unit_cost=args.get("unit_cost"),
            notes=args.get("notes"),
            sort_order=max_sort + 1,
            confidence=1.0,
        )
        self.db.add(node)
        await self.db.flush()
        await record_edit(
            self.db,
            bom_id=bom.id,
            node_id=node.id,
            node_label=label_of(node),
            field=FIELD_CREATE,
            old_value=None,
            new_value=(
                f"{node.part_name} (level={node.level}"
                + (f", parent={parent_id[:8]}" if parent_id else "")
                + ")"
            ),
            user_name=self.user_name,
            source="agent",
        )
        await self.db.commit()
        return ToolResult(
            ok=True,
            summary=f"已新增节点: {node.part_name} (id={node.id[:8]})",
            data={"id": node.id},
            mutated=True,
        )

    async def _t_bom_delete_node(self, args: dict[str, Any]) -> ToolResult:
        bom = await self._bom()
        nid = args["node_id"]
        target = next((n for n in bom.nodes if n.id == nid), None)
        if not target:
            return ToolResult(ok=False, summary=f"节点 {nid} 不存在")

        children = [n for n in bom.nodes if n.parent_id == nid]
        cascade = bool(args.get("cascade"))
        if children and not cascade:
            return ToolResult(
                ok=False,
                summary=f"节点 {target.part_name} 有 {len(children)} 个子节点，"
                "需要 cascade=true 才能删除",
            )

        # gather full subtree
        to_delete: list[str] = []
        stack = [nid]
        while stack:
            cur = stack.pop()
            to_delete.append(cur)
            stack.extend(n.id for n in bom.nodes if n.parent_id == cur)

        # Snapshot deleted nodes for the audit log before they vanish.
        deleted_nodes = [n for n in bom.nodes if n.id in to_delete]
        for n in deleted_nodes:
            await record_edit(
                self.db,
                bom_id=bom.id,
                node_id=n.id,
                node_label=label_of(n),
                field=FIELD_DELETE,
                old_value=(
                    f"{n.part_name} (level={n.level}, qty={n.quantity}{n.uom})"
                ),
                new_value=None,
                user_name=self.user_name,
                source="agent",
            )
        for n in list(bom.nodes):
            if n.id in to_delete:
                await self.db.delete(n)
        await self.db.commit()
        return ToolResult(
            ok=True,
            summary=f"已删除 {len(to_delete)} 个节点（含子树）",
            mutated=True,
        )

    async def _t_bom_update_node(self, args: dict[str, Any]) -> ToolResult:
        bom = await self._bom()
        nid = args["node_id"]
        node = next((n for n in bom.nodes if n.id == nid), None)
        if not node:
            return ToolResult(ok=False, summary=f"节点 {nid} 不存在")
        label = label_of(node)
        changed: list[str] = []
        rejected: list[str] = []  # silently-null fields we refused to wipe
        clear_set = set(args.get("clear_fields") or [])
        for field in (
            "part_name", "part_number", "quantity", "uom", "material",
            "description", "supplier", "unit_cost", "notes",
        ):
            in_args = field in args
            wants_clear = field in clear_set
            if not in_args and not wants_clear:
                continue
            old_v = getattr(node, field)
            if wants_clear:
                new_v = None
            else:
                new_v = args[field]
                # Defense against the model still leaking null despite the
                # tightened schema (older snapshots of M2.7 do this). If a
                # field arrives null but isn't in clear_fields, drop it
                # rather than wipe an existing value.
                if new_v is None:
                    if old_v not in (None, ""):
                        rejected.append(field)
                    continue
            if old_v == new_v:
                continue
            setattr(node, field, new_v)
            await record_edit(
                self.db,
                bom_id=bom.id,
                node_id=node.id,
                node_label=label,
                field=field,
                old_value=old_v,
                new_value=new_v,
                user_name=self.user_name,
                source="agent",
            )
            changed.append(field)
        await self.db.commit()
        if rejected and not changed:
            return ToolResult(
                ok=False,
                summary=(
                    f"拒绝修改 {label}：以下字段被传入 null 但当前有值，"
                    f"如确需清空请在 intent_clear 中列出：{', '.join(rejected)}"
                ),
            )
        msg = f"已更新 {node.part_name}: {', '.join(changed) or '无变化'}"
        if rejected:
            msg += f"（已拒绝 null 清空：{', '.join(rejected)}）"
        return ToolResult(ok=True, summary=msg, mutated=bool(changed))

    async def _t_bom_restyle_node(self, args: dict[str, Any]) -> ToolResult:
        bom = await self._bom()
        nid = args["node_id"]
        node = next((n for n in bom.nodes if n.id == nid), None)
        if not node:
            return ToolResult(ok=False, summary=f"节点 {nid} 不存在")
        old_style = dict(node.style or {})
        merged = dict(old_style)
        for k, v in (args.get("style") or {}).items():
            if v is None:
                merged.pop(k, None)
            else:
                merged[k] = v
        if merged != old_style:
            node.style = merged
            await record_edit(
                self.db,
                bom_id=bom.id,
                node_id=node.id,
                node_label=label_of(node),
                field=FIELD_STYLE,
                old_value=old_style,
                new_value=merged,
                user_name=self.user_name,
                source="agent",
            )
        await self.db.commit()
        return ToolResult(
            ok=True, summary=f"已更新样式 {node.part_name}", mutated=merged != old_style
        )

    async def _t_bom_restyle_by_rule(self, args: dict[str, Any]) -> ToolResult:
        bom = await self._bom()
        f = args.get("filter") or {}
        style = args.get("style") or {}
        if not style:
            return ToolResult(ok=False, summary="style 不能为空")

        nc = (f.get("name_contains") or "").lower()
        pc = (f.get("part_number_contains") or "").lower()
        mc = (f.get("material_contains") or "").lower()
        ntc = (f.get("notes_contains") or "").lower()
        lv = f.get("level")

        n_hit = 0
        for node in bom.nodes:
            if nc and nc not in (node.part_name or "").lower(): continue
            if pc and pc not in (node.part_number or "").lower(): continue
            if mc and mc not in (node.material or "").lower(): continue
            if ntc and ntc not in (node.notes or "").lower(): continue
            if lv is not None and node.level != lv: continue
            old_style = dict(node.style or {})
            merged = dict(old_style)
            for k, v in style.items():
                if v is None: merged.pop(k, None)
                else: merged[k] = v
            if merged == old_style:
                continue
            node.style = merged
            await record_edit(
                self.db,
                bom_id=bom.id,
                node_id=node.id,
                node_label=label_of(node),
                field=FIELD_STYLE,
                old_value=old_style,
                new_value=merged,
                user_name=self.user_name,
                source="agent",
            )
            n_hit += 1
        await self.db.commit()
        return ToolResult(
            ok=True, summary=f"已批量更新 {n_hit} 个节点的样式", mutated=n_hit > 0
        )

    async def _t_bom_move_node(self, args: dict[str, Any]) -> ToolResult:
        bom = await self._bom()
        nid = args["node_id"]
        new_parent = args.get("new_parent_id")
        node = next((n for n in bom.nodes if n.id == nid), None)
        if not node:
            return ToolResult(ok=False, summary=f"节点 {nid} 不存在")

        if new_parent:
            cur = new_parent
            while cur:
                if cur == nid:
                    return ToolResult(ok=False, summary="不能把节点挂到自己的子树下")
                parent_node = next((n for n in bom.nodes if n.id == cur), None)
                cur = parent_node.parent_id if parent_node else None
            parent_node = next((n for n in bom.nodes if n.id == new_parent), None)
            if not parent_node:
                return ToolResult(ok=False, summary=f"新父节点 {new_parent} 不存在")
            new_level = parent_node.level + 1
        else:
            new_level = 0

        old_parent = node.parent_id
        old_level = node.level
        if old_parent == new_parent and old_level == new_level:
            return ToolResult(ok=True, summary="无变化", mutated=False)

        label = label_of(node)
        node.parent_id = new_parent
        node.level = new_level
        if old_parent != new_parent:
            await record_edit(
                self.db,
                bom_id=bom.id,
                node_id=node.id,
                node_label=label,
                field="parent_id",
                old_value=old_parent,
                new_value=new_parent,
                user_name=self.user_name,
                source="agent",
            )
        if old_level != new_level:
            await record_edit(
                self.db,
                bom_id=bom.id,
                node_id=node.id,
                node_label=label,
                field="level",
                old_value=old_level,
                new_value=new_level,
                user_name=self.user_name,
                source="agent",
            )
        await self.db.commit()
        return ToolResult(
            ok=True, summary=f"已移动 {node.part_name}", mutated=True
        )
