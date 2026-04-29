"""Heuristic non-std component classifier.

Maps a free-form part_name / description to a component_categories.id by
keyword matching. Conservative — returns None when uncertain so the agent
(or human) can decide rather than mis-tagging.

Why heuristic and not LLM:
- Deterministic, no token cost, no latency
- 5 categories with strong keyword discriminators — heuristic is enough
- LLM can still be invoked by the agent's own reasoning when the heuristic
  declines (the bom_classify_all tool returns the unclassified list back
  to the agent for follow-up classification with bom_classify_node).

When more categories arrive, switch to a hybrid: heuristic for high-
confidence cases, LLM for the long tail.
"""

from __future__ import annotations

import re

# Per-category positive keywords (Chinese + English + common abbrevs).
# A node matches a category when ANY positive keyword is present and no
# `negative` keyword is. Order in dict doesn't matter; ranking by match
# strength happens at scoring time.
_RULES: dict[str, dict] = {
    "linear_guide": {
        "positive": [
            "直线导轨", "线性导轨", "导轨滑块", "滚动导轨",
            "linear guide", "linear motion guide",
            r"\bLM\b", r"\bLG\b", r"\bHGW?\d", r"\bMGN\d", r"\bSSR\d",
        ],
        "negative": ["导轨油", "导轨润滑"],
        "weight": 1.0,
    },
    "ball_screw": {
        "positive": [
            "滚珠丝杠", "滚珠丝杆", "滚珠螺杆",
            "ball screw", "ballscrew",
            r"\bBNK?\d", r"\bSFU\d", r"\bSFS\d", r"\bDFU\d",
        ],
        "negative": [],
        "weight": 1.0,
    },
    "aluminum_extrusion": {
        "positive": [
            "铝型材", "工业铝", "铝合金型材", "型材",
            "aluminum extrusion", "aluminium extrusion", "extrusion profile",
            # Common cross-section codes appearing in BOM names
            r"\b(20|30|40|45|50|60|80|90|100)(20|30|40|45|50|60|80|90|120|160)\b",
            r"\b4040\b", r"\b4080\b", r"\b3030\b", r"\b6060\b",
        ],
        "negative": ["铝板", "铝棒", "铝管"],
        "weight": 0.9,  # slightly lower because cross-section regex can false-positive
    },
    "dowel_pin": {
        "positive": [
            "定位销", "精密销", "基准销", "导向销",
            "dowel pin", "dowel", "locating pin",
            r"销.*g6", r"销.*h7",
        ],
        "negative": ["销轴(轴)?$", "圆柱销 GB"],   # exclude ordinary GB pins
        "weight": 0.95,
    },
    "coupling": {
        "positive": [
            "联轴器", "联轴节", "梅花联轴", "膜片联轴", "波纹管联轴",
            "刚性联轴", "弹性联轴", "十字滑块联轴",
            "coupling", "shaft coupling", "flexible coupling",
            r"\bMJC\b", r"\bMOR\b",
        ],
        "negative": ["油管接头", "气管接头"],
        "weight": 1.0,
    },
}


def _make_haystack(node_name: str, part_number: str | None,
                   description: str | None, notes: str | None) -> str:
    parts = [node_name or "", part_number or "", description or "", notes or ""]
    return " ".join(p for p in parts if p).lower()


def classify(
    part_name: str,
    part_number: str | None = None,
    description: str | None = None,
    notes: str | None = None,
) -> tuple[str | None, float]:
    """Return (category_id, confidence) where confidence is 0..1.

    None category means we couldn't determine — caller should leave
    the node unclassified.
    """
    text = _make_haystack(part_name, part_number, description, notes)
    if not text.strip():
        return None, 0.0

    best_id: str | None = None
    best_score: float = 0.0

    for cat_id, rules in _RULES.items():
        # Reject if any negative keyword present
        if any(re.search(neg, text, re.IGNORECASE) for neg in rules["negative"]):
            continue
        # Count positive matches
        hits = sum(
            1 for pat in rules["positive"]
            if re.search(pat, text, re.IGNORECASE)
        )
        if hits == 0:
            continue
        # Score = (hits / total_positives) * weight, capped at 1
        score = min(1.0, (hits / max(1, len(rules["positive"])) * 4) * rules["weight"])
        if score > best_score:
            best_score = score
            best_id = cat_id

    # Threshold: < 0.25 means too weak to commit
    if best_score < 0.25:
        return None, best_score

    return best_id, round(best_score, 2)
