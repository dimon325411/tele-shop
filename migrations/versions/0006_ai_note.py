"""hot-deal mode: listings.ai_note

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-24
"""
from pathlib import Path

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

SQL = Path(__file__).resolve().parents[1] / "sql" / "0006_ai_note.sql"


def upgrade() -> None:
    lines = [line for line in SQL.read_text(encoding="utf-8").splitlines() if not line.strip().startswith("--")]
    for statement in "\n".join(lines).split(";"):
        if statement.strip():
            op.get_bind().exec_driver_sql(statement.strip())


def downgrade() -> None:
    op.execute("ALTER TABLE listings DROP COLUMN ai_note")
