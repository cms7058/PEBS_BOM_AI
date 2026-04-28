from __future__ import annotations

from io import BytesIO
from typing import Iterable

from openpyxl import Workbook

from app.models.bom import BOMNode

COLUMNS = [
    ("level", "Level"),
    ("part_number", "Part Number"),
    ("part_name", "Part Name"),
    ("description", "Description"),
    ("quantity", "Qty"),
    ("uom", "UoM"),
    ("material", "Material"),
    ("weight", "Weight"),
    ("supplier", "Supplier"),
    ("unit_cost", "Unit Cost"),
    ("notes", "Notes"),
]


def export_bom_xlsx(bom_name: str, nodes: Iterable[BOMNode]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = (bom_name or "BOM")[:31]

    ws.append([label for _, label in COLUMNS])
    for node in sorted(nodes, key=lambda n: n.sort_order):
        ws.append([getattr(node, key) for key, _ in COLUMNS])

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
