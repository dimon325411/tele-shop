"""Move the demo state (data/dev_state.pickle) into PostgreSQL.

    python scripts/pickle_to_sql.py data/dev_state.pickle > data/migration.sql
    docker compose exec -T postgres psql -U market -d market -v ON_ERROR_STOP=1 < data/migration.sql

The script only READS the pickle and prints SQL (standard library only, no database driver needed).
The SQL runs in one transaction, keeps all ids, resets the id sequences and refuses to run when the
database already has users (so it cannot mix two data sets). Apply the migrations first (alembic upgrade head).

Not moved: payments/refunds/audit (empty in personal mode). Job state, including legacy
digest/free-item markers converted by MemoryRepository.load, is preserved (migration 0009 required).
"""

from __future__ import annotations

import json
import sys
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.storage.memory import MemoryRepository  # noqa: E402

# column lists = schema after migrations 0001-0008 (only columns we fill; the rest keep their defaults)
COLUMNS = {
    "users": ["id", "telegram_id", "username", "first_name", "language", "timezone", "is_suspended",
              "suspended_reason", "trial_used", "bot_blocked", "notify_price_drops", "quiet_start_hour",
              "quiet_end_hour", "terms_accepted_at", "created_at", "last_seen_at", "deleted_at",
              "hide_new_sellers", "deal_city", "deal_radius_km", "free_alerts"],
    "plans": ["id", "code", "name", "price_stars", "duration_days", "max_active_searches", "allowed_sources",
              "min_interval_seconds", "max_notifications_per_day", "advanced_filters", "fuzzy_matching",
              "price_drop_alerts", "is_trial", "is_active", "sort_order"],
    "subscriptions": ["id", "user_id", "plan_id", "status", "origin", "starts_at", "expires_at", "payment_id",
                      "expiry_notified_at", "created_at"],
    "searches": ["id", "user_id", "name", "sources", "keywords", "alt_keywords", "excluded_keywords", "category",
                 "price_min", "price_max", "currency", "condition", "city", "region", "radius_km", "delivery",
                 "include_unknown", "fuzzy", "interval_seconds", "is_active", "paused_reason", "baseline_done",
                 "consecutive_failures", "last_checked_at", "last_success_at", "next_check_at", "created_at",
                 "match_description", "deal_min_discount"],
    "listings": ["id", "source", "external_id", "url", "title", "description", "price", "previous_price", "currency",
                 "city", "region", "condition", "category", "delivery_available", "latitude", "longitude",
                 "posted_at", "is_test", "first_seen_at", "last_seen_at", "price_changed_at", "seller_created_at",
                 "seller_is_business", "photo_url", "ai_note"],
    "match_results": ["id", "search_id", "listing_id", "user_id", "reasons", "matched_at"],
    "monitoring_runs": ["search_id", "source", "started_at", "finished_at", "status", "fetched", "new_listings",
                        "matched", "error"],
    "notifications": ["id", "user_id", "listing_id", "search_id", "kind", "dedup_key", "status", "attempts",
                      "next_attempt_at", "claimed_at", "last_error", "price_at_notify", "telegram_message_id",
                      "created_at", "sent_at", "payload"],
    "source_states": ["source", "enabled", "status", "disabled_reason", "blocked_until", "last_success_at",
                      "last_error_at", "last_error"],
    "job_state": ["key", "value", "updated_at"],
    "market_snapshots": ["search_id", "day", "variant", "count", "median", "p25", "p75", "min_price"],
    "listing_price_history": ["listing_id", "source", "external_id", "observed_at", "price", "currency", "kind"],
}
SEQUENCES = ["users", "plans", "subscriptions", "searches", "listings", "match_results", "monitoring_runs",
             "notifications"]
NOT_NULL_DEFAULTS = {"created_at", "first_seen_at", "last_seen_at", "next_attempt_at", "matched_at"}


def _array(items) -> str:
    out = []
    for item in items:
        s = str(item).replace("\\", "\\\\").replace('"', '\\"')
        out.append(f'"{s}"')
    return "{" + ",".join(out) + "}"


