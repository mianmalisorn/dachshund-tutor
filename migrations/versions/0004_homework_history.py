"""Preserve review history and deadline facts; optimistic concurrency for homework."""
from alembic import op
import sqlalchemy as sa

revision = '0004'
down_revision = '0003'


def upgrade():
    op.add_column('assignments', sa.Column('version', sa.Integer(), nullable=False, server_default='1'))
    op.add_column('assignment_targets', sa.Column('version', sa.Integer(), nullable=False, server_default='1'))
    # Earlier deadlines cannot be reconstructed honestly: legacy snapshots stay NULL.
    op.add_column('submissions', sa.Column('due_at_snapshot', sa.DateTime(), nullable=True))
    op.create_table('review_events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('submission_id', sa.Integer(), sa.ForeignKey('submissions.id'), nullable=False),
        sa.Column('state', sa.String(12), nullable=False),
        sa.Column('comment', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('actor_id', sa.Integer(), sa.ForeignKey('users.id')),
        sa.CheckConstraint("state IN ('checked','revision')", name='review_event_state'))
    op.create_index('ix_review_events_submission_id', 'review_events', ['submission_id'])
    op.execute("INSERT INTO review_events (submission_id,state,comment,created_at,actor_id) "
               "SELECT id,review_state,review_comment,COALESCE(reviewed_at,created_at),reviewed_by "
               "FROM submissions WHERE review_state IN ('checked','revision')")
    op.create_table('deadline_events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('assignment_id', sa.Integer(), sa.ForeignKey('assignments.id'), nullable=False),
        sa.Column('previous_due_at', sa.DateTime(), nullable=False),
        sa.Column('due_at', sa.DateTime(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('actor_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False))
    op.create_index('ix_deadline_events_assignment_id', 'deadline_events', ['assignment_id'])


def downgrade():
    raise RuntimeError('Downgrade would destroy homework history; restore a verified backup instead.')
