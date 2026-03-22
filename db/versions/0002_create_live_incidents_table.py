"""create live_incidents table

Revision ID: 0002_live_incidents
Revises: 0001_incidents
Create Date: 2026-03-21 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0002_live_incidents"
down_revision = "0001_incidents"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "live_incidents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "incident_id",
            sa.String(64),
            nullable=False,
            unique=True,
            index=True,
        ),
        sa.Column("data", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade():
    op.drop_table("live_incidents")