def cell(value) -> str:
    """One value in COPY text format."""
    if value is None:
        return r"\N"
    if isinstance(value, Enum):
        value = value.value
    if isinstance(value, bool):
        return "t" if value else "f"
    if isinstance(value, (datetime, date)):
        value = value.isoformat()
    elif isinstance(value, (list, tuple)):
        value = _array(value)
    elif isinstance(value, dict):
        value = json.dumps(value, ensure_ascii=False, default=str)
    else:
        value = str(value)
    return value.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")


def as_dict(obj) -> dict:
    return {f.name: getattr(obj, f.name) for f in fields(obj)} if is_dataclass(obj) else dict(obj)


def rows_for(repo: MemoryRepository) -> dict[str, list[dict]]:
    history_ids = {r["listing_id"] for r in repo.price_history}
    price_history = list(repo.price_history) + [
        {"listing_id": l.id, "source": l.source, "external_id": l.external_id,
         "observed_at": l.last_seen_at, "price": l.price, "currency": l.currency, "kind": "baseline"}
        for l in repo.listings.values() if l.price is not None and l.id not in history_ids
    ]
    searches = []
    for s in repo.searches.values():
        row = as_dict(s)
        row.update(as_dict(s.criteria))
        searches.append(row)
    matches = []
    for (sid, lid), m in sorted(repo.matches.items(), key=lambda kv: kv[1].get("seq", 0)):
        if sid not in repo.searches or lid not in repo.listings:
            continue
        listing = repo.listings[lid]
        matches.append({"id": m.get("seq"), "search_id": sid, "listing_id": lid, "user_id": m["user_id"],
                        "reasons": json.dumps(m.get("reasons") or [], ensure_ascii=False, default=str),
                        "matched_at": listing.first_seen_at})
    runs = [r for r in repo.runs if r.get("search_id") in repo.searches]
    return {
        "users": [as_dict(u) for u in repo.users.values()],
        "plans": [as_dict(p) for p in repo.plans.values()],
        "subscriptions": [as_dict(s) for s in repo.subscriptions.values()],
        "searches": searches,
        "listings": [as_dict(l) for l in repo.listings.values()],
        "listing_price_history": price_history,
        "match_results": matches,
        "monitoring_runs": runs,
        "notifications": [as_dict(n) for n in repo.notifications.values()
                          if n.listing_id in repo.listings and n.user_id in repo.users],
        "source_states": [as_dict(s) for s in repo.source_states.values()],
        "job_state": [{"key": k, "value": v, "updated_at": datetime.now().astimezone()}
                      for k, v in repo.job_state.items()],
        "market_snapshots": [dict(v) for v in repo.snapshots.values() if v.get("search_id") in repo.searches],
    }


def write_sql(repo: MemoryRepository, out) -> dict[str, int]:
    counts = {}
    out.write("-- generated by scripts/pickle_to_sql.py\nBEGIN;\n")
    out.write("DO $$ BEGIN IF EXISTS (SELECT 1 FROM users) THEN "
              "RAISE EXCEPTION 'database is not empty - refusing to import'; END IF; END $$;\n")
    out.write("DELETE FROM plans;\nDELETE FROM source_states;\n")  # seeded defaults -> replaced by the saved ones
    for table, rows in rows_for(repo).items():
        cols = COLUMNS[table]
        # skip columns whose value is missing in every row (defaults apply), keep NOT NULL-with-default columns out
        used = [c for c in cols if any(c in r for r in rows)]
        counts[table] = len(rows)
        if not rows:
            continue
        out.write(f"COPY {table} ({', '.join(used)}) FROM stdin;\n")
        for r in rows:
            values = []
            for c in used:
                v = r.get(c)
                if v is None and c in NOT_NULL_DEFAULTS:
                    v = r.get("first_seen_at") or r.get("created_at") or datetime.now().astimezone()
                values.append(cell(v))
            out.write("\t".join(values) + "\n")
        out.write("\\.\n")
    for table in SEQUENCES:
        out.write(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                  f"COALESCE((SELECT max(id) FROM {table}), 0) + 1, false);\n")
    out.write("COMMIT;\n")
    return counts


def main() -> None:
    src = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "data" / "dev_state.pickle")
    if not src.is_file():
        sys.exit(f"no state file: {src}")
    repo = MemoryRepository.load(src)
    counts = write_sql(repo, sys.stdout)
    print("exported: " + ", ".join(f"{k}={v}" for k, v in counts.items()), file=sys.stderr)


if __name__ == "__main__":
    main()
