"""Scenario studies in PostgreSQL (backend/db/scenario_repository.py)."""

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from backend.db.models import (
    Scenario,
    ScenarioAreaImpact,
    ScenarioResult,
    ScenarioRoadImpact,
    ScenarioStatus,
    ScenarioStudy,
    ScenarioTimeseries,
    StudyStatus,
)
from backend.db.scenario_repository import ScenarioRepository, result_from_rows
from backend.scenarios.compare import compare
from backend.scenarios.runner import ENGINE_VERSION, World, run_scenario
from backend.scenarios.spec import ScenarioSpec
from backend.tests.helpers import make_grid_graph


GRAPH = "c" * 64
CUT = [{"start": 3 * 8 + c, "destination": 4 * 8 + c} for c in range(1, 8)]


@pytest.fixture(scope="module")
def world():
    return World(make_grid_graph(size=8, seed=7))


@pytest.fixture(scope="module")
def specs():
    return [
        ScenarioSpec.model_validate({"name": "Baseline", "seed": 4, "demand": {"vehicles": 40}}),
        ScenarioSpec.model_validate(
            {"name": "Cut", "seed": 4, "demand": {"vehicles": 40}, "closures": CUT}
        ),
        ScenarioSpec.model_validate(
            {"name": "Short", "seed": 4, "demand": {"vehicles": 40}, "max_seconds": 60}
        ),
    ]


@pytest.fixture(scope="module")
def results(world, specs):
    return [run_scenario(world, spec) for spec in specs]


def new_study(repo, specs, baseline=0):
    return repo.create_study(
        "Study", specs, "total_delay_vehicle_hours", 2, GRAPH, ENGINE_VERSION,
        baseline_position=baseline,
    )


def run_all(repo, study, results):
    for scenario, result in zip(repo.scenarios(study.id), results):
        assert repo.start_scenario(scenario.id)
        assert repo.save_result(scenario.id, result)


def expect_refused(session, row):
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            session.add(row)
            session.flush()


def test_a_study_starts_with_every_scenario_queued(session, specs):
    repo = ScenarioRepository(session)
    study = new_study(repo, specs)

    scenarios = repo.scenarios(study.id)

    assert study.status == StudyStatus.QUEUED
    assert study.scenario_count == 3
    assert [s.position for s in scenarios] == [0, 1, 2]
    assert [s.name for s in scenarios] == ["Baseline", "Cut", "Short"]
    assert [s.is_baseline for s in scenarios] == [True, False, False]
    assert all(s.status == ScenarioStatus.QUEUED for s in scenarios)
    assert scenarios[0].spec == specs[0].model_dump(mode="json")
    assert scenarios[0].spec_fingerprint == specs[0].fingerprint()
    assert scenarios[0].trip_signature == scenarios[1].trip_signature


def test_a_saved_result_comes_back_exactly(session, specs, results):
    repo = ScenarioRepository(session)
    study = new_study(repo, specs)
    run_all(repo, study, results)
    session.expire_all()

    for scenario, original in zip(repo.scenarios(study.id), results):
        loaded = result_from_rows(repo.get_scenario(scenario.id, full=True))
        for key in ("summary", "emergencies", "trips", "roads", "areas", "timeseries", "execution"):
            assert loaded[key] == original[key], key
        assert loaded["trip_signature"] == original["trip_signature"]


def test_finishing_every_scenario_completes_the_study(session, specs, results):
    repo = ScenarioRepository(session)
    study = new_study(repo, specs)
    repo.start_study(study.id)
    run_all(repo, study, results)
    session.expire_all()

    study = repo.get_study(study.id)
    assert study.status == StudyStatus.COMPLETED
    assert study.completed_count == 3
    assert study.finished_at is not None and study.wall_seconds >= 0


def test_a_failed_scenario_is_counted_and_needs_its_error(session, specs, results):
    repo = ScenarioRepository(session)
    study = new_study(repo, specs)
    scenarios = repo.scenarios(study.id)

    repo.start_scenario(scenarios[0].id)
    repo.save_result(scenarios[0].id, results[0])
    assert repo.fail_scenario(scenarios[1].id, "junction 9 is not on the map")
    repo.start_scenario(scenarios[2].id)
    repo.save_result(scenarios[2].id, results[2])
    session.expire_all()

    study = repo.get_study(study.id)
    assert (study.completed_count, study.failed_count, study.status) == (2, 1, StudyStatus.COMPLETED)
    assert repo.get_scenario(scenarios[1].id).error == "junction 9 is not on the map"

    # The database insists a failed scenario says why
    bad = repo.get_scenario(scenarios[0].id)
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            bad.status = ScenarioStatus.FAILED
            session.flush()


