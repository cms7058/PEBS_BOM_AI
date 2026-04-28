"""Reconstruct parent-child links from a flat list ordered by sort_order + level."""

from __future__ import annotations

from typing import Iterable

from app.models.bom import BOMNode


def assign_parents(nodes: Iterable[BOMNode]) -> None:
    """Assign parent_id based on level transitions in document order."""
    stack: list[BOMNode] = []
    for node in sorted(nodes, key=lambda n: n.sort_order):
        while stack and stack[-1].level >= node.level:
            stack.pop()
        node.parent_id = stack[-1].id if stack else None
        stack.append(node)
