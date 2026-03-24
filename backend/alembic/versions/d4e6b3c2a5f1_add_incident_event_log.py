"""add incident_event_log table

Revision ID: d4e6b3c2a5f1
Revises: c3d5f2a1b4e8
Create Date: 2026-03-24 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'd4e6b3c2a5f1'
down_revision: Union[str, None] = 'c3d5f2a1b4e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'incident_event_log',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('incident_id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=True),
        sa.Column('event_type', sa.String(length=80), nullable=False),
        sa.Column('source', sa.String(length=50), nullable=False, server_default='incident_ws'),
        sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='{}'),
        sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['incident_id'], ['incidents.id']),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_incident_event_log_incident_recorded', 'incident_event_log', ['incident_id', 'recorded_at'])
    op.create_index('ix_incident_event_log_incident_event_type', 'incident_event_log', ['incident_id', 'event_type'])


def downgrade() -> None:
    op.drop_index('ix_incident_event_log_incident_event_type', table_name='incident_event_log')
    op.drop_index('ix_incident_event_log_incident_recorded', table_name='incident_event_log')
    op.drop_table('incident_event_log')
