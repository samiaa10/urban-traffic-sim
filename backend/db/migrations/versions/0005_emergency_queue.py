"""Emergency queue: emergencies.dispatched_sim_time, 'still_queued', and
scenario_results.emergency_detail.

With the realistic emergency rules (osm traffic model) an emergency with
no free vehicle waits in a queue. dispatched_sim_time records when a
vehicle was sent, so the wait (dispatched - created) is kept apart from the
drive; an emergency still waiting when the run ends is saved as cancelled
with the new reason 'still_queued'. Scenario results also keep the queue
and per-service emergency figures (emergency_detail, JSONB). Existing rows
keep NULL. Checked by
`alembic check` against the models; no enum types involved.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '0005'
down_revision: Union[str, Sequence[str], None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_REASONS = "cancel_reason IS NULL OR cancel_reason IN ('no_vehicle', 'no_route')"
NEW_REASONS = "cancel_reason IS NULL OR cancel_reason IN ('no_vehicle', 'no_route', 'still_queued')"


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('emergencies', sa.Column('dispatched_sim_time', sa.Float(), nullable=True))
    op.add_column(
        'scenario_results',
        sa.Column('emergency_detail', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.drop_constraint(op.f('ck_emergencies_known_cancel_reason'), 'emergencies', type_='check')
    op.create_check_constraint(op.f('ck_emergencies_known_cancel_reason'), 'emergencies', NEW_REASONS)


def downgrade() -> None:
    """Downgrade schema (emergencies cancelled as still_queued become no_vehicle)."""
    op.execute(
        "UPDATE emergencies SET cancel_reason = 'no_vehicle' WHERE cancel_reason = 'still_queued'"
    )
    op.drop_constraint(op.f('ck_emergencies_known_cancel_reason'), 'emergencies', type_='check')
    op.create_check_constraint(op.f('ck_emergencies_known_cancel_reason'), 'emergencies', OLD_REASONS)
    op.drop_column('scenario_results', 'emergency_detail')
    op.drop_column('emergencies', 'dispatched_sim_time')
