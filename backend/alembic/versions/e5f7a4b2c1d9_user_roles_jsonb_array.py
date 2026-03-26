"""convert users.role string to roles JSONB array

Revision ID: e5f7a4b2c1d9
Revises: d4e6b3c2a5f1
Create Date: 2026-03-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'e5f7a4b2c1d9'
down_revision: Union[str, None] = 'd4e6b3c2a5f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add the new roles JSONB column, defaulting to empty array
    op.add_column('users', sa.Column('roles', postgresql.JSONB(astext_type=sa.Text()), nullable=True))

    # Migrate existing single-role string into a one-element JSON array
    op.execute("UPDATE users SET roles = jsonb_build_array(role)")

    # Make non-nullable now that data is populated
    op.alter_column('users', 'roles', nullable=False)

    # Drop the old role column
    op.drop_column('users', 'role')

    # Replace old composite index with org+status index and a GIN index on roles
    op.execute('DROP INDEX IF EXISTS ix_users_org_role_status')
    op.execute('DROP INDEX IF EXISTS ix_users_org_status')
    op.execute('DROP INDEX IF EXISTS ix_users_roles_gin')
    op.create_index('ix_users_org_status', 'users', ['org_id', 'status'])
    op.create_index('ix_users_roles_gin', 'users', ['roles'], postgresql_using='gin')


def downgrade() -> None:
    op.drop_index('ix_users_roles_gin', table_name='users')
    op.drop_index('ix_users_org_status', table_name='users')

    # Restore old role column from first element of roles array
    op.add_column('users', sa.Column('role', sa.String(length=30), nullable=True))
    op.execute("UPDATE users SET role = roles->>0")
    op.alter_column('users', 'role', nullable=False)

    op.drop_column('users', 'roles')

    op.execute('DROP INDEX IF EXISTS ix_users_org_status')
    op.create_index('ix_users_org_role_status', 'users', ['org_id', 'role', 'status'])
