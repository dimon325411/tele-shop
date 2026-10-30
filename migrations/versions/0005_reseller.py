"""reseller mode: searches.deal_min_discount, market_snapshots

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-24
"""
from pathlib import Path

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

SQL = Path(__file__).resolve().parents[1] / "sql" / "0005_reseller.sql"


def upgrade() -> None:
    lines = [line for line in SQL.read_text(encoding="utf-8").splitlines() if not line.strip().startswith("--")]
    for statement in "\n".join(lines).split(";"):
        if statement.strip():
            op.get_bind().exec_driver_sql(statement.strip())


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS market_snapshots")
    op.execute("ALTER TABLE searches DROP COLUMN deal_min_discount")
