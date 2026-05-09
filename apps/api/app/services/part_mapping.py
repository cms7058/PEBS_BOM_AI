from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bom import BOM, BOMNode, Part, PartAlias
from app.tenancy import current_tenant


# A "reference node" is any BOMNode that has already been confirmed-mapped
# to a Part. We use the full row (description, material, supplier, etc.) as
# a richer signal source than PartAlias alone — aliases only capture
# raw_name + raw_part_number, but real engineer-written BOMs carry more
# context that helps recognize the same component again later.
@dataclass
class ReferenceNode:
    bom_id: str
    bom_name: str | None
    node_id: str
    part_name: str | None
    part_number: str | None
    description: str | None
    material: str | None
    supplier: str | None
    notes: str | None
    category_id: str | None
    spec: dict[str, Any] = field(default_factory=dict)

    def text(self) -> str:
        return " ".join(
            str(v)
            for v in (
                self.part_name,
                self.part_number,
                self.description,
                self.material,
                self.supplier,
                self.notes,
            )
            if v
        )


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", "", value).lower()


# Token-level helpers — drive the "fuzzy" tier of scoring.
# We tokenize names and zero-noise tokens (single chars, pure digits) so a
# "Battery Pack 36V 10Ah" node can still score against a part named
# "Battery Pack" even though no substring is a literal subset.

# Stop tokens we don't want to give Jaccard credit for. Bare units / digits
# match too easily and create noise (everything with "10" looks alike).
_STOP_TOKENS = {
    "the", "and", "for", "of", "组", "件", "的", "和",
    "ea", "pcs", "set", "kg", "mm", "cm",
}


def _tokenize(value: str | None) -> set[str]:
    if not value:
        return set()
    # Split on non-alphanumeric (keeps Chinese chars together as one block).
    raw = re.split(r"[\s\W_]+", value.lower())
    out: set[str] = set()
    for tok in raw:
        if not tok or tok in _STOP_TOKENS:
            continue
        # Drop pure numeric / 1-char garbage tokens — they match too easily.
        if len(tok) <= 1 or tok.isdigit():
            continue
        out.add(tok)
    return out


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter == 0:
        return 0.0
    return inter / len(a | b)


def _shared_part_number_prefix(a: str | None, b: str | None) -> int:
    """Length of leading alpha+dash prefix shared by two part numbers.

    Catches "N-M5" vs "N-M4" (shared "N-M" → 3) and "ABC123" vs "ABC456"
    (shared "ABC" → 3). Returns 0 when no shared alpha prefix.
    """
    if not a or not b:
        return 0
    a = a.lower()
    b = b.lower()
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    # Require ≥ 2 alpha-ish chars to count — otherwise "1-2-3" / "1-9-0"
    # would share "1-" and trigger noise.
    prefix = a[:i]
    alpha = sum(1 for c in prefix if c.isalpha())
    if alpha < 2:
        return 0
    return i


def node_mapping_text(node: BOMNode) -> str:
    return " ".join(
        str(v)
        for v in (
            node.part_number,
            node.part_name,
            node.description,
            node.material,
            node.supplier,
            node.notes,
        )
        if v
    )


async def load_reference_nodes(
    db: AsyncSession, tenant_id: str
) -> dict[str, list[ReferenceNode]]:
    """Group every confirmed BOMNode by its part_id, tenant-scoped.

    Loaded once per suggestion call and reused across all parts so we don't
    re-query for each candidate.
    """
    rows = (
        await db.execute(
            select(
                BOMNode.part_id,
                BOMNode.bom_id,
                BOM.name,
                BOMNode.id,
                BOMNode.part_name,
                BOMNode.part_number,
                BOMNode.description,
                BOMNode.material,
                BOMNode.supplier,
                BOMNode.notes,
                BOMNode.category_id,
                BOMNode.spec,
            )
            .join(BOM, BOM.id == BOMNode.bom_id)
            .join(Part, Part.id == BOMNode.part_id)
            .where(Part.tenant_id == tenant_id, BOMNode.part_id.is_not(None))
        )
    ).all()
    out: dict[str, list[ReferenceNode]] = {}
    for r in rows:
        out.setdefault(r[0], []).append(
            ReferenceNode(
                bom_id=r[1],
                bom_name=r[2],
                node_id=r[3],
                part_name=r[4],
                part_number=r[5],
                description=r[6],
                material=r[7],
                supplier=r[8],
                notes=r[9],
                category_id=r[10],
                spec=r[11] or {},
            )
        )
    return out


