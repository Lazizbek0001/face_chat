"""Log in through Authentik: add user.oidc_sub, drop the custom hub tables.

Revision ID: c7d8e9f0a1b2
Revises: b3c1d2e4f5a6
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "c7d8e9f0a1b2"
down_revision = "b3c1d2e4f5a6"
branch_labels = None
depends_on = None

HUB_TABLES = ("hub_sso_codes", "hub_links", "hub_sites")


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "user" in tables:
        columns = {c["name"] for c in inspector.get_columns("user")}
        if "oidc_sub" not in columns:
            with op.batch_alter_table("user") as batch:
                batch.add_column(sa.Column("oidc_sub", sa.String(255), nullable=True))
        indexes = {i["name"] for i in sa.inspect(bind).get_indexes("user")}
        if "ix_user_oidc_sub" not in indexes:
            op.create_index("ix_user_oidc_sub", "user", ["oidc_sub"], unique=True)

    for name in HUB_TABLES:
        if name in tables:
            op.drop_table(name)


def downgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())

    if "user" in tables:
        indexes = {i["name"] for i in sa.inspect(bind).get_indexes("user")}
        if "ix_user_oidc_sub" in indexes:
            op.drop_index("ix_user_oidc_sub", table_name="user")
        with op.batch_alter_table("user") as batch:
            batch.drop_column("oidc_sub")

    # Recreate the hub tables empty, as revision b3c1d2e4f5a6 left them.
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
