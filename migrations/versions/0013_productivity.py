"""Private expiring drafts and per-user notification read markers."""
from alembic import op
import sqlalchemy as sa

revision = '0013'
down_revision = '0012'


def upgrade():
    op.create_table('user_drafts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(32), nullable=False),
        sa.Column('draft_key', sa.String(80), nullable=False),
        sa.Column('payload_json', sa.Text(), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('user_id', 'kind', 'draft_key', name='uq_user_draft_owner_key'))
    op.create_index('ix_user_drafts_user_id', 'user_drafts', ['user_id'])
    op.create_index('ix_user_drafts_expires_at', 'user_drafts', ['expires_at'])
    op.create_table('user_notification_states',
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('notification_key', sa.String(120), primary_key=True),
        sa.Column('read_at', sa.DateTime(), nullable=False))


def downgrade():
    raise RuntimeError('Restore a verified backup instead of removing private productivity state.')
