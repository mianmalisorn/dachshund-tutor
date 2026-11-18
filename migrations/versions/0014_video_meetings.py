"""Store LiveKit meetings and the single Tutor video lease."""
from alembic import op
import sqlalchemy as sa

revision = '0014'
down_revision = '0013'


def upgrade():
    op.create_table('video_meetings',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('room_name', sa.String(80), nullable=False),
        sa.Column('teacher_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('student_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('state', sa.String(12), nullable=False),
        sa.Column('lease_slot', sa.Integer()),
        sa.Column('start_key', sa.String(80)),
        sa.Column('revocations_complete', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('ended_at', sa.DateTime()),
        sa.CheckConstraint("state IN ('starting','active','ending','ended')", name='video_meeting_state'),
        sa.CheckConstraint("(state = 'ended' AND lease_slot IS NULL) OR "
                           "(state != 'ended' AND lease_slot = 1)", name='video_single_slot'),
        sa.UniqueConstraint('room_name'),
        sa.UniqueConstraint('lease_slot'),
        sa.UniqueConstraint('start_key'))
    op.create_index('ix_video_meetings_student_id', 'video_meetings', ['student_id'])


def downgrade():
    op.drop_index('ix_video_meetings_student_id', table_name='video_meetings')
    op.drop_table('video_meetings')