def test_cancelling_stops_the_scenarios_not_yet_finished(session, specs, results):
    repo = ScenarioRepository(session)
    study = new_study(repo, specs)
    first, second, third = repo.scenarios(study.id)
    repo.start_study(study.id)
    repo.start_scenario(first.id)
    repo.save_result(first.id, results[0])
    repo.start_scenario(second.id)

    assert repo.end_study(study.id, StudyStatus.CANCELLED) is True
    assert repo.end_study(study.id, StudyStatus.CANCELLED) is False
    session.expire_all()

    statuses = [s.status for s in repo.scenarios(study.id)]
    assert statuses == [ScenarioStatus.COMPLETED, ScenarioStatus.CANCELLED, ScenarioStatus.CANCELLED]

    # A result that arrives after the cancel is not stored
    assert repo.save_result(second.id, results[1]) is False
    assert repo.start_scenario(third.id) is False
    assert repo.get_study(study.id).status == StudyStatus.CANCELLED


def test_unfinished_studies_are_interrupted_at_start_up(session, specs):
    repo = ScenarioRepository(session)
    running = new_study(repo, specs)
    repo.start_study(running.id)
    queued = new_study(repo, specs)

    interrupted = repo.interrupt_unfinished()
    session.expire_all()

    assert set(interrupted) >= {running.id, queued.id}
    assert repo.get_study(running.id).status == StudyStatus.INTERRUPTED
    assert {s.status for s in repo.scenarios(queued.id)} == {ScenarioStatus.INTERRUPTED}


def test_studies_are_listed_newest_first(session, specs):
    repo = ScenarioRepository(session)
    ids = [new_study(repo, specs).id for _ in range(3)]

    rows, total = repo.list_studies(limit=2)

    assert total >= 3 and len(rows) == 2
    assert rows[0].created_at >= rows[1].created_at
    assert set(ids) & {row.id for row in repo.list_studies(limit=50)[0]} == set(ids)


def test_deleting_a_study_deletes_everything_in_it(session, specs, results):
    repo = ScenarioRepository(session)
    study = new_study(repo, specs)
    run_all(repo, study, results)
    scenario_ids = [s.id for s in repo.scenarios(study.id)]

    assert repo.delete_study(study.id)

    for model, column in (
        (Scenario, Scenario.id), (ScenarioResult, ScenarioResult.scenario_id),
        (ScenarioRoadImpact, ScenarioRoadImpact.scenario_id),
        (ScenarioAreaImpact, ScenarioAreaImpact.scenario_id),
        (ScenarioTimeseries, ScenarioTimeseries.scenario_id),
    ):
        count = session.scalar(select(func.count()).select_from(model).where(column.in_(scenario_ids)))
        assert count == 0, model.__tablename__


def test_comparing_stored_results_gives_the_same_answer(session, specs, results):
    repo = ScenarioRepository(session)
    study = new_study(repo, specs)
    run_all(repo, study, results)
    session.expire_all()

    fresh = [
        {"name": spec.name, "spec": spec.model_dump(mode="json"), "result": result}
        for spec, result in zip(specs, results)
    ]
    stored = [
        {"name": s.name, "spec": s.spec, "result": result_from_rows(repo.get_scenario(s.id, full=True))}
        for s in repo.scenarios(study.id)
    ]

    assert compare(stored, baseline_name="Baseline") == compare(fresh, baseline_name="Baseline")


# ------------------------------------------------------------
# Rules the database enforces
# ------------------------------------------------------------

def test_one_baseline_per_study(session, specs):
    repo = ScenarioRepository(session)
    study = new_study(repo, specs)

    expect_refused(session, Scenario(
        id=uuid.uuid4(), study_id=study.id, position=9, name="Second baseline",
        is_baseline=True, spec={}, seed=1, spec_fingerprint="f" * 64, trip_signature="t" * 16,
    ))


def test_a_result_needs_one_entry_per_trip(session, specs, results):
    repo = ScenarioRepository(session)
    study = new_study(repo, specs)
    scenario = repo.scenarios(study.id)[0]
    repo.start_scenario(scenario.id)

    broken = dict(results[0])
    broken["trips"] = dict(results[0]["trips"], status=results[0]["trips"]["status"][:-1])

    with pytest.raises(IntegrityError):
        with session.begin_nested():
            repo.save_result(scenario.id, broken)


def test_study_counts_must_add_up(session):
    expect_refused(session, ScenarioStudy(
        id=uuid.uuid4(), name="Bad", metric="m", scenario_count=2, completed_count=2,
        failed_count=1, workers=1, graph_sha256=GRAPH, engine_version=1,
    ))
    expect_refused(session, ScenarioStudy(
        id=uuid.uuid4(), name="Too big", metric="m", scenario_count=6000,
        workers=1, graph_sha256=GRAPH, engine_version=1,
    ))
