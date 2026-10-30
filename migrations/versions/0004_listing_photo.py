"""listings.photo_url

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-24
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE listings ADD COLUMN photo_url TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE listings DROP COLUMN photo_url")
