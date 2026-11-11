"""Export aggregate daily analytics from PostgreSQL, without Telegram IDs or listing details.

Run: python scripts/export_analytics.py data/reports
The two CSV files can be imported into Google Sheets. DATABASE_URL comes from .env.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.config import get_settings  # noqa: E402

SNAPSHOTS = """
SELECT s.day, s.search_id, q.name AS search_name, s.variant, s.count AS listing_count,
       s.median, s.p25, s.p75, s.min_price
FROM market_snapshots s JOIN searches q ON q.id = s.search_id
ORDER BY s.day DESC, q.name, s.variant
"""

PRICE_CHANGES = """
SELECT (h.observed_at AT TIME ZONE 'Europe/Warsaw')::date AS day,
       m.search_id, q.name AS search_name, count(*) AS price_changes,
       count(*) FILTER (WHERE h.price < previous.price) AS price_drops,
       count(*) FILTER (WHERE h.price > previous.price) AS price_rises
FROM listing_price_history h
JOIN LATERAL (
    SELECT h2.price FROM listing_price_history h2
    WHERE h2.listing_id = h.listing_id AND h2.id < h.id
    ORDER BY h2.id DESC LIMIT 1
) previous ON true
JOIN match_results m ON m.listing_id = h.listing_id
JOIN searches q ON q.id = m.search_id
WHERE h.kind = 'change'
GROUP BY 1, 2, 3
ORDER BY 1 DESC, 3
"""


def export(output_dir: Path, database_url: str) -> dict[str, int]:
    output_dir.mkdir(parents=True, exist_ok=True)
    dsn = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    counts = {}
    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as conn:
        for name, query, columns in (
            ("snapshots", SNAPSHOTS, ["day", "search_id", "search_name", "variant", "listing_count", "median", "p25", "p75", "min_price"]),
            ("price_changes", PRICE_CHANGES, ["day", "search_id", "search_name", "price_changes", "price_drops", "price_rises"]),
        ):
            rows = conn.execute(query).fetchall()
            target = output_dir / f"{name}.csv"
            with target.open("w", newline="", encoding="utf-8-sig") as file:
                writer = csv.DictWriter(file, fieldnames=columns)
                writer.writeheader()
                writer.writerows(rows)
            counts[name] = len(rows)
    return counts


if __name__ == "__main__":
    directory = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "data" / "reports")
    print(export(directory, get_settings().database_url))
