"""LLM-driven field mapping + BOM normalization.

Given raw spreadsheet rows, ask MiniMax M2.7 to produce a normalized BOM
in our canonical schema. The LLM returns JSON only; we tolerate minor
formatting issues via a tolerant extractor.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.config import settings
from app.llm.minimax_plan import MiniMaxPlanProvider
from app.llm.registry import get_provider

SYSTEM_PROMPT = """你是一个制造业 BOM (物料清单) 规范化助手。
输入是用户上传的表格原始数据（表头 + 每行字典）。
你的任务：把它映射到标准 BOM schema，推断层级关系，并输出严格 JSON。

标准字段：
- level (int, 0=顶层)
- part_number (零件号/图号)
- part_name (零件名称，必填)
- description
- quantity (float, 默认 1)
- uom (单位，默认 "EA")
- material (材料)
- weight (kg)
- supplier
- unit_cost
- notes
- confidence (0-1，你对这行映射的置信度)
- source_row (原始表格行号，从 _row 字段取)

层级推断规则：
1. 若表格有明显的层级列（如 "Level"/"层级"/"1.1.2" 编号），按该列推断。
2. 若有缩进或"装配/组件/零件"分类列，据此推断。
3. 否则全部视为 level=1，level=0 为用户填的项目名占位。

输出格式（严格 JSON，不要 markdown code fence，不要多余文字）：
{
  "bom_name": "<从表头或文件名猜测的项目名>",
  "nodes": [ { ...字段... }, ... ]
}
"""


def _extract_json(text: str) -> dict[str, Any]:
    # strip ```json fences if model slipped one in
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    return json.loads(text)


async def normalize_spreadsheet_to_bom(
    raw: dict[str, Any], filename: str
) -> dict[str, Any]:
    provider = get_provider()
    if not isinstance(provider, MiniMaxPlanProvider):
        raise RuntimeError("Only minimaxPlan provider is wired for normalization in P0")

    user_prompt = (
        f"文件名: {filename}\n"
        f"表头: {raw['headers']}\n"
        f"行数: {raw['row_count']}\n"
        f"数据 (JSON):\n{json.dumps(raw['rows'], ensure_ascii=False)}\n\n"
        "请输出标准 BOM JSON。"
    )

    text = await provider.complete_json(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        model=settings.llm_model,
        max_tokens=settings.llm_max_tokens,
    )

    try:
        parsed = _extract_json(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"LLM 返回非法 JSON: {exc}\n原文前 500 字: {text[:500]}") from exc

    nodes_in = parsed.get("nodes", [])
    nodes_out: list[dict[str, Any]] = []
    for i, n in enumerate(nodes_in):
        nodes_out.append(
            {
                "level": int(n.get("level", 1) or 1),
                "part_number": _s(n.get("part_number")),
                "part_name": _s(n.get("part_name")) or f"Part {i+1}",
                "description": _s(n.get("description")),
                "quantity": float(n.get("quantity") or 1),
                "uom": _s(n.get("uom")) or "EA",
                "material": _s(n.get("material")),
                "weight": _f(n.get("weight")),
                "supplier": _s(n.get("supplier")),
                "unit_cost": _f(n.get("unit_cost")),
                "notes": _s(n.get("notes")),
                "confidence": float(n.get("confidence") or 0.8),
                "source_ref": {"type": "excel_row", "row": n.get("source_row")}
                if n.get("source_row") is not None
                else None,
                "sort_order": i,
            }
        )

    return {"bom_name": parsed.get("bom_name") or filename, "nodes": nodes_out}


def _s(v: Any) -> str | None:
    if v is None or v == "":
        return None
    return str(v)


def _f(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
