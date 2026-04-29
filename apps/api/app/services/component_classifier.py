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
    "timing_belt_pulley": {
        "positive": [
            "同步带轮", "同步轮", "正时带轮", "皮带轮(同步)",
            "timing pulley", "timing belt pulley", "synchro pulley",
            r"\bHTD\d", r"\bMXL\b", r"\bGT\d",
            r"S[235]M-\d", r"S5M\b", r"S8M\b", r"AT[510]\b",
        ],
        "negative": ["皮带轮 V 带", "三角带轮"],
        "weight": 0.95,
    },
    "pneumatic_cylinder": {
        "positive": [
            "气缸", "笔形气缸", "薄型气缸", "导杆气缸", "无杆气缸",
            "三轴气缸", "夹爪气缸", "旋转气缸",
            "pneumatic cylinder", "air cylinder",
            r"\bSC\d", r"\bSI\d", r"\bCDQ", r"\bCQ2", r"\bMGP",
            r"\bCJ2", r"\bMHZ", r"\bCY1",
        ],
        "negative": ["液压缸", "hydraulic"],
        "weight": 1.0,
    },
    "encoder": {
        "positive": [
            "编码器", "光电编码器", "磁电编码器", "增量编码器",
            "绝对值编码器", "拉线编码器", "光栅尺",
            "encoder", "rotary encoder", "linear scale",
            r"\bE6B2", r"\bE6C", r"\bRENISHAW", r"\bROD\d",
            r"\bROC\d", r"\bECN\d", r"\bEQI\d",
        ],
        "negative": ["编码线", "条形码"],
        "weight": 0.95,
    },
    "proximity_sensor": {
        "positive": [
            "接近开关", "接近传感器", "光电开关", "光电传感器",
            "光电对射", "光电反射", "槽型光电", "电感传感器",
            "电容传感器", "磁性开关",
            "proximity sensor", "photoelectric", "photo sensor",
            r"\bE2E\b", r"\bE2EM\b", r"\bE3F\b", r"\bE3Z\b",
            r"\bM(?:8|12|18|30)\s*接近",
        ],
        # exclude limit switches & temperature sensors which look superficially similar
        "negative": ["行程开关", "限位开关", "温度传感器", "压力传感器", "encoder"],
        "weight": 0.95,
    },
    "gearbox": {
        "positive": [
            "减速机", "减速器", "行星减速", "谐波减速", "RV 减速",
            "蜗轮蜗杆减速", "斜齿减速", "摆线针轮减速", "齿轮箱",
            "gearbox", "gear reducer", "planetary gearbox",
            "harmonic drive",
            r"\bAB\d", r"\bAPEX\b", r"\bPLE\d", r"\bPGL\d",
        ],
        "negative": ["增速机", "变速箱(汽车)"],
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
        # Score = bumpy curve based on # of distinct keyword hits, NOT ratio
        # to total. Earlier ratio-based formula penalised categories with
        # large keyword lists (proximity_sensor has 16) — a real "接近开关"
        # match scored only 0.21 because hits/16 * 4 = 0.25 * 0.95.
        # New: 1 hit → 0.35, 2 hits → 0.65, 3+ hits → 0.85, capped at 1.0,
        # then multiplied by category weight.
        per_hit = [0.35, 0.65, 0.85, 0.95, 1.0]
        base = per_hit[min(hits - 1, len(per_hit) - 1)]
        score = min(1.0, base * rules["weight"])
        if score > best_score:
            best_score = score
            best_id = cat_id

    # Threshold: < 0.25 means too weak to commit
    if best_score < 0.25:
        return None, best_score

    return best_id, round(best_score, 2)
