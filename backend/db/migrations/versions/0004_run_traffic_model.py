"""Traffic model of each run: simulation_runs.traffic_model.

Which rules the run's roads followed (simulation/road_model.py): "simple"
(the original rules) or "osm" (OpenStreetMap speed limits, lanes and
per-direction traffic). Restarting a run uses the same model. Runs saved
before this migration used the original rules, so existing rows get
"simple" (the server default). Checked by `alembic check` against the
models; no enum types involved.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-16

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0004'
down_revision: Union[str, Sequence[str], None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'simulation_runs',
        sa.Column(
            'traffic_model',
            sa.String(length=16),
            server_default=sa.text("'simple'"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f('ck_simulation_runs_known_traffic_model'),
        'simulation_runs',
        "traffic_model IN ('simple', 'osm')",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        op.f('ck_simulation_runs_known_traffic_model'), 'simulation_runs', type_='check'
    )
    op.drop_column('simulation_runs', 'traffic_model')
