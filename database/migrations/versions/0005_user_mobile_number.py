"""add mobile_number to users

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-09
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("mobile_number", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "mobile_number")

