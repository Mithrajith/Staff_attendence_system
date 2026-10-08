"""department code (business id such as "002"; kept as text to preserve leading zeros)

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-08
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("departments", sa.Column("code", sa.String(16), nullable=True))
    conn = op.get_bind()
    for (dept_id,) in conn.execute(sa.text("SELECT id FROM departments")).fetchall():
        conn.execute(sa.text("UPDATE departments SET code = :c WHERE id = :i"), {"c": f"{dept_id:03d}", "i": dept_id})
    op.alter_column("departments", "code", existing_type=sa.String(16), nullable=False)
    op.create_unique_constraint("uq_departments_code", "departments", ["code"])


def downgrade() -> None:
    op.drop_constraint("uq_departments_code", "departments", type_="unique")
    op.drop_column("departments", "code")
