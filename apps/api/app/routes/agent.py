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
- bom_list_nodes       查看节点（回答问题前先查，避免猜测）
- bom_add_node         新增节点或子节点
- bom_delete_node      删除节点（cascade=true 删子树）
- bom_update_node      修改字段（零件号/数量/材料/供应商等）
- bom_restyle_node     改单个节点视觉样式
- bom_restyle_by_rule  按规则批量改样式（如"所有外购件标红"）
- bom_move_node        改挂接关系（换父节点）

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

【示例】
用户："把装配体1的零件号改成 001"
你的处理：（在概要中找到 part_name="装配体1"，记下其完整 id）→ 调用 bom_update_node(node_id="<完整 id>", part_number="001")
→ 回复："已将『装配体1』的零件号改为 001。"

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
        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(bom_summary=_bom_summary(bom))

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
            sys_prompt = SYSTEM_PROMPT_TEMPLATE.format(bom_summary=_bom_summary(fresh))

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
