"""Behavioural checks of the critical SQL statements on a scratch PostgreSQL database.

Usage:  python scripts/check_sql_semantics.py "host=/tmp port=5433 user=postgres"
Creates (and drops) database ``tm_semantics``. Requires only the ``psql`` binary.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.storage import sql_queries as q  # noqa: E402
from scripts.check_sql import to_positional  # noqa: E402

DB = "tm_semantics"


def psql(dsn: str, sql: str, db: str | None = DB) -> str:
    target = f"{dsn} dbname={db}" if db else f"{dsn} dbname=postgres"
    proc = subprocess.run(["psql", target, "-v", "ON_ERROR_STOP=1", "-qAt", "-F", "|"], input=sql, text=True,
                          capture_output=True)
    if proc.returncode != 0:
        raise AssertionError(proc.stderr)
    return proc.stdout.strip()


def prep(name: str, sql: str) -> str:
    return f"PREPARE {name} AS {to_positional(sql).strip()};\n"


def check(label: str, got: str, expected: str) -> None:
    status = "ok  " if got == expected else "FAIL"
    print(f"{status} {label}: {got!r}" + ("" if got == expected else f" (expected {expected!r})"))
    if got != expected:
        raise SystemExit(1)


def main(dsn: str) -> None:
    psql(dsn, f"DROP DATABASE IF EXISTS {DB}; CREATE DATABASE {DB};", db=None)
    for migration in sorted((ROOT / "migrations/sql").glob("*.sql")):
        psql(dsn, migration.read_text())
    psql(dsn, """
        INSERT INTO users (telegram_id) VALUES (1), (2);
        INSERT INTO plans (code, name, price_stars, duration_days, max_active_searches, allowed_sources,
                           min_interval_seconds, max_notifications_per_day)
        VALUES ('basic', 'Basic', 150, 30, 5, '{mock}', 600, 100);
    """)

    # parameter order of UPSERT_LISTING after to_positional:
    upsert = prep("up", q.UPSERT_LISTING)
    args = "'mock','x1','https://example.invalid/1','T',NULL,{price},'PLN','Kraków',NULL,'used',NULL,true,NULL,NULL,NULL,true,'{now}'"
    order = []
    import re
    for m in re.finditer(r"(?<!:):([a-z_]+)", q.UPSERT_LISTING):
        if m.group(1) not in order:
            order.append(m.group(1))
    assert order[:4] == ["source", "external_id", "url", "title"], order

    def ex_upsert(price: str, now: str) -> str:
        values = dict(source="'mock'", external_id="'x1'", url="'https://example.invalid/1'", title="'T'",
                      description="NULL", price=price, currency="'PLN'", city="'Kraków'", region="NULL",
                      condition="'used'", category="NULL", delivery_available="true", latitude="NULL",
                      longitude="NULL", posted_at="NULL", is_test="true", now=f"'{now}'",
                      seller_created_at="'2013-05-08 20:59+02'" if price == "100" else "NULL",
                      seller_is_business="NULL", photo_url="NULL")
        return f"EXECUTE up({', '.join(values[n] for n in order)});\n"

    out = psql(dsn, upsert + ex_upsert("100", "2026-10-24 10:00+00") +
               "SELECT 'sep';" + ex_upsert("80", "2026-10-24 11:00+00") + "SELECT 'sep';" + ex_upsert("NULL", "2026-10-24 12:00+00")
               + "SELECT price, previous_price, first_seen_at < last_seen_at, seller_created_at IS NOT NULL FROM listings;")
    rows = out.split("\n")
    first, second, third = rows[0], rows[2], rows[4]
    cols = q.LISTING_COLUMNS.replace(" ", "").split(",") + ["inserted", "old_price"]
    get = lambda row, c: row.split("|")[cols.index(c)]  # noqa: E731
    check("upsert#1 inserted", get(first, "inserted"), "t")
    check("upsert#1 old_price", get(first, "old_price"), "")
    check("upsert#2 inserted", get(second, "inserted"), "f")
    check("upsert#2 old_price", get(second, "old_price"), "100.00")
    check("upsert#2 previous_price", get(second, "previous_price"), "100.00")
    check("upsert#3 keeps price when missing", get(third, "price"), "80.00")
    check("final row (seller age kept when later missing)", rows[-1], "80.00|100.00|t|t")

    # notification dedup + claim
    enq = prep("enq", q.ENQUEUE_NOTIFICATION)
    e = "EXECUTE enq(1, 1, NULL, 'new', 'new:1', '2026-10-24 10:00+00', 100, '{}');\n"
    out = psql(dsn, enq + e + e + "EXECUTE enq(2, 1, NULL, 'new', 'new:1', '2026-10-24 10:00+00', 100, '{}');\n"
               "SELECT count(*) FROM notifications;")
    check("enqueue dedup per user (ids, then count)", out.split("\n")[-1], "2")
    claim = prep("cl", q.CLAIM_NOTIFICATIONS)
    out = psql(dsn, claim + "EXECUTE cl('2026-10-24 10:00+00', '2026-10-24 09:55+00', 10);\n"
               "SELECT count(*) FROM notifications WHERE status='sending';")
    check("claim takes ready rows", out.split("\n")[-1], "2")
    out = psql(dsn, claim + "EXECUTE cl('2026-10-24 10:01+00', '2026-10-24 09:56+00', 10);")
    check("claimed rows are not re-claimed", out, "")
    out = psql(dsn, claim + "EXECUTE cl('2026-10-24 10:10+00', '2026-10-24 10:05+00', 10);")
    check("stale 'sending' rows are re-claimed", str(len(out.split("\n"))), "2")
    fin = prep("fin", q.FINISH_NOTIFICATION)
    order_fin = []
    for m in re.finditer(r"(?<!:):([a-z_]+)", q.FINISH_NOTIFICATION):
        if m.group(1) not in order_fin:
            order_fin.append(m.group(1))
    vals = dict(status="'sent'", attempt_inc="1", error="NULL", next_attempt_at="NULL",
                now="'2026-10-24 10:11+00'", message_id="555", id="1")
    out = psql(dsn, fin + f"EXECUTE fin({', '.join(vals[n] for n in order_fin)});\n"
               "SELECT status, attempts, sent_at IS NOT NULL, telegram_message_id, claimed_at IS NULL "
               "FROM notifications WHERE id = 1;")
    check("finish -> sent", out, "sent|1|t|555|t")
    cnt = prep("cnt", q.COUNT_SENT_SINCE)
    check("count sent since", psql(dsn, cnt + "EXECUTE cnt(1, '2026-10-24 00:00+00');"), "1")

    # one active subscription per user
    sub = prep("sub", q.CREATE_SUB)
    try:
        psql(dsn, sub + "EXECUTE sub(1, 1, 'stars', '2026-10-24 10:00+00', '2026-11-24 10:00+00', NULL);\n"
                         "EXECUTE sub(1, 1, 'stars', '2026-10-24 10:00+00', '2026-11-24 10:00+00', NULL);")
        check("second active subscription rejected", "accepted", "rejected")
    except AssertionError as exc:
        check("second active subscription rejected", "rejected" if "ux_subscriptions_one_active" in str(exc) else str(exc), "rejected")

    # payment events idempotency
    pe = prep("pe", q.INSERT_PAYMENT_EVENT)
    out = psql(dsn, pe + "EXECUTE pe('telegram_stars', 'successful_payment', 'ch1', '{}', now());\n"
               "EXECUTE pe('telegram_stars', 'successful_payment', 'ch1', '{}', now());\n"
               "SELECT count(*) FROM payment_events;")
    check("payment event stored once", out.split("\n")[-1], "1")

    psql(dsn, f"DROP DATABASE {DB};", db=None)
    print("\nall semantic checks passed")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "host=/tmp port=5433 user=postgres")
