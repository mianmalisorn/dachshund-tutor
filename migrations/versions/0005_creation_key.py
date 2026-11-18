"""One creation form creates at most one homework, including concurrent POSTs."""
from alembic import op
import sqlalchemy as sa

revision = '0005'
down_revision = '0004'


def upgrade():
    op.add_column('users', sa.Column('profile_version', sa.Integer(), nullable=False, server_default='1'))
    # Legacy assignments have no recoverable request key; preserve them as NULL.
    op.add_column('assignments', sa.Column('creation_key', sa.String(32), nullable=True))
    op.create_index('uq_assignments_creation_key', 'assignments', ['creation_key'], unique=True)


def downgrade():
    raise RuntimeError('Restore a verified backup instead of removing replay protection.')
