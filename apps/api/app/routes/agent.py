"""Agent streaming endpoint with tool-use loop.

Flow per turn:
  1. Stream assistant text + tool_use blocks from MiniMax M2.7
  2. If stop_reason == "tool_use", execute each tool, collect results
  3. Feed tool_results back as a new user message, re-stream
  4. Repeat until stop_reason == "end_turn" (or iteration cap)

SSE events emitted to frontend:
  - delta       { text }               text stream
  - tool_call   { name, args, summary, ok, mutated }   each tool execution
  - bom_updated {}                     after any mutation (frontend reloads BOM)
  - done        { reason }             conversation turn ended
  - error       { message }
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sse_starlette.sse import EventSourceResponse

from app.agent_tools import TOOLS, BOMToolExecutor
from app.config import settings
from app.db import get_db
from app.llm.base import ChatMessage, StreamOptions
from app.llm.registry import get_provider
from app.models.bom import BOM
from app.schemas import AgentChatRequest

router = APIRouter(prefix="/agent", tags=["agent"])


SYSTEM_PROMPT_TEMPLATE = """你是 BOM 编辑助手，帮用户查看和编辑一个 BOM（物料清单）结构。

可用工具（节点变化会实时同步到用户界面）：
- bom_list_nodes        查看节点（回答问题前先查，避免猜测）
- bom_add_node          新增节点或子节点
- bom_delete_node       删除节点（cascade=true 删子树）
- bom_update_node       修改字段（零件号/数量/材料/供应商等）
- bom_restyle_node      改单个节点整体样式（高亮/变灰/加角标等粗粒度）
- bom_restyle_by_rule   按规则批量改整体样式
- bom_describe_node     **列出节点上每个可视元素（slot）的当前状态和可改属性**
- bom_set_slot          **细粒度修改单个 slot（颜色/文本/绑定字段/显隐）**
- bom_set_slot_by_rule  按规则批量改某个 slot
- bom_move_node         改挂接关系（换父节点）

【节点视觉元素（slot）地图】
每个 BOM 节点是一张卡片，由以下 slot 组成：
  ┌─────────────────────────────────────────┐
  │ header (顶部小号)                         │
  │                                          │
  │ title (底部大号)        qty trend metric │
  │ ████progress██░░░░░░░░░░░░░░░░░░░░░░░░░ │
  └─────────────────────────────────────────┘
  · header / title / qty / metric / badge：可改 text、color、visible
    (header / title / metric 还可改 bound — 把该位置绑定到任意 BOM 字段)
  · trend / progress：可改 color、visible
  · card：可改 fill / stroke / lineWidth / opacity
  · 所有 slot 通用：visible=false 隐藏该元素

bound 可选字段：part_number / part_name / quantity / uom / material / supplier
  / unit_cost / notes / description / confidence_pct

【中文字段 → 工具参数对照表（必须严格遵守）】
- "零件号" / "编号" / "料号" / "PN"           → part_number
- "零件名" / "零件名称" / "名称"              → part_name
- "数量"                                      → quantity
- "单位"                                      → uom
- "材料" / "材质"                             → material
- "供应商"                                    → supplier
- "单价" / "成本"                             → unit_cost
- "描述" / "说明"                             → description
- "备注"                                      → notes

【关键执行规则】
1. **指令明确就立刻执行**——用户给出"把 X 的 Y 改成 Z"这种确定指令时，直接调用 bom_update_node 一次完成；不要反复"理解中""即将执行""请确认"。
2. **定位节点用工具，不要猜**——
   - 如果用户提到的零件名/编号在下方"BOM 概要"中能直接找到，请直接用对应的 8 位 id 前缀去比对完整 id（前缀只用于在概要里查找，调工具时必须传完整 id）。
   - 如果概要里看不全或找不到，先调 bom_list_nodes，找到唯一匹配再继续。
3. **part_name 与 part_number 可能相同**——常见于从 3D 文件导入的 BOM。修改 part_number 时仍只动 part_number 这一个字段，不要顺手改 part_name。
4. **bom_update_node 调用规则（极其重要）**：
   - 只在参数对象里包含真正要修改的字段；其它字段一律 **省略**，不要写出来
   - 字符串字段必须传字符串值，例如 part_number 写 "001"，**不要写 null**、不要写数字 1
   - 即使值看起来像纯数字（"001"、"01"），part_number 也必须传字符串，保留前导零
   - 用户要清空某字段时，使用 clear_fields 数组，如 clear_fields=["notes"]
   - **绝对不允许**：把字符串字段写成 null 来"占位"或"不修改"。"不修改"=不传这个字段
