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
from app.models.bom import BOM, BOMNode, ComponentCategory
from app.services.component_classifier import classify as heuristic_classify
from app.services.audit import (
    FIELD_CATEGORY,
    FIELD_CREATE,
    FIELD_DELETE,
    FIELD_SPEC,
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
            "修改单个节点在 BOM 图卡片上的视觉样式。style 会合并到节点 style JSON。"
            "传 null 清除该键。\n"
            "支持的键（优先用语义键）：\n"
            "  · highlight (bool)：高亮该节点（红色 2px 描边）。"
            "想标记『需关注 / 风险件 / 待审核』时用这个。\n"
            "  · dim (bool)：把节点变灰（opacity 0.45）。"
            "想标记『已弃用 / 待删除』时用这个。\n"
            "  · accent (hex 颜色)：覆盖右下三角、百分比、底部进度条的状态色。"
            "想强调某种分类配色时用这个。\n"
            "  · badge (string)：在节点右上角显示一个短标签，"
            "如 \"OEM\"/\"外购\"/\"重要\"。最多 4 个汉字或 8 个字母。\n"
            "  · 兜底通用键：fill (卡片填充色)、stroke (边框色)、lineWidth、opacity。"
        ),
        input_schema={
            "type": "object",
            "required": ["node_id", "style"],
            "properties": {
                "node_id": {"type": "string"},
                "style": {"type": "object", "description": "样式键值对，见工具说明"},
            },
        },
    ),
    ToolDef(
        name="bom_restyle_by_rule",
        description=(
            "按规则批量改样式（同 bom_restyle_node 的样式键，作用到一组节点）。"
            "例：『所有外购件加 OEM 标签且高亮』 →"
            ' filter={"notes_contains":"外购"}, style={"badge":"OEM","highlight":true}'
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
    ToolDef(
        name="bom_describe_node",
        description=(
            "返回指定节点当前所有可视元素（slot）的清单：每个 slot 当前显示什么、"
            "支持改哪些属性。\n"
            "**触发时机**：用户表达样式调整意图但还没说清要改哪个元素时（"
            "例如『改一下这个节点的样式』），先用本工具拿到 slot 表，"
            "用 markdown 表格展示给用户挑选，再据其选择调用 bom_set_slot。"
        ),
        input_schema={
            "type": "object",
            "required": ["node_id"],
            "properties": {"node_id": {"type": "string"}},
        },
    ),
    ToolDef(
        name="bom_set_slot",
        description=(
            "修改节点中**单个 slot**（视觉元素）的属性。最细粒度的样式编辑入口。\n"
            "slot 取值：header / title / qty / metric / trend / progress / badge / card\n"
            "  · header   顶部小号文字（默认显示零件号）\n"
            "  · title    底部大号文字（默认显示零件名）\n"
            "  · qty      『× N 单位』段\n"
            "  · metric   右下角数值（默认置信度%；可改 text 或 bound 任意 BOM 字段）\n"
            "  · trend    数值左侧的上下三角\n"
            "  · progress 卡片底部的进度条\n"
            "  · badge    右上角小徽章\n"
            "  · card     卡片本身（背景/边框）\n"
            "attrs 取值（看 slot 类型）：\n"
            "  · text (string)：固定显示这段文本\n"
            "  · bound (string)：把该 slot 绑定到 BOM 字段，自动跟随。"
            "支持: part_number / part_name / quantity / uom / material / supplier"
            " / unit_cost / notes / description / confidence_pct\n"
            "  · color (hex)：文字 / 三角 / 进度条 / 徽章背景的颜色\n"
            "  · visible (bool)：是否显示该元素\n"
            "  · 仅 card slot：fill / stroke / lineWidth / opacity\n"
            "传 null 清除该键。"
        ),
        input_schema={
            "type": "object",
            "required": ["node_id", "slot", "attrs"],
            "properties": {
                "node_id": {"type": "string"},
                "slot": {
                    "type": "string",
                    "enum": [
                        "header", "title", "qty", "metric",
                        "trend", "progress", "badge", "card",
                    ],
                },
                "attrs": {"type": "object", "description": "见工具描述"},
            },
        },
    ),
    ToolDef(
        name="bom_set_slot_by_rule",
        description=(
            "按规则批量改某个 slot（同 bom_set_slot 的 attrs，作用到一组节点）。\n"
            '例：所有非标件 metric 改成显示供应商 → '
            'filter={"notes_contains":"非标"}, slot="metric", attrs={"bound":"supplier"}'
        ),
        input_schema={
            "type": "object",
            "required": ["filter", "slot", "attrs"],
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
                "slot": {
                    "type": "string",
                    "enum": [
                        "header", "title", "qty", "metric",
                        "trend", "progress", "badge", "card",
                    ],
                },
                "attrs": {"type": "object"},
            },
        },
    ),
    ToolDef(
        name="component_categories_list",
        description=(
            "列出所有可用的非标件类目（直线导轨/滚珠丝杠/铝型材/定位销/联轴器…），"
            "返回每个类目的参数 schema 和常用品牌。在做 bom_classify_node 前可先查询，"
            "或者用户问『有哪些类目？』时调用。"
        ),
        input_schema={"type": "object", "properties": {}},
    ),
    ToolDef(
        name="bom_classify_node",
        description=(
            "把一个 BOM 节点归类到 component_categories 里的某个类目，并尽量从"
            " part_name / part_number / description 解析出该类目要求的参数（spec）。\n"
            "用法两种：\n"
            "  · 你已经能确定类目和参数 → 直接传 category_id 和 spec\n"
            "  · 你只想标注类目、参数让用户后填 → 只传 category_id，spec 省略或填部分\n"
            "传 spec={} 表示清空规格；传 category_id=null 表示取消分类。"
        ),
        input_schema={
            "type": "object",
            "required": ["node_id"],
            "properties": {
                "node_id": {"type": "string"},
                "category_id": {
                    "type": ["string", "null"],
                    "description": "类目 ID（如 linear_guide）。null 取消分类。"
                },
                "spec": {
                    "type": "object",
                    "description": "结构化参数；键应匹配该类目 parameters 里的 name。"
                },
            },
        },
    ),
    ToolDef(
        name="bom_classify_all",
        description=(
            "批量自动分类整张 BOM 中尚未分类的节点。"
            "对每个 category_id 为空的节点，根据 part_name / description 推断最合适的类目。"
            "不确定的件保持未分类，不会强行打标。\n"
            "返回 {classified, skipped, unclassified} 统计 + 不确定件清单。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "force": {
                    "type": "boolean",
                    "default": False,
                    "description": "true 时连已分类的节点也重新评估"
                },
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

    # ----- per-slot style editing -----

    # Frontend slot vocabulary, kept in sync with apps/web/components/BOMGraph.tsx.
    _SLOT_LABELS = {
        "header":   "顶部小号文字",
        "title":    "底部大号文字",
        "qty":      "数量·单位段",
        "metric":   "右下角数值",
        "trend":    "数值左侧的上下三角",
        "progress": "底部进度条",
        "badge":    "右上角徽章",
        "card":     "卡片本身（背景/边框）",
    }
    _BOUND_FIELDS = [
        "part_number", "part_name", "quantity", "uom", "material",
        "supplier", "unit_cost", "notes", "description", "confidence_pct",
    ]

    @staticmethod
    def _slot_default_text(slot: str, n: BOMNode) -> str:
        if slot == "header":
            return n.part_number or ""
        if slot == "title":
            return n.part_name or ""
        if slot == "qty":
            return f"× {n.quantity} {n.uom}".strip()
        if slot == "metric":
            return f"{round((n.confidence or 0) * 100)}%"
        return ""

    async def _t_bom_describe_node(self, args: dict[str, Any]) -> ToolResult:
        bom = await self._bom()
        nid = args["node_id"]
        node = next((n for n in bom.nodes if n.id == nid), None)
        if not node:
            return ToolResult(ok=False, summary=f"节点 {nid} 不存在")
        style = dict(node.style or {})
        slots_state = (style.get("slots") or {}) if isinstance(style.get("slots"), dict) else {}
        out_slots = []
        for sid, label in self._SLOT_LABELS.items():
            cur = slots_state.get(sid) or {}
            if sid == "card":
                editable = ["fill", "stroke", "lineWidth", "opacity", "visible"]
                current = {
                    "fill": cur.get("fill", style.get("fill", "#fff")),
                    "stroke": cur.get("stroke", style.get("stroke", "#CED4D9")),
                    "lineWidth": cur.get("lineWidth", style.get("lineWidth", 1)),
                    "opacity": cur.get("opacity", style.get("opacity", 1)),
                }
            else:
                editable = ["color", "visible"]
                if sid in ("header", "title", "qty", "metric", "badge"):
                    editable = ["text", "color", "visible"] + (
                        ["bound"] if sid in ("header", "title", "metric") else []
                    )
                current = {
                    "displayed": (
                        cur.get("text")
                        or (
                            f"<{cur['bound']}>" if cur.get("bound") else
                            self._slot_default_text(sid, node)
                        )
                    ),
                    "color": cur.get("color"),
                    "visible": cur.get("visible", True),
                    "bound": cur.get("bound"),
                }
            out_slots.append({
                "id": sid,
                "label": label,
                "current": current,
                "editable": editable,
            })
        return ToolResult(
            ok=True,
            summary=f"返回 {node.part_name} 的 {len(out_slots)} 个 slot 信息",
            data={
                "node": {
                    "id": node.id,
                    "part_number": node.part_number,
                    "part_name": node.part_name,
                },
                "slots": out_slots,
                "bound_fields": self._BOUND_FIELDS,
            },
        )

    @staticmethod
    def _merge_slot(style: dict, slot: str, attrs: dict) -> dict:
        new_style = dict(style or {})
        slots = dict(new_style.get("slots") or {})
        cur = dict(slots.get(slot) or {})
        for k, v in attrs.items():
            if v is None:
                cur.pop(k, None)
            else:
                cur[k] = v
        if cur:
            slots[slot] = cur
        else:
            slots.pop(slot, None)
        if slots:
            new_style["slots"] = slots
        else:
            new_style.pop("slots", None)
        return new_style

    async def _t_bom_set_slot(self, args: dict[str, Any]) -> ToolResult:
        bom = await self._bom()
        nid = args["node_id"]
        slot = args["slot"]
        attrs = args.get("attrs") or {}
        if slot not in self._SLOT_LABELS:
            return ToolResult(ok=False, summary=f"未知 slot: {slot}")
        if not attrs:
            return ToolResult(ok=False, summary="attrs 不能为空")
        node = next((n for n in bom.nodes if n.id == nid), None)
        if not node:
            return ToolResult(ok=False, summary=f"节点 {nid} 不存在")
        old_style = dict(node.style or {})
        new_style = self._merge_slot(old_style, slot, attrs)
        if new_style == old_style:
            return ToolResult(ok=True, summary="无变化", mutated=False)
        node.style = new_style
        await record_edit(
            self.db,
            bom_id=bom.id,
            node_id=node.id,
            node_label=label_of(node),
            field=FIELD_STYLE,
            old_value=old_style,
            new_value=new_style,
            user_name=self.user_name,
            source="agent",
        )
        await self.db.commit()
        return ToolResult(
            ok=True,
            summary=f"已更新 {node.part_name} 的 {self._SLOT_LABELS[slot]}",
            mutated=True,
        )

    async def _t_bom_set_slot_by_rule(self, args: dict[str, Any]) -> ToolResult:
        bom = await self._bom()
        slot = args["slot"]
        attrs = args.get("attrs") or {}
        f = args.get("filter") or {}
        if slot not in self._SLOT_LABELS:
            return ToolResult(ok=False, summary=f"未知 slot: {slot}")
        if not attrs:
            return ToolResult(ok=False, summary="attrs 不能为空")

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
            new_style = self._merge_slot(old_style, slot, attrs)
            if new_style == old_style:
                continue
            node.style = new_style
            await record_edit(
                self.db,
                bom_id=bom.id,
                node_id=node.id,
                node_label=label_of(node),
                field=FIELD_STYLE,
                old_value=old_style,
                new_value=new_style,
                user_name=self.user_name,
                source="agent",
            )
            n_hit += 1
        await self.db.commit()
        return ToolResult(
            ok=True,
            summary=f"已批量更新 {n_hit} 个节点的 {self._SLOT_LABELS[slot]}",
            mutated=n_hit > 0,
        )

    # ----- non-std component classification -----

    async def _t_component_categories_list(self, args: dict[str, Any]) -> ToolResult:
        rows = (
            await self.db.execute(select(ComponentCategory).order_by(ComponentCategory.sort_order))
        ).scalars().all()
        out = [
            {
                "id": c.id,
                "name_zh": c.name_zh,
                "name_en": c.name_en,
                "description": c.description,
                "parameters": c.parameters,
                "common_brands": c.common_brands,
                "typical_use": c.typical_use,
            }
            for c in rows
        ]
        return ToolResult(
            ok=True,
            summary=f"返回 {len(out)} 个类目",
            data={"categories": out},
        )

    async def _t_bom_classify_node(self, args: dict[str, Any]) -> ToolResult:
        bom = await self._bom()
        nid = args["node_id"]
        node = next((n for n in bom.nodes if n.id == nid), None)
        if not node:
            return ToolResult(ok=False, summary=f"节点 {nid} 不存在")

        new_cat = args.get("category_id", "__missing__")
        new_spec = args.get("spec", "__missing__")
        if new_cat == "__missing__" and new_spec == "__missing__":
            return ToolResult(ok=False, summary="必须传 category_id 或 spec 之一")

        # Validate category exists if provided (and not null)
        if new_cat not in ("__missing__", None):
            cat = (
                await self.db.execute(
                    select(ComponentCategory).where(ComponentCategory.id == new_cat)
                )
            ).scalar_one_or_none()
            if cat is None:
                return ToolResult(ok=False, summary=f"未知类目 {new_cat!r}")
            # Validate spec keys against the category schema if both provided
            if new_spec not in ("__missing__", None) and isinstance(new_spec, dict):
                allowed = {p.get("name") for p in (cat.parameters or [])}
                unknown = [k for k in new_spec.keys() if k not in allowed]
                if unknown:
                    return ToolResult(
                        ok=False,
                        summary=(
                            f"spec 中包含 {cat.name_zh} 未定义的键: {unknown}。"
                            f"该类目允许的参数是: {sorted(allowed)}"
                        ),
                    )

        changed: list[str] = []
        label = label_of(node)
        if new_cat != "__missing__":
            old = node.category_id
            new = new_cat
            if old != new:
                node.category_id = new
                await record_edit(
                    self.db, bom_id=bom.id, node_id=node.id, node_label=label,
                    field=FIELD_CATEGORY, old_value=old, new_value=new,
                    user_name=self.user_name, source="agent",
                )
                changed.append("category_id")
        if new_spec != "__missing__":
            old_spec = dict(node.spec or {})
            # spec=None or {} → clear
            new_spec_dict = dict(new_spec) if isinstance(new_spec, dict) else {}
            if old_spec != new_spec_dict:
                node.spec = new_spec_dict
                await record_edit(
                    self.db, bom_id=bom.id, node_id=node.id, node_label=label,
                    field=FIELD_SPEC, old_value=old_spec, new_value=new_spec_dict,
                    user_name=self.user_name, source="agent",
                )
                changed.append("spec")

        await self.db.commit()
        if not changed:
            return ToolResult(ok=True, summary="无变化", mutated=False)
        return ToolResult(
            ok=True,
            summary=f"已更新 {label}: {', '.join(changed)}",
            mutated=True,
        )

    async def _t_bom_classify_all(self, args: dict[str, Any]) -> ToolResult:
        bom = await self._bom()
        force = bool(args.get("force"))

        classified: list[dict] = []
        unclassified: list[dict] = []
        skipped = 0

        for node in bom.nodes:
            if node.category_id and not force:
                skipped += 1
                continue
            cat_id, conf = heuristic_classify(
                part_name=node.part_name,
                part_number=node.part_number,
                description=node.description,
                notes=node.notes,
            )
            if cat_id is None:
                unclassified.append({
                    "node_id": node.id,
                    "part_name": node.part_name,
                    "confidence": conf,
                })
                continue
            old = node.category_id
            if old == cat_id:
                continue
            node.category_id = cat_id
            await record_edit(
                self.db, bom_id=bom.id, node_id=node.id, node_label=label_of(node),
                field=FIELD_CATEGORY, old_value=old, new_value=cat_id,
                user_name=self.user_name, source="agent",
            )
            classified.append({
                "node_id": node.id,
                "part_name": node.part_name,
                "category_id": cat_id,
                "confidence": conf,
            })

        await self.db.commit()
        n = len(classified)
        return ToolResult(
            ok=True,
            summary=(
                f"自动分类 {n} 个节点；{len(unclassified)} 个不确定保持未分类；"
                f"{skipped} 个已分类未动" + ("（force=true）" if force else "")
            ),
            data={
                "classified": classified,
                "unclassified": unclassified,
                "skipped": skipped,
            },
            mutated=n > 0,
        )
