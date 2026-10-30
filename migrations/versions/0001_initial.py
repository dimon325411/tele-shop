"""Initial schema (migrations/sql/0001_initial.sql).

Revision ID: 0001
Revises:
Create Date: 2026-10-24
"""
from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SQL = Path(__file__).resolve().parents[1] / "sql" / "0001_initial.sql"

TABLES = [
    "admin_audit_log", "source_states", "refunds", "payment_events", "notifications", "match_results",
    "monitoring_runs", "listings", "searches", "subscriptions", "payments", "plans", "users",
]


def split_statements(sql: str) -> list[str]:
    """Split the schema file into single statements (it contains no ';' inside literals)."""
    lines = [line for line in sql.splitlines() if not line.strip().startswith("--")]
    return [stmt.strip() for stmt in "\n".join(lines).split(";") if stmt.strip()]


def upgrade() -> None:
    bind = op.get_bind()
    for statement in split_statements(SQL.read_text(encoding="utf-8")):
        bind.exec_driver_sql(statement)


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
