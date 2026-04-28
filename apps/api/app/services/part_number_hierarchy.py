"""Detect and apply BOM hierarchy from part-number patterns.

Example part numbers:
    A-001          (top)
    A-001-01       (child of A-001)
    A-001-01-1     (child of A-001-01)
    S-M4-10        (separate top — no match with A-001 prefix)

Algorithm:
    1. Detect the dominant separator across all part numbers
       (candidates: - . _ / · ·)
    2. For each node with a part number, find the longest prefix (with trailing
       separator stripped) that matches another node's full part number;
       that's the parent.
    3. Nodes with no prefix match become top level.

Returned detection info lets the frontend show the inferred rule and let the
user override the separator before re-applying.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Sequence

from app.models.bom import BOMNode

CANDIDATE_SEPARATORS = ["-", ".", "_", "/", "·"]


@dataclass
class HierarchyRule:
    separator: str
    confidence: float  # 0-1
    sample_chains: list[list[str]]  # a few detected parent→child chains
    orphan_count: int                # nodes with no detectable parent


def detect_rule(nodes: Sequence[BOMNode]) -> HierarchyRule:
    pns = [n.part_number for n in nodes if n.part_number]
    if not pns:
        return HierarchyRule(separator="-", confidence=0.0, sample_chains=[], orphan_count=0)

    best: tuple[float, str, list[list[str]], int] = (0.0, "-", [], len(pns))

    for sep in CANDIDATE_SEPARATORS:
        multi_seg = [p for p in pns if sep in p]
        if len(multi_seg) < 2:
            continue
        # Coverage: fraction of part numbers that use this separator
        coverage = len(multi_seg) / len(pns)
        # How many nodes can find a parent using this separator?
        pn_set = set(pns)
        matched_chains: list[list[str]] = []
        matched = 0
        orphans = 0
        for pn in multi_seg:
            parents = _find_parent_pn(pn, pn_set, sep)
            if parents:
                matched += 1
                if len(matched_chains) < 3:
                    matched_chains.append([parents, pn])
            else:
                orphans += 1

        match_ratio = matched / len(multi_seg)
        # score favors coverage + match ratio
        score = 0.5 * coverage + 0.5 * match_ratio
        if score > best[0]:
            best = (score, sep, matched_chains, orphans + (len(pns) - len(multi_seg)))

    confidence, sep, chains, orphans = best
    return HierarchyRule(
        separator=sep, confidence=confidence, sample_chains=chains, orphan_count=orphans
    )


def _find_parent_pn(pn: str, all_pns: set[str], sep: str) -> str | None:
    """Return the longest proper prefix of pn (split by sep) that exists in all_pns."""
    parts = pn.split(sep)
    for i in range(len(parts) - 1, 0, -1):
        candidate = sep.join(parts[:i])
        if candidate in all_pns and candidate != pn:
            return candidate
    return None


def apply_rule(nodes: Iterable[BOMNode], separator: str) -> dict[str, int]:
    """Mutate nodes' parent_id + level based on the separator rule.

    Rules:
    - Sorts nodes deterministically by part_number (None last), so parents exist
      before we assign children (needed for level computation).
    - If a node has no part_number, leaves parent_id as-is but resets level
      based on whatever parent it has.
    - Returns summary: {top_level, linked, orphans, no_partnumber}
    """
    nodes = list(nodes)
    by_pn: dict[str, BOMNode] = {}
    for n in nodes:
        if n.part_number:
            by_pn[n.part_number] = n

    # reset
    for n in nodes:
        n.parent_id = None

    # two passes: assign parent_id by prefix match, then compute level by walking up
    pn_set = set(by_pn.keys())
    linked = 0
    orphans = 0
    no_partnumber = 0

    for n in nodes:
        if not n.part_number:
            no_partnumber += 1
            continue
        if separator not in n.part_number:
            continue
        parent_pn = _find_parent_pn(n.part_number, pn_set, separator)
        if parent_pn:
            n.parent_id = by_pn[parent_pn].id
            linked += 1
        else:
            orphans += 1

    # Compute levels via BFS from top-level nodes
    children_of: dict[str | None, list[BOMNode]] = {}
    for n in nodes:
        children_of.setdefault(n.parent_id, []).append(n)

    top_level = children_of.get(None, [])
    for n in top_level:
        n.level = 0
    queue: list[BOMNode] = list(top_level)
    while queue:
        parent = queue.pop(0)
        for child in children_of.get(parent.id, []):
            child.level = parent.level + 1
            queue.append(child)

    # stable sort order: group children under parents, keep original order within siblings
    _renumber_sort(nodes, children_of)

    return {
        "top_level": len(top_level),
        "linked": linked,
        "orphans": orphans,
        "no_partnumber": no_partnumber,
    }


def _renumber_sort(nodes: list[BOMNode], children_of: dict) -> None:
    """Pre-order traversal to assign sort_order so children immediately follow parent."""
    order = [0]
    visited: set[str] = set()

    def visit(n: BOMNode) -> None:
        n.sort_order = order[0]
        order[0] += 1
        visited.add(n.id)
        for c in children_of.get(n.id, []):
            visit(c)

    for top in children_of.get(None, []):
        visit(top)

    # any disconnected nodes (shouldn't happen after apply, but safety)
    for n in nodes:
        if n.id not in visited:
            n.sort_order = order[0]
            order[0] += 1


# Handy for tests
def clean_pn(pn: str) -> str:
    return re.sub(r"\s+", "", pn)
