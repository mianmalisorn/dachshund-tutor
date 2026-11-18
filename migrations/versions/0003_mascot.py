"""Private preferences and bounded frequency state, independent from homework."""
from alembic import op
import sqlalchemy as sa

revision = '0003'
down_revision = '0002'

def upgrade():
    op.create_table('mascot_preferences',
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), primary_key=True),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('frequency', sa.String(12), nullable=False),
        sa.Column('tone', sa.String(12), nullable=False),
        sa.Column('rare_care', sa.Boolean(), nullable=False),
        sa.Column('easter_eggs', sa.Boolean(), nullable=False),
        sa.Column('animations', sa.Boolean(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('state_json', sa.Text(), nullable=False),
        sa.CheckConstraint("frequency IN ('rare','normal','frequent')", name='mascot_frequency_check'),
        sa.CheckConstraint("tone IN ('soft','medium','spicy')", name='mascot_tone_check'))

def downgrade():
    op.drop_table('mascot_preferences')
