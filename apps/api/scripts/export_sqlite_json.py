from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def export_sqlite(sqlite_path: Path, output_path: Path) -> None:
    if not sqlite_path.exists():
        raise FileNotFoundError(f"SQLite database not found: {sqlite_path}")

    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row
    try:
        tables = [
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
            if row["name"] != "alembic_version"
        ]
        payload = {
            "source": str(sqlite_path),
            "tables": {
                table: [dict(row) for row in conn.execute(f'SELECT * FROM "{table}"')]
                for table in tables
            },
        }
    finally:
        conn.close()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export PEBS BOM SQLite data to JSON.")
    parser.add_argument("--sqlite", default="data/app.db", help="SQLite DB path under apps/api")
    parser.add_argument("--out", default="data/sqlite-export.json", help="Output JSON path")
    args = parser.parse_args()

    export_sqlite(Path(args.sqlite), Path(args.out))
    print(f"Exported SQLite data to {args.out}")


if __name__ == "__main__":
    main()
