"""Persistent job state and free-item notification payloads."""
from pathlib import Path

from alembic import op

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade():
    path = Path(__file__).resolve().parents[1] / 'sql' / '0009_reliable_jobs.sql'
    for statement in path.read_text().split(';'):
        if statement.strip():
            op.get_bind().exec_driver_sql(statement.strip())


def downgrade():
    # Refuse a rollback with pending free alerts rather than silently discarding them.
    op.execute("ALTER TABLE notifications DROP CONSTRAINT notifications_kind_check")
    op.execute("ALTER TABLE notifications ADD CONSTRAINT notifications_kind_check CHECK (kind IN ('new', 'price_drop'))")
    op.execute('ALTER TABLE notifications DROP COLUMN payload')
    op.execute('DROP TABLE job_state')
