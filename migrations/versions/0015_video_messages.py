"""Persist meeting chat in Tutor's database."""
from alembic import op
import sqlalchemy as sa

revision = '0015'
down_revision = '0014'


def upgrade():
    op.create_table('video_messages',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('meeting_id', sa.String(36), sa.ForeignKey('video_meetings.id'), nullable=False),
        sa.Column('author_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('client_key', sa.String(80), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('meeting_id', 'author_id', 'client_key', name='uq_video_message_retry'))
    op.create_index('ix_video_messages_meeting_order', 'video_messages', ['meeting_id', 'id'])


def downgrade():
    op.drop_index('ix_video_messages_meeting_order', table_name='video_messages')
    op.drop_table('video_messages')
