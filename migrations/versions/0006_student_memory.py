"""Optional student profile and private teaching memory, preserving all existing rows."""
from alembic import op
import sqlalchemy as sa
revision = '0006'
down_revision = '0005'


def upgrade():
    for name in ('learning_goal', 'learning_materials', 'learning_position'):
        op.add_column('users', sa.Column(name, sa.Text(), nullable=False, server_default=''))
    for name in ('lesson_records', 'student_notes'):
        columns = [sa.Column('id',sa.Integer(),primary_key=True),
                   sa.Column('student_id',sa.Integer(),sa.ForeignKey('users.id'),nullable=False)]
        if name == 'lesson_records':
            columns += [sa.Column('lesson_date',sa.Date(),nullable=False),
                        sa.Column('lesson_time',sa.Time()),
                        sa.Column('timezone_name',sa.String(80),nullable=False)]
            columns += [sa.Column(f,sa.Text(),nullable=False) for f in ('topic','completed','stopped_at','difficulties','next_step')]
        else:
            columns += [sa.Column('body',sa.Text(),nullable=False)]
        columns += [sa.Column('creation_key',sa.String(32),nullable=False,unique=True),
                    sa.Column('version',sa.Integer(),nullable=False),
                    sa.Column('created_at',sa.DateTime(),nullable=False),
                    sa.Column('updated_at',sa.DateTime(),nullable=False),
                    sa.Column('deleted_at',sa.DateTime())]
        op.create_table(name,*columns)
        op.create_index('ix_'+name+'_student_id',name,['student_id'])
    op.create_index('ix_lesson_records_student_date','lesson_records',['student_id','deleted_at','lesson_date','lesson_time','id'])
    op.create_index('ix_student_notes_student_created','student_notes',['student_id','deleted_at','created_at','id'])
    op.create_table('teaching_revisions',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('lesson_record_id',sa.Integer(),sa.ForeignKey('lesson_records.id')),
        sa.Column('note_id',sa.Integer(),sa.ForeignKey('student_notes.id')),
        sa.Column('actor_id',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),
        sa.Column('created_at',sa.DateTime(),nullable=False),
        sa.Column('action',sa.String(12),nullable=False),
        sa.Column('snapshot',sa.Text(),nullable=False),
        sa.CheckConstraint('(lesson_record_id IS NOT NULL AND note_id IS NULL) OR (lesson_record_id IS NULL AND note_id IS NOT NULL)',name='one_teaching_revision_owner'))
    op.create_index('ix_teaching_revisions_lesson_record_id','teaching_revisions',['lesson_record_id'])
    op.create_index('ix_teaching_revisions_note_id','teaching_revisions',['note_id'])


def downgrade():
    raise RuntimeError('Restore a verified backup instead of deleting teaching history.')
