"""Attach existing Tutor uploads and links to video meetings."""
from alembic import op
import sqlalchemy as sa

revision = '0016'
down_revision = '0015'

OLD_OWNER = ('(assignment_id IS NOT NULL AND submission_id IS NULL) OR '
             '(assignment_id IS NULL AND submission_id IS NOT NULL)')
NEW_OWNER = ('(assignment_id IS NOT NULL AND submission_id IS NULL AND meeting_id IS NULL) OR '
             '(assignment_id IS NULL AND submission_id IS NOT NULL AND meeting_id IS NULL) OR '
             '(assignment_id IS NULL AND submission_id IS NULL AND meeting_id IS NOT NULL)')
MATERIAL_KIND = ("(kind = 'file' AND attachment_id IS NOT NULL AND url IS NULL) OR "
                 "(kind = 'link' AND attachment_id IS NULL AND url IS NOT NULL)")


def upgrade():
    with op.batch_alter_table('attachments', recreate='always') as batch:
        batch.drop_constraint('one_attachment_owner', type_='check')
        batch.add_column(sa.Column('meeting_id', sa.String(36), nullable=True))
        batch.create_foreign_key('fk_attachments_meeting_id', 'video_meetings', ['meeting_id'], ['id'])
        batch.create_check_constraint('one_attachment_owner', NEW_OWNER)

    op.create_table('video_materials',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('meeting_id', sa.String(36), sa.ForeignKey('video_meetings.id'), nullable=False),
        sa.Column('kind', sa.String(8), nullable=False),
        sa.Column('title', sa.String(255), nullable=False),
        sa.Column('author_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('attachment_id', sa.Integer(), sa.ForeignKey('attachments.id'), unique=True),
        sa.Column('url', sa.String(2048)),
        sa.CheckConstraint(MATERIAL_KIND, name='video_material_kind'))
    op.create_index('ix_video_materials_meeting_order', 'video_materials', ['meeting_id', 'id'])


def downgrade():
    op.drop_index('ix_video_materials_meeting_order', table_name='video_materials')
    op.drop_table('video_materials')
    with op.batch_alter_table('attachments', recreate='always') as batch:
        batch.drop_constraint('one_attachment_owner', type_='check')
        batch.drop_constraint('fk_attachments_meeting_id', type_='foreignkey')
        batch.drop_column('meeting_id')
        batch.create_check_constraint('one_attachment_owner', OLD_OWNER)
