"""make users.org_id nullable for super_admin support

Revision ID: c3d5f2a1b4e8
Revises: b2c4e1f9a3d7
Create Date: 2026-03-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3d5f2a1b4e8'
down_revision: Union[str, None] = 'b2c4e1f9a3d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('users', 'org_id', nullable=True)


def downgrade() -> None:
    op.alter_column('users', 'org_id', nullable=False)
