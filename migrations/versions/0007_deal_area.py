"""deal area: users.deal_city, users.deal_radius_km

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-24
"""
from pathlib import Path

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

SQL = Path(__file__).resolve().parents[1] / "sql" / "0007_deal_area.sql"


def upgrade() -> None:
    lines = [line for line in SQL.read_text(encoding="utf-8").splitlines() if not line.strip().startswith("--")]
    for statement in "\n".join(lines).split(";"):
        if statement.strip():
            op.get_bind().exec_driver_sql(statement.strip())


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN deal_radius_km")
    op.execute("ALTER TABLE users DROP COLUMN deal_city")
