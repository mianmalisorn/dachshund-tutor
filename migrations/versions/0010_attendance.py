"""Explicit attendance, no historical inference or financial side effects."""
from alembic import op
import sqlalchemy as sa
revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('lesson_attendance',
        sa.Column('lesson_id',sa.Integer(),primary_key=True),
        sa.Column('student_id',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),
        sa.Column('outcome',sa.String(24),nullable=False),
        sa.Column('actual_minutes',sa.Integer()),
        sa.Column('comment',sa.Text(),nullable=False),
        sa.Column('version',sa.Integer(),nullable=False),
        sa.Column('updated_at',sa.DateTime(),nullable=False),
        sa.Column('updated_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),
        sa.ForeignKeyConstraint(['lesson_id','student_id'],['lessons.id','lessons.student_id']),
        sa.CheckConstraint("outcome IN ('unknown','conducted','student_absent','teacher_absent','cancelled_student','cancelled_teacher','other')",name='attendance_outcome'),
        sa.CheckConstraint('version >= 1',name='attendance_version'),
        sa.CheckConstraint("actual_minutes IS NULL OR (outcome = 'conducted' AND actual_minutes BETWEEN 1 AND 1440)",name='attendance_minutes'))
    op.create_index('ix_attendance_student_outcome','lesson_attendance',['student_id','outcome'])
    op.create_table('attendance_events',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('lesson_id',sa.Integer(),sa.ForeignKey('lesson_attendance.lesson_id'),nullable=False),
        sa.Column('actor_id',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),
        sa.Column('created_at',sa.DateTime(),nullable=False),
        sa.Column('request_key',sa.String(32),nullable=False,unique=True),
        sa.Column('request_hash',sa.String(64),nullable=False),
        sa.Column('before_outcome',sa.String(24),nullable=False),
        sa.Column('after_outcome',sa.String(24),nullable=False),
        sa.Column('before_minutes',sa.Integer()),sa.Column('after_minutes',sa.Integer()),
        sa.Column('before_comment',sa.Text(),nullable=False),sa.Column('after_comment',sa.Text(),nullable=False),
        sa.Column('reason',sa.String(500),nullable=False),sa.Column('result_version',sa.Integer(),nullable=False))
    op.create_index('ix_attendance_events_lesson','attendance_events',['lesson_id','id'])

def downgrade():
    op.drop_table('attendance_events')
    op.drop_table('lesson_attendance')