def score_part_for_node(
    node: BOMNode,
    part: Part,
    aliases: list[PartAlias],
    *,
    reference_nodes: list[ReferenceNode] | None = None,
) -> tuple[float, str, ReferenceNode | None]:
    node_number = normalize_text(node.part_number)
    node_name = normalize_text(node.part_name)
    haystack = normalize_text(node_mapping_text(node))

    best = 0.0
    reasons: list[str] = []
    if node_number and normalize_text(part.part_number) == node_number:
        best = max(best, 0.96)
        reasons.append("零件号精确匹配")
    if node_name and normalize_text(part.name_standard) == node_name:
        best = max(best, 0.92)
        reasons.append("标准名称精确匹配")
    if part.part_number and normalize_text(part.part_number) in haystack:
        best = max(best, 0.86)
        reasons.append("节点文本包含标准零件号")
    if part.name_standard and normalize_text(part.name_standard) in haystack:
        best = max(best, 0.78)
        reasons.append("节点文本包含标准名称")

    for alias in aliases:
        raw_name = normalize_text(alias.raw_name)
        raw_number = normalize_text(alias.raw_part_number)
        if node_number and raw_number and raw_number == node_number:
            best = max(best, 0.94)
            reasons.append(f"历史别名零件号匹配：{alias.raw_part_number}")
        if node_name and raw_name and raw_name == node_name:
            best = max(best, 0.9)
            reasons.append(f"历史别名名称匹配：{alias.raw_name}")
        if raw_name and raw_name in haystack:
            best = max(best, 0.74)
            reasons.append(f"包含历史别名：{alias.raw_name}")

    # Fuzzy tier — only fire when no stronger reason has matched yet, so the
    # exact-match band still dominates the suggestion order. Caps at 0.72 so
    # auto-confirm thresholds (default 0.85) won't blanket-confirm fuzzy hits.
    if best < 0.72:
        node_tokens = _tokenize(node_mapping_text(node))
        part_tokens = _tokenize(" ".join(filter(None, [
            part.name_standard, part.part_number, part.brand, part.notes,
        ])))
        for alias in aliases:
            part_tokens |= _tokenize(" ".join(filter(None, [alias.raw_name, alias.raw_part_number])))
        jacc = _jaccard(node_tokens, part_tokens)
        shared = node_tokens & part_tokens
        # Two ways to qualify (either is enough):
        #  A. ≥ 2 real tokens overlap and Jaccard ≥ 0.2 — for descriptive
        #     names like "Battery Pack 36V" matching "Battery Pack".
        #  B. node's own tokens are mostly covered by the part — catches
        #     short names ("ROOT" vs alias "Root Assembly") where there's
        #     only one shared token but it's the node's entire signal.
        node_coverage = len(shared) / max(1, len(node_tokens))
        path_a = len(shared) >= 2 and jacc >= 0.2
        # Path B accepts short nodes only when the part covers ALL of the
        # node's tokens — otherwise "ROLLER ASSEMBLY" wrongly hits a part
        # whose only shared token is "assembly". Full coverage means the
        # node says nothing the part doesn't already represent.
        path_b = (
            len(shared) >= 1
            and len(node_tokens) <= 2
            and node_coverage >= 1.0
        )
        if path_a or path_b:
            # Score scales with Jaccard but also rewards full node coverage,
            # so a one-word node fully covered scores higher than a sparse
            # multi-word match.
            fuzzy = 0.55 + min(0.12, jacc * 0.4) + min(0.08, node_coverage * 0.1)
            if fuzzy > best:
                best = fuzzy
                reasons.append(
                    f"词元相似（共 {len(shared)} 词，Jaccard={jacc:.2f}，节点覆盖 {node_coverage:.0%}）"
                )

        # Same-category nudge — engineers' classification pre-filters by type,
        # so two parts in the same category share more semantic context than
        # text alone reveals. Only adds, never subtracts.
        if node.category_id and part.category_id and node.category_id == part.category_id:
            if best > 0:
                best = min(0.82, best + 0.08)
                reasons.append("同类目")
            elif len(shared) >= 1:
                best = max(best, 0.5)
                reasons.append("同类目 + 词元交集")

        # Part-number prefix overlap (N-M5 vs N-M4 etc.). Only credit when the
        # prefix is meaningful relative to total length; small contribution.
        if node.part_number and part.part_number:
            prefix_len = _shared_part_number_prefix(node.part_number, part.part_number)
            min_len = min(len(node.part_number), len(part.part_number))
            if prefix_len >= 3 and prefix_len >= min_len * 0.5:
                bumped = min(0.7, best + 0.08) if best > 0 else 0.55
                if bumped > best:
                    best = bumped
                    reasons.append(f"零件号前缀相同：{node.part_number[:prefix_len]}")

        # Cross-BOM reference-node match — see ReferenceNode docstring.
        # The Part's PartAlias rows only carry name+part_number, but real
        # confirmed BOMNodes also carry description / material / supplier /
        # category, so we get richer hits here than the alias loop above.
        # Capped at 0.78 so this can't beat exact-match suggestions.
        if reference_nodes:
            best_ref: tuple[float, str, ReferenceNode] | None = None
            for ref in reference_nodes:
                if ref.node_id == node.id:
                    continue  # don't recommend a node back to itself
                ref_tokens = _tokenize(ref.text())
                if not ref_tokens:
                    continue
                shared_r = node_tokens & ref_tokens
                if not shared_r:
                    continue
                jacc_r = _jaccard(node_tokens, ref_tokens)
                cov_r = len(shared_r) / max(1, len(node_tokens))
                path_a_r = len(shared_r) >= 2 and jacc_r >= 0.2
                path_b_r = (
                    len(shared_r) >= 1
                    and len(node_tokens) <= 2
                    and cov_r >= 1.0
                )
                if not (path_a_r or path_b_r):
                    continue
                ref_score = 0.55 + min(0.12, jacc_r * 0.4) + min(0.08, cov_r * 0.1)
                # Same category between node and the historical reference is
                # a strong "same kind of thing" signal.
                if (
                    node.category_id
                    and ref.category_id
                    and node.category_id == ref.category_id
                ):
                    ref_score = min(0.78, ref_score + 0.06)
                # spec key overlap — only contributes when the engineer has
                # actually filled in spec values on both sides.
                if node.spec and ref.spec:
                    common_keys = set(node.spec.keys()) & set(ref.spec.keys())
                    same_value = sum(
                        1 for k in common_keys if node.spec.get(k) == ref.spec.get(k)
                    )
                    if same_value >= 1:
                        ref_score = min(0.78, ref_score + 0.04 * same_value)
                bom_label = ref.bom_name or ref.bom_id[:8]
                node_label = ref.part_name or ref.part_number or ref.node_id[:8]
                reason = f"曾在 BOM「{bom_label}」中作为节点「{node_label}」映射到此物料"
                if best_ref is None or ref_score > best_ref[0]:
                    best_ref = (ref_score, reason, ref)
            if best_ref and best_ref[0] > best:
                best = best_ref[0]
                reasons.append(best_ref[1])
                ref_hit = best_ref[2]
            else:
                ref_hit = None
        else:
            ref_hit = None
    else:
        ref_hit = None

    return best, "；".join(reasons[:2]) or "文本相似", ref_hit


