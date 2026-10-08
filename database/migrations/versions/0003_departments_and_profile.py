"""departments + user profile columns

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-08
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "departments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        mysql_charset="utf8mb4",
    )
    op.add_column("users", sa.Column("username", sa.String(32), nullable=True))
    op.add_column("users", sa.Column("employee_id", sa.String(32), nullable=True))
    op.add_column("users", sa.Column("department_id", sa.Integer(), nullable=True))
    op.add_column("users", sa.Column("face_enrolled_at", sa.DateTime(), nullable=True))
    op.create_unique_constraint("uq_users_username", "users", ["username"])
    op.create_unique_constraint("uq_users_employee_id", "users", ["employee_id"])
    op.create_index("ix_users_department_id", "users", ["department_id"])
    op.create_foreign_key("fk_users_department_id", "users", "departments", ["department_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint("fk_users_department_id", "users", type_="foreignkey")
    op.drop_index("ix_users_department_id", "users")
    op.drop_constraint("uq_users_employee_id", "users", type_="unique")
    op.drop_constraint("uq_users_username", "users", type_="unique")
    op.drop_column("users", "face_enrolled_at")
    op.drop_column("users", "department_id")
    op.drop_column("users", "employee_id")
    op.drop_column("users", "username")
    op.drop_table("departments")
