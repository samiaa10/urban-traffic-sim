"""Station evaluations: the optimiser's best answers tested in traffic.

One row per evaluation (optimiser/evaluation.py), linked to the scenario
study that runs its simulations (deleted with it). Checked by `alembic
check` against the models; no enum types involved.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '0006'
down_revision: Union[str, Sequence[str], None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'station_evaluations',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('study_id', sa.UUID(), nullable=False),
        sa.Column('settings', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('candidates', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('plan', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(
            ['study_id'], ['scenario_studies.id'],
            name=op.f('fk_station_evaluations_study_id_scenario_studies'), ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_station_evaluations')),
        sa.UniqueConstraint('study_id', name=op.f('uq_station_evaluations_study_id')),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('station_evaluations')
