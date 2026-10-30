"""searches.match_description

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-24
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE searches ADD COLUMN match_description BOOLEAN NOT NULL DEFAULT FALSE")


def downgrade() -> None:
    op.execute("ALTER TABLE searches DROP COLUMN match_description")
