"""Frozen original schema; never import current application models here."""
from alembic import op
from migrations.baseline_schema import Base

revision = '0001'
down_revision = None

def upgrade():
    Base.metadata.create_all(op.get_bind())

def downgrade():
    raise RuntimeError('Destructive downgrade disabled. Restore a verified backup instead.')
