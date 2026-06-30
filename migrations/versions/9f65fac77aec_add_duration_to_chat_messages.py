from alembic import op
import sqlalchemy as sa


revision = '9f65fac77aec'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():

    op.add_column(
        'chat_messages',
        sa.Column(
            'duration',
            sa.Float(),
            nullable=True
        )
    )


def downgrade():

    op.drop_column(
        'chat_messages',
        'duration'
    )