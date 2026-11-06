"""Hub tables, and API keys stored as hashes.

The app also runs create_all on startup, so each step first checks whether the table or
column already exists.

Existing API keys keep working: the old key was the row id, so its hash is filled in from it.

Revision ID: b3c1d2e4f5a6
Revises: 9f65fac77aec
"""
import hashlib
import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "b3c1d2e4f5a6"
down_revision = "9f65fac77aec"
branch_labels = None
depends_on = None


def _tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = _tables(bind)

    # ---- API keys: add key_hash and fill it in for existing keys
    if "apikeys" in tables:
        columns = {c["name"] for c in inspector.get_columns("apikeys")}
        if "key_hash" not in columns:
            with op.batch_alter_table("apikeys") as batch:
                batch.add_column(sa.Column("key_hash", sa.String(64), nullable=True))

        apikeys = sa.table("apikeys", sa.column("id", UUID(as_uuid=True)), sa.column("key_hash", sa.String))
        rows = bind.execute(sa.select(apikeys.c.id).where(apikeys.c.key_hash.is_(None))).all()
        for (key_id,) in rows:
            old_key = str(uuid.UUID(str(key_id)))  # the old key, exactly as clients send it
            bind.execute(
                apikeys.update()
                .where(apikeys.c.id == key_id)
                .values(key_hash=hashlib.sha256(old_key.encode()).hexdigest())
            )

        indexes = {i["name"] for i in sa.inspect(bind).get_indexes("apikeys")}
        uniques = {u["name"] for u in sa.inspect(bind).get_unique_constraints("apikeys")}
        if "ix_apikeys_key_hash" not in indexes and not uniques:
            op.create_index("ix_apikeys_key_hash", "apikeys", ["key_hash"], unique=True)

    # ---- hub tables
    if "hub_sites" not in tables:
        op.create_table(
            "hub_sites",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("name", sa.String(100), nullable=False, unique=True),
            sa.Column("base_url", sa.String(500), nullable=False),
            sa.Column("verify_url", sa.String(500), nullable=False),
            sa.Column("site_key_hash", sa.String(64), nullable=False, unique=True),
            sa.Column("hub_key", sa.String(128), nullable=False),
            sa.Column("created_at", sa.DateTime()),
        )

    if "hub_links" not in tables:
        op.create_table(
            "hub_links",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
            sa.Column("site_id", UUID(as_uuid=True), sa.ForeignKey("hub_sites.id", ondelete="CASCADE"), nullable=False),
            sa.Column("remote_user_id", sa.String(255), nullable=False),
            sa.Column("created_at", sa.DateTime()),
            sa.UniqueConstraint("user_id", "site_id", name="uq_hub_link_user_site"),
            sa.UniqueConstraint("site_id", "remote_user_id", name="uq_hub_link_site_remote_user"),
        )

    if "hub_sso_codes" not in tables:
        op.create_table(
            "hub_sso_codes",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("code_hash", sa.String(64), nullable=False, unique=True),
            sa.Column("site_id", UUID(as_uuid=True), sa.ForeignKey("hub_sites.id", ondelete="CASCADE"), nullable=False),
            sa.Column("remote_user_id", sa.String(255), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("used_at", sa.DateTime(), nullable=True),
        )


def downgrade():
    tables = _tables(op.get_bind())
    for name in ("hub_sso_codes", "hub_links", "hub_sites"):
        if name in tables:
            op.drop_table(name)
    if "apikeys" in tables:
        indexes = {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("apikeys")}
        if "ix_apikeys_key_hash" in indexes:
            op.drop_index("ix_apikeys_key_hash", table_name="apikeys")
        with op.batch_alter_table("apikeys") as batch:
            batch.drop_column("key_hash")
