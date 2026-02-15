"""create incidents table

Revision ID: 0001_incidents
Revises:
Create Date: 2026-02-14 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0001_incidents"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "incidents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "detected_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("error_code", sa.String(128), nullable=False, index=True),
        sa.Column("symptoms", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "breadcrumbs", sa.Text(), nullable=False, server_default="[]"
        ),
        sa.Column("root_cause", sa.Text(), nullable=True),
        sa.Column("remediation", sa.Text(), nullable=True),
        sa.Column("verification", sa.Text(), nullable=True),
        sa.Column(
            "resolved",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("rag_query", sa.Text(), nullable=True),
        sa.Column("rag_response", sa.Text(), nullable=True),
        sa.Column("rag_confidence", sa.Float(), nullable=True),
        sa.Column("backboard_doc_id", sa.String(256), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade():
    op.drop_table("incidents")
