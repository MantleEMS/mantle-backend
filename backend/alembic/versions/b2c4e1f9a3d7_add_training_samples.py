"""add training_samples table

Revision ID: b2c4e1f9a3d7
Revises: ae83887d8c6f
Create Date: 2026-03-19 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'b2c4e1f9a3d7'
down_revision: Union[str, None] = 'ae83887d8c6f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'training_samples',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('incident_id', sa.UUID(), nullable=True),
        sa.Column('org_id', sa.UUID(), nullable=True),
        sa.Column('provider', sa.String(length=30), nullable=False),
        sa.Column('model', sa.String(length=100), nullable=False),
        sa.Column('emergency_type', sa.String(length=50), nullable=True),
        sa.Column('quality', sa.String(length=20), nullable=False, server_default='good'),
        sa.Column('success', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('iterations', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('conversation', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='[]'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['incident_id'], ['incidents.id']),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_training_samples_quality', 'training_samples', ['quality'])
    op.create_index('ix_training_samples_emergency_type', 'training_samples', ['emergency_type'])


def downgrade() -> None:
    op.drop_index('ix_training_samples_emergency_type', table_name='training_samples')
    op.drop_index('ix_training_samples_quality', table_name='training_samples')
    op.drop_table('training_samples')
