"""Initial schema — orders table.

Revision: 0001
"""
from alembic import op
import sqlalchemy as sa


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("destination", sa.Text, nullable=False),
        sa.Column("weight", sa.Float, nullable=False),
        sa.Column("region", sa.Text, nullable=False),
        sa.Column("shipping_cost", sa.Float, nullable=False),
        sa.Column("created_at", sa.Text, nullable=False, server_default=sa.text("datetime('now')")),
    )


def downgrade() -> None:
    op.drop_table("orders")
