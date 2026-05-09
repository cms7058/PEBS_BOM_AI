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
from app.llm.registry import get_provider_for_model, list_models
from app.models.bom import BOM
from app.schemas import AgentChatRequest

router = APIRouter(prefix="/agent", tags=["agent"])


@router.get("/models")
async def models() -> dict[str, Any]:
    """List the LLM models the user can pick from in the chat sidebar.
    Filters out entries whose API key isn't configured so the UI never
    surfaces a model that 500s on first use."""
    return {"models": list_models(), "default": settings.llm_model}


SYSTEM_PROMPT_TEMPLATE = """你是 BOM 编辑助手，帮用户查看和编辑一个 BOM（物料清单）结构。

【身份回答规则】
用户问"你现在是什么模型"、"你是什么模型"、"用的什么模型"、"你是谁开发的"等身份/模型问题时，不要给出具体底层模型名称，必须按下面内容回答：
我是 PEBS 开发的 AI 助手。当前在这个对话中我的角色是 **BOM 编辑助手**，专门帮你查看和编辑物料清单结构。我能正常调用所有 BOM 相关工具，随时帮你干活。需要我帮你处理这个 BOM 吗?

不要自称 Claude、Anthropic、OpenAI、DeepSeek 或其它底层模型/厂商。

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
- component_categories_list  列出非标件类目（直线导轨/丝杠/铝型材/定位销/联轴器…）
- bom_classify_node     单个节点归类到某 category_id 并填 spec（结构化参数）
- bom_classify_all      批量自动分类（启发式，不确定的节点保持未分类）
- part_suggest_mappings 为节点查找可能匹配的公司标准物料
- part_list             查询公司现有标准物料库
- part_draft_from_text  把用户粘贴的多行物料文本解析成待确认导入草案
- part_confirm_mapping  用户确认后，把节点映射到已有标准物料
- part_create_from_node 用户确认新建后，基于节点创建标准物料并映射
- part_reject_mapping   用户跳过/否定候选时，标记为暂不映射
- brand_add             录入用户私有品牌知识库（『我们用 X 牌』时调用）
- brand_bulk_add        **批量** 录入（用户粘贴 AVL 表格 / Excel 选区时调用）
- brand_list            列出当前租户已录入的品牌
- brand_recommend       为某类目推荐品牌（私有 KB 优先，无则提示用户录入）
- brand_remove / brand_update  删除/修改私有品牌条目

【非标件分类（component category）流程】
- 用户说『分类一下』『识别非标件』『打类目』→ 先调 bom_classify_all
  返回中的 unclassified 列表是 agent 进一步处理的对象
- 对 unclassified 中的件，结合 part_name / description 用你自己的判断
  调 bom_classify_node 一一处理；不确定的就在回复里告诉用户，让 ta 决定
- 设 spec 时键名必须严格匹配该 category 的 parameters[].name；不确定参数
  时先只设 category_id，spec 留空
- 设完 category_id，前端 BOM 卡片上的"右下数值"slot 会显示类目名，
  用户在图谱上能直接看到分类结果

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
  / unit_cost / notes / description / confidence_pct / category

【metric slot 默认显示规则】
  · 节点未分类（category_id=null）→ 显示 confidence%（带上下三角）
  · 节点已分类 → 显示类目中文名（"直线导轨" 等，蓝色，无三角）
  · 用户用 bom_set_slot 显式设了 text 或 bound → 优先该设置

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

【品牌知识库流程】
12. **物料映射是确认优先**：
    - 当前端把用户选中的节点上下文放进对话（如『当前选中节点...未映射』）时，
      先调 part_suggest_mappings 查看候选。
    - 如果已有候选，只向用户解释候选和原因，询问『选第几个 / 新建 / 跳过』；
      不要替用户自动确认。
    - 用户回复『选第一个』『用上银那个』『确认这个』等明确确认时，
      调 part_confirm_mapping。
    - 用户回复『新建』『作为新物料保存』时，调 part_create_from_node。
    - 用户回复『跳过』『先不映射』『都不是』时，调 part_reject_mapping。
    - 所有确认结果会写入公司标准物料库和别名，后续类似 BOM 可复用。
    - 用户查询『公司现有物料』『标准物料库』『已有物料』时，调 part_list；
      不要在回复正文列出物料明细，不要用编号清单复述物料名。只用一句话说明
      已生成可查看清单；前端会在对话里提供『点击查看』入口、图表和工作台内部数据视图。
    - 用户粘贴多行标准物料、供应商物料清单、采购台账片段，并要求导入/加入标准物料库时，
      调 part_draft_from_text。它只生成导入草案，不直接入库；用户需要在前端预览后确认。
    - 用户说『返回 BOM 详情页』『回到工作台』『返回上一页』时，不要调用工具；
      简短确认即可，前端会处理页面切换。
13. **录入触发词**：用户说『我们用 X 牌』『我们供应商是 Y』『把 Z 加到品牌库』
    → 调 brand_add。如果用户提到的类目不在 component_categories 里，
    先告诉用户当前可选类目，让 ta 决定要哪个。
14. **推荐触发词**：用户说『X 类目有哪些选择』『推荐几个 Y 厂家』『给我 X 的品牌』
    → 先调 brand_recommend(category_id=X)。返回三档可信度：
       ★★ private  ←  当前客户自己录入的（最高优先）
       ★  shared   ←  自家共享出来的
       ·  community ←  其他客户共享给社区的
    回复时**带溯源标记**，让用户一眼看出哪些是自家 KB 哪些是社区共享。
15. 如果 brand_recommend 返回 recommendations 为空（KB 还没录），
    把 fallback_brands（类目自带的通用品牌列表）作为参考给用户，
    并主动建议：『要不要把你们常用的品牌加进 KB？以后我能直接帮你优先推荐。』
16. **不要把 LLM 的通用品牌知识当成私有推荐展示**——明确说明这只是通用参考。
17. **批量录入触发词**：用户在消息里粘贴**多行表格状内容**（一般 ≥ 3 行，
    每行能识别出品牌名 + 至少一个属性），无论分隔符是制表符 / 逗号 / 竖线 /
    多空格，都先尝试解析成 rows 字典数组，再调 brand_bulk_add 一次性入库。
    流程：
      a) 推断列含义（哪一列是品牌名/类目/地区/价位/账期…）
      b) 类目列里出现的中文（『直线导轨』『丝杠』）映射到 component_categories.id；
         不能映射的就在该行 categories 留 [] 数组
      c) 调 brand_bulk_add，回复时给出 inserted / merged / rejected 统计；
         rejected 行要列原因，请求用户补全或确认放弃
      d) 如果识别到的行 < 3 或不像表格，回退用 brand_add 单条录入
    示例：用户粘贴
        HIWIN 上银 | 直线导轨,丝杠 | 台湾 | 中端
        雅威达 | 直线导轨 | 浙江温岭 | 国产高端 | 账期30天
        SMC | 气缸 | 日本 | 中端
    → 你解析为 3 个 rows，调 brand_bulk_add 一次入库

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
        mapping = n.mapping_status or ("confirmed" if n.part_id else "unmapped")
        lines.append(
            f"  {n.id} | L{n.level} | 零件号={pn} | 零件名={n.part_name} | "
            f"{n.quantity}{n.uom} | 映射={mapping}"
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

    # Resolve model + provider from the request (or fall back to default).
    # Returning a clear error here is better than letting an unknown model
    # explode 6 layers down inside the SDK.
    try:
        provider, real_model_name = get_provider_for_model(req.model)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    user_name = (req.user_name or "").strip() or "agent"
    executor = BOMToolExecutor(db=db, bom_id=req.bom_id, user_name=user_name)

    async def event_source() -> AsyncIterator[dict]:
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
                model=real_model_name,
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

            # Used to throttle thinking-phase updates — bursting thinking_delta
            # to the SSE stream every few ms is wasteful; once per ~500 chars
            # is enough for the UI heartbeat.
            thinking_buffer = ""
            last_phase_emit = 0
            async for evt in provider.stream(options):
                if evt.type == "text_delta" and evt.delta:
                    accumulated_text += evt.delta
                    yield {"event": "delta", "data": json.dumps({"text": evt.delta})}
                elif evt.type == "thinking_delta" and evt.delta:
                    thinking_buffer += evt.delta
                    # Emit phase update every 500 chars or so. Trim to last
                    # 80 chars so it stays one-line readable.
                    if len(thinking_buffer) - last_phase_emit >= 500:
                        last_phase_emit = len(thinking_buffer)
                        preview = thinking_buffer[-80:].replace("\n", " ")
                        yield {
                            "event": "status",
                            "data": json.dumps(
                                {"phase": f"思考中…{preview}"},
                                ensure_ascii=False,
                            ),
                        }
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
            # DeepSeek reasoning models require the prior assistant
            # reasoning_content to be included when continuing after tool calls.
            # Keep this provider-scoped so Anthropic-compatible providers don't
            # receive unknown content block types.
            if thinking_buffer and getattr(provider, "provider_id", "") == "deepseek":
                assistant_blocks.append({"type": "reasoning", "text": thinking_buffer})
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
                            "data": result.data,
                        },
                        ensure_ascii=False,
                    ),
                }

                content = result.summary
                # Large material-master payloads are for the frontend view
                # renderer only. Do not feed them back into the LLM context:
                # otherwise the model may spend tokens repeating long lists in
                # chat as the private material library grows.
                ui_only_data_tools = {"part_list"}
                if result.data is not None and tu["name"] not in ui_only_data_tools:
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

    # ping every 15s keeps proxies (and the user's browser fetch reader)
    # from killing the connection during long thinking phases on big prompts.
    return EventSourceResponse(event_source(), ping=15)
