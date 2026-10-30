"""PREPARE every statement from app/storage/sql_queries.py against a real PostgreSQL.

Usage:  python scripts/check_sql.py "host=/tmp port=5433 user=postgres dbname=tm"

Uses the ``psql`` binary only (no Python DB driver needed). Catches syntax errors, unknown
columns and parameter type-inference problems. Exit code 1 on any failure.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.storage import sql_queries as q  # noqa: E402

PARAM = re.compile(r"(?<!:):([a-z_][a-z0-9_]*)")


def to_positional(sql: str) -> str:
    names: list[str] = []

    def repl(m: re.Match) -> str:
        name = m.group(1)
        if name not in names:
            names.append(name)
        return f"${names.index(name) + 1}"

    return PARAM.sub(repl, sql)


def statements() -> dict[str, str]:
    stmts = {k: v for k, v in vars(q).items() if k.isupper() and isinstance(v, str) and len(v.split()) > 3}
    stmts = {k: v for k, v in stmts.items() if not k.endswith("_COLUMNS")}
    stmts["DYN_UPDATE_USER"] = q.build_update("users", dict.fromkeys(q.USER_UPDATABLE), q.USER_UPDATABLE,
                                              "updated_at = now()")
    stmts["DYN_UPDATE_SEARCH"] = q.build_update("searches", dict.fromkeys(q.SEARCH_UPDATABLE), q.SEARCH_UPDATABLE,
                                                "updated_at = now()")
    stmts["DYN_UPDATE_PLAN"] = q.build_update("plans", dict.fromkeys(q.PLAN_UPDATABLE), q.PLAN_UPDATABLE,
                                              "updated_at = now()")
    stmts["DYN_UPDATE_PAYMENT"] = q.build_update("payments", dict.fromkeys(q.PAYMENT_UPDATABLE), q.PAYMENT_UPDATABLE)
    return stmts


def main(dsn: str) -> int:
    failures = 0
    for i, (name, sql) in enumerate(sorted(statements().items())):
        script = f"BEGIN;\nPREPARE s{i} AS {to_positional(sql).strip().rstrip(';')};\nROLLBACK;\n"
        proc = subprocess.run(["psql", dsn, "-v", "ON_ERROR_STOP=1", "-q"], input=script, text=True,
                              capture_output=True)
        if proc.returncode != 0:
            failures += 1
            print(f"FAIL {name}: {proc.stderr.strip()}")
        else:
            print(f"ok   {name}")
    print(f"\n{failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "host=/tmp port=5433 user=postgres dbname=tm"))