async def suggest_parts_for_node(
    db: AsyncSession,
    node: BOMNode,
    *,
    tenant_id: str | None = None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    tenant = tenant_id or current_tenant()
    aliases = (
        await db.execute(select(PartAlias).where(PartAlias.tenant_id == tenant))
    ).scalars().all()
    aliases_by_part: dict[str, list[PartAlias]] = {}
    for alias in aliases:
        aliases_by_part.setdefault(alias.part_id, []).append(alias)

    parts = (
        await db.execute(select(Part).where(Part.tenant_id == tenant))
    ).scalars().all()
    references = await load_reference_nodes(db, tenant)
    scored: list[dict[str, Any]] = []
    for part in parts:
        score, reason, ref = score_part_for_node(
            node,
            part,
            aliases_by_part.get(part.id, []),
            reference_nodes=references.get(part.id, []),
        )
        if score <= 0:
            continue
        scored.append({
            "part": part,
            "score": round(score, 3),
            "reason": reason,
            "reference": (
                {
                    "bom_id": ref.bom_id,
                    "bom_name": ref.bom_name,
                    "node_id": ref.node_id,
                    "node_label": ref.part_name or ref.part_number or ref.node_id[:8],
                }
                if ref
                else None
            ),
        })
    scored.sort(key=lambda item: item["score"], reverse=True)
    return scored[:limit]


async def add_alias_for_node(
    db: AsyncSession,
    *,
    part: Part,
    node: BOMNode,
    user_name: str,
    confidence: float = 1.0,
) -> PartAlias:
    tenant = part.tenant_id
    raw_name = (node.part_name or "").strip()
    raw_number = (node.part_number or "").strip() if node.part_number else None
    q = select(PartAlias).where(
        PartAlias.tenant_id == tenant,
        PartAlias.part_id == part.id,
        PartAlias.raw_name == raw_name,
    )
    if raw_number:
        q = q.where(
            or_(PartAlias.raw_part_number == raw_number, PartAlias.raw_part_number.is_(None))
        )
    existing = (await db.execute(q)).scalars().first()
    if existing:
        existing.status = "confirmed"
        existing.confidence = max(existing.confidence or 0, confidence)
        existing.confirmed_by = user_name
        existing.confirmed_at = datetime.utcnow()
        return existing
    alias = PartAlias(
        tenant_id=tenant,
        part_id=part.id,
        raw_name=raw_name,
        raw_part_number=raw_number,
        source_node_id=node.id,
        status="confirmed",
        confidence=confidence,
        confirmed_by=user_name,
        confirmed_at=datetime.utcnow(),
    )
    db.add(alias)
    return alias


def make_part_from_node(node: BOMNode, *, user_name: str, tenant_id: str | None = None) -> Part:
    return Part(
        tenant_id=tenant_id or current_tenant(),
        sku_internal=node.part_number,
        name_standard=node.part_name,
        part_number=node.part_number,
        category_id=node.category_id,
        brand=node.supplier,
        supplier=node.supplier,
        uom=node.uom,
        unit_cost=node.unit_cost,
        spec=dict(node.spec or {}),
        notes=node.notes,
        created_by=user_name,
    )