5. **不要修改用户没提及的字段**——只在 args 里包含真正要改的字段；其它字段（包括 part_name）不要传。
6. 破坏性操作（删除子树/批量改）执行前用一句话说明将做什么；普通字段修改不必确认。
7. 工具返回的 id 直接复用，不要自己编造 UUID。
8. 始终用中文简洁回答。
9. **样式编辑的两种粒度**：
   - 整张卡片层面（高亮 / 变灰 / 加角标 / 强调色 / 卡片底色）→ 用 bom_restyle_node。
   - 单个视觉元素（『把进度条改红色』『把 100% 改成显示供应商』）→ 用 bom_set_slot。
10. **样式引导式编辑流程**（用户表达样式意图但没说清要改哪里时触发）：
    a. 调 bom_describe_node 拿到该节点 slot 表
    b. 用 markdown 表格回复用户，列出每个 slot 的『名称 / 当前内容 / 可改属性』
       表格末尾追加：『请告诉我要改哪个 slot 的哪个属性，改成什么。』
    c. 收到用户回复后，调 bom_set_slot 执行
    d. 用户明确指了 slot 时（『把进度条颜色改成红色』），跳过 a/b 直接执行
11. 颜色用 hex 值（如 #F46649 红、#60C42D 绿、#1783FF 蓝、#DB9D0D 黄）。

【示例】
用户："把装配体1的零件号改成 001"
你的处理：（在概要中找到 part_name="装配体1"，记下其完整 id）→ 调用 bom_update_node(node_id="<完整 id>", part_number="001")
→ 回复："已将『装配体1』的零件号改为 001。"

用户："把基座的进度条改成红色"
你的处理：识别出明确指了 slot=progress，直接调 bom_set_slot(node_id="<基座 id>", slot="progress", attrs={"color":"#F46649"})
→ 回复："已将『基座』的进度条改为红色。"

用户："基座右下角那个 100% 改成显示供应商"
你的处理：识别为 metric slot，bound="supplier" → 调 bom_set_slot(node_id="<id>", slot="metric", attrs={"bound":"supplier"})
→ 回复："已把『基座』的右下数值改为绑定供应商字段。"

用户："改一下基座的样式"（意图模糊）
你的处理：先调 bom_describe_node(node_id="<基座 id>") 拿到 slot 表，然后用 markdown 表格回复：
  | slot | 当前内容 | 可改属性 |
  |---|---|---|
  | header (顶部小号) | BASE-001 | text / color / visible / bound |
  | title (底部大号) | 基座 | text / color / visible / bound |
  | qty (数量段) | × 1 EA | text / color / visible |
  | metric (右下数值) | 100% | text / color / visible / bound |
  | trend (上下三角) | ▲ | color / visible |
  | progress (底部进度条) | 绿 100% | color / visible |
  | badge (右上角) | 无 | text / color / visible |
  | card (卡片本身) | 白底灰边 | fill / stroke / lineWidth / opacity |

  请告诉我要改哪个 slot 的哪个属性，改成什么。

