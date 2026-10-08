"""create attendance_events

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-08
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "attendance_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(16), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("work_date", sa.Date(), nullable=False),
        sa.Column("face_confidence", sa.Float(), nullable=True),
        mysql_charset="utf8mb4",
    )
    op.create_index("ix_attendance_events_user_work_date", "attendance_events", ["user_id", "work_date"])
    op.create_index("ix_attendance_events_work_date", "attendance_events", ["work_date"])


def downgrade() -> None:
    op.drop_table("attendance_events")
