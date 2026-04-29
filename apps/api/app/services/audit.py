"""Helpers to write rows into the BOMNodeEdit audit log.

Every code path that mutates a BOM node should funnel through here so the
edit history captures who/when/what regardless of source (table, agent,
hierarchy rebuild, undo).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bom import BOMNode, BOMNodeEdit


# Special pseudo-fields for non-scalar mutations.
FIELD_CREATE = "__create__"
FIELD_DELETE = "__delete__"
FIELD_STYLE = "style"
FIELD_CATEGORY = "category_id"
FIELD_SPEC = "spec"


def stringify(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def label_of(node: BOMNode) -> str:
    return node.part_name or node.part_number or node.id[:8]


async def record_edit(
    db: AsyncSession,
    *,
    bom_id: str,
    node_id: str,
    node_label: str | None,
    field: str,
    old_value: Any,
    new_value: Any,
    user_name: str,
    source: str = "table",
) -> BOMNodeEdit:
    """Append a single audit row. Caller is responsible for db.commit()."""
    e = BOMNodeEdit(
        bom_id=bom_id,
        node_id=node_id,
        node_label=node_label,
        field=field,
        old_value=stringify(old_value),
        new_value=stringify(new_value),
        user_name=(user_name or "anonymous").strip()[:128] or "anonymous",
        source=source,
        created_at=datetime.utcnow(),
    )
    db.add(e)
    return e


async def record_field_changes(
    db: AsyncSession,
    *,
    bom_id: str,
    node: BOMNode,
    before: dict[str, Any],
    after: dict[str, Any],
    user_name: str,
    source: str,
) -> int:
    """For each key in `after`, if value changed vs `before`, write one row.

    Returns the number of audit rows written.
    """
    written = 0
    label = label_of(node)
    for k, new_v in after.items():
        old_v = before.get(k)
        if old_v == new_v:
            continue
        await record_edit(
            db,
            bom_id=bom_id,
            node_id=node.id,
            node_label=label,
            field=k,
            old_value=old_v,
            new_value=new_v,
            user_name=user_name,
            source=source,
        )
        written += 1
    return written
