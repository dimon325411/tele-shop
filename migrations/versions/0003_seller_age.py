"""seller account age + users.hide_new_sellers

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-24
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE listings ADD COLUMN seller_created_at TIMESTAMPTZ")
    op.execute("ALTER TABLE listings ADD COLUMN seller_is_business BOOLEAN")
    op.execute("ALTER TABLE users ADD COLUMN hide_new_sellers BOOLEAN NOT NULL DEFAULT FALSE")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN hide_new_sellers")
    op.execute("ALTER TABLE listings DROP COLUMN seller_is_business")
    op.execute("ALTER TABLE listings DROP COLUMN seller_created_at")
