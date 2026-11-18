"""Private topic catalog, per-student state and lesson associations."""
from alembic import op
import sqlalchemy as sa
revision = '0007'
down_revision = '0006'


def upgrade():
    op.create_index('uq_lesson_record_owner','lesson_records',['id','student_id'],unique=True)
    op.create_table('topics',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('title',sa.String(180),nullable=False),sa.Column('category',sa.String(80),nullable=False),
        sa.Column('normalized_title',sa.String(360),nullable=False),sa.Column('normalized_category',sa.String(160),nullable=False),
        sa.Column('creation_key',sa.String(32),nullable=False,unique=True),sa.Column('version',sa.Integer(),nullable=False),
        sa.Column('created_at',sa.DateTime(),nullable=False),sa.Column('updated_at',sa.DateTime(),nullable=False),
        sa.UniqueConstraint('normalized_title','normalized_category',name='uq_topic_name_category'))
    op.create_table('student_topics',
        sa.Column('id',sa.Integer(),primary_key=True),sa.Column('student_id',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),
        sa.Column('topic_id',sa.Integer(),sa.ForeignKey('topics.id'),nullable=False),sa.Column('status',sa.String(16),nullable=False),
        sa.Column('note',sa.Text(),nullable=False),sa.Column('creation_key',sa.String(32),nullable=False,unique=True),
        sa.Column('version',sa.Integer(),nullable=False),sa.Column('created_at',sa.DateTime(),nullable=False),
        sa.Column('updated_at',sa.DateTime(),nullable=False),sa.Column('status_changed_at',sa.DateTime(),nullable=False),
        sa.UniqueConstraint('student_id','topic_id',name='uq_student_topic'),
        sa.UniqueConstraint('id','student_id',name='uq_student_topic_owner'),
        sa.CheckConstraint("status IN ('unmarked','studying','practicing','repeat','confident')",name='student_topic_status'))
    op.create_index('ix_student_topics_student_status','student_topics',['student_id','status','updated_at'])
    op.create_table('lesson_topics',
        sa.Column('lesson_record_id',sa.Integer(),primary_key=True),sa.Column('student_topic_id',sa.Integer(),primary_key=True),
        sa.Column('student_id',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),
        sa.ForeignKeyConstraint(['lesson_record_id','student_id'],['lesson_records.id','lesson_records.student_id']),
        sa.ForeignKeyConstraint(['student_topic_id','student_id'],['student_topics.id','student_topics.student_id']))
    op.create_index('ix_lesson_topics_progress','lesson_topics',['student_topic_id','lesson_record_id'])
    op.create_table('topic_events',
        sa.Column('id',sa.Integer(),primary_key=True),sa.Column('topic_id',sa.Integer(),sa.ForeignKey('topics.id'),nullable=False),
        sa.Column('student_topic_id',sa.Integer(),sa.ForeignKey('student_topics.id')),
        sa.Column('lesson_record_id',sa.Integer(),sa.ForeignKey('lesson_records.id')),
        sa.Column('actor_id',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),
        sa.Column('kind',sa.String(24),nullable=False),sa.Column('title_snapshot',sa.String(180),nullable=False),
        sa.Column('before_json',sa.Text(),nullable=False),sa.Column('after_json',sa.Text(),nullable=False),
        sa.Column('created_at',sa.DateTime(),nullable=False))
    op.create_index('ix_topic_events_progress','topic_events',['student_topic_id','id'])
    op.create_index('ix_topic_events_catalog','topic_events',['topic_id','id'])


def downgrade():
    raise RuntimeError('Restore a verified backup instead of removing topic history.')
