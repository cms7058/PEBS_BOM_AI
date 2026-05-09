"""Rule-based risk evaluator for BOM nodes.

This is the lightweight "before-LLM" tier of risk detection: pure rules over
existing fields, runs in milliseconds, never makes external calls. Output
becomes input later when we layer LLM risk reasoning on top.

Tag taxonomy (codes are stable — UI/Agent rely on them):
  · uncategorized       — node has no category_id (Layer 1 gap)
  · unmapped            — node has no part_id (no standard material)
  · unknown_supplier    — neither node.supplier nor part.brand is set
  · orphan_part         — mapped to a Part used 0/1 times → no peer history
  · long_lead_time      — part.typical_lead_time string parses to ≥ 30 days
  · high_value          — quantity * unit_cost ≥ HIGH_VALUE_THRESHOLD
  · inactive_part       — mapped to a Part with status='inactive'/'pending'

Severity is intentionally coarse (info/warn/critical) — granular weighting
is the job of the LLM tier.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from app.models.bom import BOMNode, Part


HIGH_VALUE_THRESHOLD = 1000.0  # extended cost (qty × unit_cost) in default currency
LONG_LEAD_TIME_DAYS = 30


@dataclass
class RiskTag:
    code: str
    severity: str  # "info" | "warn" | "critical"
    message: str


def _parse_lead_time_days(value: str | None) -> int | None:
    """Best-effort extract day count from free-text lead time fields.

    Accepts forms like "30 天" / "4 周" / "2 months" / "30d". Returns None
    if no numeric token can be parsed.
    """
    if not value:
        return None
    text = value.strip().lower()
    m = re.search(r"(\d+)\s*(week|weeks|周|month|months|月|day|days|天|d|w)?", text)
    if not m:
        return None
    n = int(m.group(1))
    unit = (m.group(2) or "day").lower()
    if unit in {"week", "weeks", "周", "w"}:
        return n * 7
    if unit in {"month", "months", "月"}:
        return n * 30
    return n  # day or unspecified


def evaluate_node_risks(node: BOMNode, part: Part | None) -> list[RiskTag]:
    """Pure-function risk evaluation. `part` is the resolved standard material
    (None when node.part_id is null).

    Returns the *applicable* tags, ordered by severity (critical → warn →
    info). UI may show only the top 1-2 to avoid clutter.
    """
    tags: list[RiskTag] = []

    if not node.part_id:
        tags.append(RiskTag(
            code="unmapped",
            severity="warn",
            message="未映射到公司标准物料",
        ))

    if not (node.category_id or (part and part.category_id)):
        tags.append(RiskTag(
            code="uncategorized",
            severity="info",
            message="未分类，建议补充类目",
        ))

    supplier = (node.supplier or (part.brand if part else None) or "").strip()
    if not supplier:
        tags.append(RiskTag(
            code="unknown_supplier",
            severity="warn",
            message="未指定供应商或品牌",
        ))

    if part and (part.usage_count or 0) <= 1:
        # The current node likely accounts for the only usage. Means we have
        # no historical peer to learn supplier/brand/cost from.
        tags.append(RiskTag(
            code="orphan_part",
            severity="info",
            message=f"仅在 {part.usage_count or 0} 个 BOM 节点出现，缺少历史参考",
        ))

    if part and part.typical_lead_time:
        days = _parse_lead_time_days(part.typical_lead_time)
        if days is not None and days >= LONG_LEAD_TIME_DAYS:
            tags.append(RiskTag(
                code="long_lead_time",
                severity="warn",
                message=f"长货期（约 {days} 天）",
            ))

    qty = float(node.quantity or 0)
    unit_cost = float(node.unit_cost or 0) if node.unit_cost is not None else 0.0
    if qty and unit_cost and qty * unit_cost >= HIGH_VALUE_THRESHOLD:
        tags.append(RiskTag(
            code="high_value",
            severity="warn",
            message=f"高价值（合计 ¥{qty * unit_cost:,.0f}）",
        ))

    if part and part.status and part.status != "active":
        tags.append(RiskTag(
            code="inactive_part",
            severity="critical",
            message=f"标准物料状态为 {part.status}，不应继续使用",
        ))

    rank = {"critical": 0, "warn": 1, "info": 2}
    tags.sort(key=lambda t: rank.get(t.severity, 3))
    return tags


def severity_counts(items: Iterable[Iterable[RiskTag]]) -> dict[str, int]:
    """Aggregate severity counts across a list of per-node tag lists.

    Each node contributes at most once per severity level — a node with two
    'warn' tags counts as a single 'warn' for the BOM-level summary.
    """
    out = {"critical": 0, "warn": 0, "info": 0}
    for tags in items:
        seen: set[str] = set()
        for t in tags:
            if t.severity in out and t.severity not in seen:
                out[t.severity] += 1
                seen.add(t.severity)
    return out
