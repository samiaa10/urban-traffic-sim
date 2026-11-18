"""Allow the experimental cellular-automaton traffic model in runs.

simulation_runs.traffic_model may now also be "ca" (simulation/cellular.py).
Only the check constraint changes; no rows do. Downgrading fails if a run
used "ca", rather than silently relabel it.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-18

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '0007'
down_revision: Union[str, Sequence[str], None] = '0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NAME = 'ck_simulation_runs_known_traffic_model'


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(op.f(NAME), 'simulation_runs', type_='check')
    op.create_check_constraint(
        op.f(NAME), 'simulation_runs', "traffic_model IN ('simple', 'osm', 'ca')",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f(NAME), 'simulation_runs', type_='check')
    op.create_check_constraint(
        op.f(NAME), 'simulation_runs', "traffic_model IN ('simple', 'osm')",
    )