当前 BOM 概要（每行格式：完整 id | level | 零件号 | 零件名 | 数量·单位）：
{bom_summary}
"""


def _bom_summary(bom: BOM) -> str:
    nodes = sorted(bom.nodes, key=lambda n: n.sort_order)
    lines = [f"名称: {bom.name}", f"节点总数: {len(nodes)}"]
    # Show full id (so model can copy-paste it directly into tool calls)
    # plus per-field labels in the header — model previously confused
    # part_number vs part_name when they were equal (common for STEP imports).
    lines.append("节点列表：")
    cap = 60  # show more nodes than before so 50-node STEP files fit
    for n in nodes[:cap]:
        pn = n.part_number or "-"
        lines.append(
            f"  {n.id} | L{n.level} | 零件号={pn} | 零件名={n.part_name} | "
            f"{n.quantity}{n.uom}"
        )
    if len(nodes) > cap:
        lines.append(f"  ... 另 {len(nodes) - cap} 个节点未显示，可调 bom_list_nodes 查询")
    return "\n".join(lines)


@router.post("/chat")
async def chat(req: AgentChatRequest, db: AsyncSession = Depends(get_db)):
    q = select(BOM).where(BOM.id == req.bom_id).options(selectinload(BOM.nodes))
    bom = (await db.execute(q)).scalar_one_or_none()
    if not bom:
        raise HTTPException(404, "BOM not found")

    provider = get_provider()
    user_name = (req.user_name or "").strip() or "agent"
    executor = BOMToolExecutor(db=db, bom_id=req.bom_id, user_name=user_name)

    async def event_source() -> AsyncIterator[dict]:
        # Use .replace() not .format() — the prompt body legitimately contains
        # JSON-style examples with `{...}` braces (e.g. attrs={"color":"#F46649"})
        # which `.format()` would parse as fields and explode with KeyError.
        system_prompt = SYSTEM_PROMPT_TEMPLATE.replace(
            "{bom_summary}", _bom_summary(bom)
        )

        # Conversation history: prior turns from frontend (plain text, no tool blocks).
        messages: list[ChatMessage] = [
            ChatMessage(role=m["role"], content=m["content"]) for m in req.history
        ]
        messages.append(ChatMessage(role="user", content=req.message))

        MAX_TOOL_ROUNDS = 6
        any_mutation = False

        for _round in range(MAX_TOOL_ROUNDS):
            # Refresh BOM summary each round so the model sees mutations it made.
            q2 = select(BOM).where(BOM.id == req.bom_id).options(selectinload(BOM.nodes))
            fresh = (await db.execute(q2)).scalar_one()
            sys_prompt = SYSTEM_PROMPT_TEMPLATE.replace(
                "{bom_summary}", _bom_summary(fresh)
            )

            options = StreamOptions(
                model=settings.llm_model,
                messages=messages,
                system_prompt=sys_prompt,
                max_tokens=settings.llm_max_tokens,
                temperature=settings.llm_temperature,
                tools=TOOLS,
            )

            # Tell the frontend we're between calls so the UI can show a
            # "thinking" indicator even before the first text token arrives.
            phase = "思考中…" if _round == 0 else f"继续推理（第 {_round + 1} 轮）…"
            yield {"event": "status", "data": json.dumps({"phase": phase}, ensure_ascii=False)}

            accumulated_text = ""
            pending_tool_uses: list[dict[str, Any]] = []
            stop_reason: str | None = None
            errored = False

            async for evt in provider.stream(options):
                if evt.type == "text_delta" and evt.delta:
                    accumulated_text += evt.delta
                    yield {"event": "delta", "data": json.dumps({"text": evt.delta})}
                elif evt.type == "tool_use":
                    pending_tool_uses.append(
                        {"id": evt.id, "name": evt.name, "input": evt.input or {}}
                    )
                elif evt.type == "stop":
                    stop_reason = evt.reason
                elif evt.type == "error":
                    errored = True
                    yield {"event": "error", "data": json.dumps({"message": evt.message})}
                    break

            if errored:
                return

            # Build the assistant message that just streamed. Content is a list
            # of blocks (text + tool_use) so the model can reference tool_use_id
            # on the next turn via tool_result.
            assistant_blocks: list[dict[str, Any]] = []
            if accumulated_text:
                assistant_blocks.append({"type": "text", "text": accumulated_text})
            for tu in pending_tool_uses:
                assistant_blocks.append(
                    {
                        "type": "tool_use",
                        "id": tu["id"],
                        "name": tu["name"],
                        "input": tu["input"],
                    }
                )
            if assistant_blocks:
                messages.append(ChatMessage(role="assistant", content=assistant_blocks))

            if not pending_tool_uses:
                yield {"event": "done", "data": json.dumps({"reason": stop_reason or "end_turn"})}
                return

            # Execute tools, stream results
            tool_result_blocks: list[dict[str, Any]] = []
            for tu in pending_tool_uses:
                yield {
                    "event": "status",
                    "data": json.dumps(
                        {"phase": f"执行工具 {tu['name']}…"}, ensure_ascii=False
                    ),
                }
                result = await executor.dispatch(tu["name"], tu["input"])
                if result.mutated:
                    any_mutation = True

                yield {
                    "event": "tool_call",
                    "data": json.dumps(
                        {
                            "name": tu["name"],
                            "args": tu["input"],
                            "ok": result.ok,
                            "summary": result.summary,
                            "mutated": result.mutated,
                        },
                        ensure_ascii=False,
                    ),
                }

                content = result.summary
                if result.data is not None:
                    content += "\n\n" + json.dumps(result.data, ensure_ascii=False)
                tool_result_blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu["id"],
                        "content": content,
                        "is_error": not result.ok,
                    }
                )

            if any_mutation:
                yield {"event": "bom_updated", "data": "{}"}

            messages.append(ChatMessage(role="user", content=tool_result_blocks))

        yield {
            "event": "done",
            "data": json.dumps({"reason": "max_tool_rounds"}),
        }

    return EventSourceResponse(event_source())
