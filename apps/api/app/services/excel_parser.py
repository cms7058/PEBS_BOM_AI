"""Raw Excel/CSV parsing. Produces a dict-per-row payload for the LLM normalizer."""

from __future__ import annotations

from io import BytesIO
from typing import Any

import pandas as pd


def parse_spreadsheet(filename: str, data: bytes) -> dict[str, Any]:
    suffix = filename.lower().rsplit(".", 1)[-1]
    buf = BytesIO(data)
    if suffix in ("xlsx", "xls", "xlsm"):
        df = pd.read_excel(buf, sheet_name=0, dtype=object)
    elif suffix == "csv":
        df = pd.read_csv(buf, dtype=object)
    else:
        raise ValueError(f"Unsupported file type: {suffix}")

    df = df.fillna("")
    headers = [str(c) for c in df.columns]
    rows: list[dict[str, Any]] = []
    for idx, raw_row in df.iterrows():
        row = {h: _stringify(raw_row[h]) for h in headers}
        row["_row"] = int(idx) + 2  # +2 to match spreadsheet row numbers (header = 1)
        rows.append(row)

    return {"headers": headers, "rows": rows, "row_count": len(rows)}


def _stringify(v: Any) -> Any:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v
