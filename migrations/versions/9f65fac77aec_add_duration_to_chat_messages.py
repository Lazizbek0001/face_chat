"""Add chat_messages.duration.

The app creates missing tables itself on startup (create_all), so this checks first:
on a new database the table may not exist yet, or may already have the column.
"""
import sqlalchemy as sa
from alembic import op

revision = '9f65fac77aec'
down_revision = None
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str] | None:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return None
    return {c["name"] for c in inspector.get_columns(table)}


def upgrade():
    columns = _columns("chat_messages")
    if columns is not None and "duration" not in columns:
        with op.batch_alter_table("chat_messages") as batch:
            batch.add_column(sa.Column("duration", sa.Float(), nullable=True))


def downgrade():
    columns = _columns("chat_messages")
    if columns is not None and "duration" in columns:
        with op.batch_alter_table("chat_messages") as batch:
            batch.drop_column("duration")
