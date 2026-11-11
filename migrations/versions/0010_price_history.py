"""Append-only listing price observations."""

from alembic import op
from pathlib import Path

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = Path(__file__).resolve().parents[1] / "sql" / "0010_price_history.sql"
    for statement in sql.read_text().split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE listing_price_history")
