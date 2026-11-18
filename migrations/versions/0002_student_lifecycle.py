"""Student lifecycle, explicit access and transition history."""
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'

def upgrade():
    op.add_column('users', sa.Column('learning_status', sa.String(12), sa.CheckConstraint("learning_status IN ('active','paused','archived')", name='learning_status_check'), nullable=False, server_default='active'))
    op.add_column('users', sa.Column('internal_notes', sa.Text(), nullable=False, server_default=''))
    op.add_column('users', sa.Column('last_login_at', sa.DateTime(), nullable=True))
    op.create_index('ix_users_learning_status', 'users', ['learning_status'])
    op.create_table('student_status_events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('student_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('status', sa.String(12), nullable=False),
        sa.Column('access_enabled', sa.Boolean(), nullable=False),
        sa.Column('changed_at', sa.DateTime(), nullable=False),
        sa.Column('changed_by', sa.Integer(), sa.ForeignKey('users.id')),
        sa.CheckConstraint("status IN ('active','paused','archived')", name='event_status_check'))
    op.create_index('ix_student_status_events_student_id', 'student_status_events', ['student_id'])
    op.execute("UPDATE users SET learning_status='archived' WHERE role='student' AND active=false")
    op.execute("INSERT INTO student_status_events (student_id,status,access_enabled,changed_at) SELECT id,learning_status,active,CURRENT_TIMESTAMP FROM users WHERE role='student'")

def downgrade():
    raise RuntimeError('Lifecycle history must not be dropped. Restore a verified backup instead.')
