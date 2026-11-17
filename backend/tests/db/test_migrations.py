"""The Alembic migrations build exactly the schema the models describe."""

from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect, text

from backend.db.models import Base
from backend.tests.db.conftest import downgrade, migrate


TABLES = {
    "simulation_runs", "stations", "emergency_vehicles", "emergencies",
    "incidents", "surges", "fleet_spawns", "run_events", "run_metrics",
    "optimiser_runs", "optimiser_candidates", "optimiser_selected_stations",
    "optimiser_baselines",
    # 0002: scenario studies
    "scenario_studies", "scenarios", "scenario_results", "scenario_road_impacts",
    "scenario_area_impacts", "scenario_timeseries",
}

ENUM_TYPES = {
    "run_status", "incident_kind", "incident_end_reason", "emergency_status",
    "surge_direction", "event_type", "optimiser_kind", "candidate_source",
    "study_status", "scenario_status",
}


def tables(engine):
    return set(inspect(engine).get_table_names()) - {"alembic_version"}


def enum_types(engine):
    with engine.connect() as connection:
        return set(connection.scalars(text(
            "SELECT typname FROM pg_type t JOIN pg_namespace n "
            "ON n.oid = t.typnamespace "
            "WHERE t.typtype = 'e' AND n.nspname = 'public'"
        )))


def test_upgrade_creates_every_table_and_type(engine):
    assert tables(engine) == TABLES
    assert enum_types(engine) == ENUM_TYPES


def test_the_models_and_the_migrations_agree(engine):
    """Same check as `alembic check`: nothing left to autogenerate."""

    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection, opts={"compare_type": True, "compare_server_default": True}
        )
        assert compare_metadata(context, Base.metadata) == []


def test_downgrade_removes_everything_and_upgrade_works_again(engine):
    downgrade(engine, "base")

    try:
        assert tables(engine) == set()
        # Enum types are dropped too, otherwise the next upgrade would fail
        assert enum_types(engine) == set()
    finally:
        migrate(engine, "head")

    assert tables(engine) == TABLES
    assert enum_types(engine) == ENUM_TYPES


def test_the_database_is_at_the_latest_revision(engine):
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0004"


def test_the_traffic_model_migration_can_be_undone_on_its_own(engine):
    """0004 down to 0003 removes only simulation_runs.traffic_model; going
    up again gives runs saved before it the simple model they used."""

    import uuid

    def columns():
        return {c["name"] for c in inspect(engine).get_columns("simulation_runs")}

    run_id = uuid.uuid4()
    downgrade(engine, "0003")
    try:
        assert "traffic_model" not in columns()
        assert tables(engine) == TABLES
        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO simulation_runs (id, start_node, destination_node, graph_sha256) "
                "VALUES (:id, 1, 2, 'x')"
            ), {"id": run_id})
    finally:
        migrate(engine, "head")

    with engine.begin() as connection:
        model = connection.scalar(
            text("SELECT traffic_model FROM simulation_runs WHERE id = :id"), {"id": run_id}
        )
        connection.execute(text("DELETE FROM simulation_runs WHERE id = :id"), {"id": run_id})

    assert "traffic_model" in columns()
    assert model == "simple"


def test_only_known_traffic_models_can_be_saved(engine):
    import uuid

    import pytest
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError, match="known_traffic_model"):
        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO simulation_runs (id, start_node, destination_node, graph_sha256, traffic_model) "
                "VALUES (:id, 1, 2, 'x', 'fast')"
            ), {"id": uuid.uuid4()})


def test_the_restart_link_migration_can_be_undone_on_its_own(engine):
    """0003 down to 0002 removes only simulation_runs.restarted_from."""

    def columns():
        return {c["name"] for c in inspect(engine).get_columns("simulation_runs")}

    assert "restarted_from" in columns()
    downgrade(engine, "0002")
    try:
        assert "restarted_from" not in columns()
        assert tables(engine) == TABLES
    finally:
        migrate(engine, "head")
    assert "restarted_from" in columns()


def test_the_scenario_migration_can_be_undone_on_its_own(engine):
    """0002 down to 0001 leaves exactly the Stage 3 schema."""

    downgrade(engine, "0001")

    try:
        assert tables(engine) == TABLES - {
            "scenario_studies", "scenarios", "scenario_results",
            "scenario_road_impacts", "scenario_area_impacts", "scenario_timeseries",
        }
        assert enum_types(engine) == ENUM_TYPES - {"study_status", "scenario_status"}
    finally:
        migrate(engine, "head")

    assert tables(engine) == TABLES
