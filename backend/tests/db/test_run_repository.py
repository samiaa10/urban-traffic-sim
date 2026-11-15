"""Simulation runs and their history in PostgreSQL (backend/db/repository.py).

Covers what the repository writes, and that the database itself refuses
data that does not make sense (the check constraints, composite foreign
keys and the partial unique index), whatever code writes it.
"""

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.db.models import (
    Emergency,
    EmergencyStatus,
    EmergencyVehicle,
    EventType,
    FleetSpawn,
    Incident,
    IncidentEndReason,
    IncidentKind,
    OptimiserRun,
    RunEvent,
    RunMetric,
    RunStatus,
    SimulationRun,
    Station,
    Surge,
)
from backend.db.repository import Road, RunRepository
from backend.simulation.accidents import Accident


GRAPH = "a" * 64

ROAD = Road((52.62, 1.28), (52.62, 1.283), start_node=0, end_node=1)
OTHER_ROAD = Road((52.62, 1.283), (52.62, 1.286), start_node=1, end_node=2)


def new_run(repo, **options):
    return repo.create_run(uuid.uuid4(), 0, 15, GRAPH, **options)


def event_types(repo, run_id):
    return [event.event_type for event in repo.events(run_id)]


def expect_refused(session, row):
    """The database rejects `row` (and the session carries on)."""

    with pytest.raises(IntegrityError):
        with session.begin_nested():
            session.add(row)
            session.flush()


def run_with_ambulance(repo):
    run = new_run(repo)
    station = repo.add_station(run.id, 0, "ambulance", 52.62, 1.28, 0)
    vehicle = repo.add_emergency_vehicle(run.id, station.id, 2, "ambulance", 20, 0)
    return run, station, vehicle


# ------------------------------------------------------------
# Runs
# ------------------------------------------------------------

def test_a_new_run_is_active_and_starts_its_timeline(session):
    repo = RunRepository(session)
    run = new_run(repo)

    stored = repo.get_run(run.id)

    assert stored.status == RunStatus.ACTIVE
    assert stored.ended_at is None
    assert stored.created_at is not None
    assert stored.rerouting_enabled is True
    assert [(e.seq, e.event_type) for e in repo.events(run.id)] == [
        (1, EventType.RUN_STARTED)
    ]


def test_runs_are_listed_newest_first_with_a_total(session):
    repo = RunRepository(session)
    runs = [new_run(repo) for _ in range(5)]
    repo.end_run(runs[0].id, RunStatus.STOPPED)

    page, total = repo.list_runs(limit=2)
    active, active_total = repo.list_runs(status=RunStatus.ACTIVE)

    assert total == 5 and len(page) == 2
    assert page[0].created_at >= page[1].created_at
    assert active_total == 4
    assert runs[0].id not in {run.id for run in active}


def test_progress_is_recorded(session):
    repo = RunRepository(session)
    run = new_run(repo)

    repo.record_progress(run.id, sim_time=42, vehicles_total=10, vehicles_finished=3)
    session.expire_all()

    stored = repo.get_run(run.id)
    assert (stored.sim_time, stored.vehicles_total, stored.vehicles_finished) == (42, 10, 3)


def test_a_run_cannot_have_more_finished_vehicles_than_vehicles(session):
    expect_refused(session, SimulationRun(
        id=uuid.uuid4(), start_node=0, destination_node=1, graph_sha256=GRAPH,
        vehicles_total=2, vehicles_finished=3,
    ))


def test_an_ended_run_must_say_when_it_ended(session):
    expect_refused(session, SimulationRun(
        id=uuid.uuid4(), start_node=0, destination_node=1, graph_sha256=GRAPH,
        status=RunStatus.STOPPED,
    ))


def test_ending_a_run_ends_what_was_still_going_on(session):
    repo = RunRepository(session)
    run, _, vehicle = run_with_ambulance(repo)

    repo.open_incident(run.id, IncidentKind.CLOSURE, ROAD, 5)
    repo.open_incident(
        run.id, IncidentKind.ACCIDENT, OTHER_ROAD, 6,
        accident=Accident((OTHER_ROAD.start, OTHER_ROAD.end), "minor", 6),
    )
    repo.dispatch_emergency(run.id, "ambulance", 52.63, 1.29, 7, vehicle.id, 900.0, 40)
    repo.record_progress(run.id, 30, 3, 0)

    assert repo.end_run(run.id, RunStatus.STOPPED) is True
    session.expire_all()

    stored = repo.get_run(run.id)
    assert stored.status == RunStatus.STOPPED
    assert stored.ended_at is not None
    assert all(
        (i.ended_sim_time, i.end_reason) == (30, IncidentEndReason.RUN_ENDED)
        for i in repo.incidents(run.id)
    )
    assert [e.status for e in repo.emergencies(run.id)] == [EmergencyStatus.RUN_ENDED]
    assert event_types(repo, run.id)[-1] == EventType.RUN_ENDED

    # Ending it again does nothing
    assert repo.end_run(run.id, RunStatus.STOPPED) is False


def test_runs_live_when_the_server_stopped_are_marked_interrupted(session):
    repo = RunRepository(session)
    live = [new_run(repo), new_run(repo)]
    stopped = new_run(repo)
    repo.end_run(stopped.id, RunStatus.STOPPED)

    interrupted = repo.interrupt_active_runs()
    session.expire_all()

    assert set(interrupted) == {run.id for run in live}
    assert {repo.get_run(run.id).status for run in live} == {RunStatus.INTERRUPTED}
    assert repo.get_run(stopped.id).status == RunStatus.STOPPED


def test_committed_runs_survive_a_new_connection(engine):
    """Data is in the database, not in the process: a brand new engine (as
    after a server restart) reads back what an earlier one committed."""

    run_id = uuid.uuid4()

    with Session(engine) as first:
        repo = RunRepository(first)
        repo.create_run(run_id, 0, 15, GRAPH)
        repo.open_incident(run_id, IncidentKind.CLOSURE, ROAD, 3)
        first.commit()

    engine.dispose()     # drop every pooled connection

    try:
        with Session(engine) as second:
            repo = RunRepository(second)
            assert repo.get_run(run_id).status == RunStatus.ACTIVE
            assert [i.start_node for i in repo.incidents(run_id)] == [0]
    finally:
        with Session(engine) as cleanup:
            RunRepository(cleanup).delete_run(run_id)
            cleanup.commit()


# ------------------------------------------------------------
# Timeline
# ------------------------------------------------------------

def test_events_are_numbered_in_order(session):
    repo = RunRepository(session)
    run, station, vehicle = run_with_ambulance(repo)
    repo.open_incident(run.id, IncidentKind.CLOSURE, ROAD, 4)

    events = repo.events(run.id)

    assert [e.seq for e in events] == [1, 2, 3, 4]
    assert [e.event_type for e in events] == [
        EventType.RUN_STARTED, EventType.STATION_ADDED,
        EventType.VEHICLE_ADDED, EventType.ROAD_CLOSED,
    ]
    assert events[1].station_id == station.id
    assert events[2].vehicle_id == vehicle.id
    assert repo.events(run.id, after_seq=2)[0].seq == 3


def test_an_event_must_link_to_what_it_is_about(session):
    repo = RunRepository(session)
    run = new_run(repo)

    expect_refused(session, RunEvent(
        run_id=run.id, seq=2, sim_time=0, event_type=EventType.ROAD_CLOSED,
    ))


def test_two_events_cannot_share_a_place_in_the_timeline(session):
    repo = RunRepository(session)
    run = new_run(repo)

    expect_refused(session, RunEvent(
        run_id=run.id, seq=1, sim_time=0, event_type=EventType.RUN_STARTED,
    ))


# ------------------------------------------------------------
# Stations, vehicles and emergencies
# ------------------------------------------------------------

def test_an_emergency_records_dispatch_and_arrival(session):
    repo = RunRepository(session)
    run, _, vehicle = run_with_ambulance(repo)

    emergency = repo.dispatch_emergency(
        run.id, "ambulance", 52.63, 1.29, 10, vehicle.id, 1234.5, 77
    )
    repo.emergency_arrived(emergency.id, arrived_sim_time=95, reroutes=2)
    session.expire_all()

    stored = repo.emergencies(run.id)[0]
    assert stored.status == EmergencyStatus.ARRIVED
    assert stored.response_seconds == 85
    assert stored.reroutes == 2
    assert stored.vehicle.vehicle_index == 2
    assert stored.vehicle.station.station_index == 0
    assert event_types(repo, run.id)[-2:] == [
        EventType.EMERGENCY_DISPATCHED, EventType.EMERGENCY_ARRIVED
    ]


def test_an_emergency_nothing_could_be_sent_to_is_kept_as_cancelled(session):
    repo = RunRepository(session)
    run = new_run(repo)

    repo.cancel_emergency(run.id, "ambulance", 52.63, 1.29, 3, "no_vehicle")

    stored = repo.emergencies(run.id)[0]
    assert (stored.status, stored.cancel_reason, stored.vehicle_id) == (
        EmergencyStatus.CANCELLED, "no_vehicle", None
    )


def test_an_emergency_cannot_use_another_runs_vehicle(session):
    repo = RunRepository(session)
    run_a = new_run(repo)
    _, _, vehicle_b = run_with_ambulance(repo)

    expect_refused(session, Emergency(
        run_id=run_a.id, emergency_type="ambulance", latitude=1, longitude=1,
        status=EmergencyStatus.DISPATCHED, vehicle_id=vehicle_b.id,
        created_sim_time=0,
    ))


def test_a_vehicle_cannot_belong_to_another_runs_station(session):
    repo = RunRepository(session)
    run_a = new_run(repo)
    _, station_b, _ = run_with_ambulance(repo)

    expect_refused(session, EmergencyVehicle(
        run_id=run_a.id, station_id=station_b.id, vehicle_index=0,
        vehicle_type="ambulance", speed=20, created_sim_time=0,
    ))


def test_an_arrived_emergency_must_have_its_response_time(session):
    repo = RunRepository(session)
    run, _, vehicle = run_with_ambulance(repo)

    expect_refused(session, Emergency(
        run_id=run.id, emergency_type="ambulance", latitude=1, longitude=1,
        status=EmergencyStatus.ARRIVED, vehicle_id=vehicle.id, created_sim_time=0,
    ))


def test_a_dispatched_emergency_must_have_a_vehicle(session):
    repo = RunRepository(session)
    run = new_run(repo)

    expect_refused(session, Emergency(
        run_id=run.id, emergency_type="ambulance", latitude=1, longitude=1,
        status=EmergencyStatus.DISPATCHED, created_sim_time=0,
    ))


def test_vehicles_need_a_positive_speed_and_stations_unique_numbers(session):
    repo = RunRepository(session)
    run, station, _ = run_with_ambulance(repo)

    expect_refused(session, EmergencyVehicle(
        run_id=run.id, station_id=station.id, vehicle_index=9,
        vehicle_type="ambulance", speed=0, created_sim_time=0,
    ))
    expect_refused(session, Station(
        run_id=run.id, station_index=0, station_type="ambulance",
        latitude=1, longitude=1, created_sim_time=0,
    ))


# ------------------------------------------------------------
# Incidents
# ------------------------------------------------------------

def test_a_closure_is_recorded_until_the_road_reopens(session):
    repo = RunRepository(session)
    run = new_run(repo)

    repo.open_incident(run.id, IncidentKind.CLOSURE, ROAD, 5)
    closed = repo.close_incident(
        run.id, IncidentKind.CLOSURE, ROAD, 12, IncidentEndReason.REOPENED
    )

    assert (closed.started_sim_time, closed.ended_sim_time) == (5, 12)
    assert closed.end_reason == IncidentEndReason.REOPENED
    assert (closed.start_node, closed.end_node) == (0, 1)
    assert repo.incidents(run.id, open_only=True) == []
    assert event_types(repo, run.id)[-2:] == [
        EventType.ROAD_CLOSED, EventType.ROAD_REOPENED
    ]


def test_reopening_a_road_that_is_not_closed_records_nothing(session):
    repo = RunRepository(session)
    run = new_run(repo)

    assert repo.close_incident(
        run.id, IncidentKind.CLOSURE, ROAD, 1, IncidentEndReason.REOPENED
    ) is None
    assert event_types(repo, run.id) == [EventType.RUN_STARTED]


def test_a_road_cannot_be_closed_twice_at_once_but_can_be_closed_again(session):
    repo = RunRepository(session)
    run = new_run(repo)
    repo.open_incident(run.id, IncidentKind.CLOSURE, ROAD, 1)

    with pytest.raises(IntegrityError):
        with session.begin_nested():
            repo.open_incident(run.id, IncidentKind.CLOSURE, ROAD, 2, seq=99)

    repo.close_incident(run.id, IncidentKind.CLOSURE, ROAD, 3, IncidentEndReason.REOPENED)
    repo.open_incident(run.id, IncidentKind.CLOSURE, ROAD, 4)

    assert len(repo.incidents(run.id)) == 2


def test_a_new_accident_on_the_same_road_replaces_the_old_one(session):
    repo = RunRepository(session)
    run = new_run(repo)
    segment = (ROAD.start, ROAD.end)

    first = repo.open_incident(
        run.id, IncidentKind.ACCIDENT, ROAD, 10,
        accident=Accident(segment, "minor", 10),
    )
    second = repo.open_incident(
        run.id, IncidentKind.ACCIDENT, ROAD, 20,
        accident=Accident(segment, "severe", 20, duration=600),
    )

    assert (first.ended_sim_time, first.end_reason) == (20, IncidentEndReason.REPLACED)
    assert second.ended_sim_time is None
    assert second.severity == "severe"
    assert second.planned_end_sim_time == 620
    assert second.speed_factor == Accident(segment, "severe", 0).speed_factor


def test_closures_and_accidents_carry_only_their_own_details(session):
    repo = RunRepository(session)
    run = new_run(repo)

    def incident(**fields):
        values = dict(
            run_id=run.id, start_latitude=1, start_longitude=1,
            end_latitude=2, end_longitude=2, started_sim_time=0,
        )
        values.update(fields)
        return Incident(**values)

    # A closure with an accident severity
    expect_refused(session, incident(kind=IncidentKind.CLOSURE, severity="minor"))
    # An accident without its details
    expect_refused(session, incident(kind=IncidentKind.ACCIDENT))
    # An unknown severity
    expect_refused(session, incident(
        kind=IncidentKind.ACCIDENT, severity="apocalyptic", speed_factor=0.1,
        capacity_factor=0.1, planned_end_sim_time=10,
    ))
    # An accident that "reopened" (only closures reopen)
    expect_refused(session, incident(
        kind=IncidentKind.ACCIDENT, severity="minor", speed_factor=0.5,
        capacity_factor=0.5, planned_end_sim_time=10,
        ended_sim_time=5, end_reason=IncidentEndReason.REOPENED,
    ))
    # Ended before it started
    expect_refused(session, incident(
        kind=IncidentKind.CLOSURE, started_sim_time=10,
        ended_sim_time=5, end_reason=IncidentEndReason.REOPENED,
    ))


def test_a_closure_can_point_at_the_emergency_it_was_made_for(session):
    repo = RunRepository(session)
    run, _, vehicle = run_with_ambulance(repo)
    emergency = repo.dispatch_emergency(run.id, "ambulance", 1, 1, 0, vehicle.id, 10, 1)

    closure = repo.open_incident(
        run.id, IncidentKind.CLOSURE, ROAD, 3, emergency_id=emergency.id
    )

    assert closure.emergency.id == emergency.id


# ------------------------------------------------------------
# Demand and metrics
# ------------------------------------------------------------

def test_surges_and_fleets_are_recorded(session):
    repo = RunRepository(session)
    run = new_run(repo)

    surge = repo.add_surge(run.id, 52.63, 1.29, 800, "inbound", 60, 5, 905)
    repo.update_surge_counts(surge.id, spawned=40, failed=2)
    repo.add_fleet_spawn(run.id, 6, requested=100, spawned=97, failed=3, seed=4, wall_seconds=0.8)
    session.expire_all()

    assert (repo.surges(run.id)[0].spawned, repo.surges(run.id)[0].failed) == (40, 2)
    assert repo.fleet_spawns(run.id)[0].spawned == 97
    assert event_types(repo, run.id)[-2:] == [EventType.SURGE_STARTED, EventType.FLEET_ADDED]


def test_impossible_surges_and_fleets_are_refused(session):
    repo = RunRepository(session)
    run = new_run(repo)

    expect_refused(session, Surge(
        run_id=run.id, latitude=1, longitude=1, radius_metres=500,
        direction="inbound", vehicles_per_minute=10,
        starts_sim_time=100, ends_sim_time=50,
    ))
    expect_refused(session, FleetSpawn(
        run_id=run.id, sim_time=0, requested=10, spawned=9, failed=5, wall_seconds=1,
    ))


def test_metrics_are_kept_per_simulated_time(session):
    repo = RunRepository(session)
    run = new_run(repo)
    values = dict(
        vehicles_total=10, vehicles_finished=1, mean_speed_factor=0.8,
        congested_roads=2, closed_roads=0, active_accidents=0, reroutes_total=0,
    )

    for sim_time in (60, 120, 180):
        repo.add_metrics(run.id, sim_time, **values)
    session.flush()

    assert [m.sim_time for m in repo.metrics(run.id, from_time=100)] == [120, 180]

    expect_refused(session, RunMetric(run_id=run.id, sim_time=60, **values))
    expect_refused(session, RunMetric(
        run_id=run.id, sim_time=240, **{**values, "mean_speed_factor": 1.5}
    ))


# ------------------------------------------------------------
# Deleting
# ------------------------------------------------------------

def test_deleting_a_run_deletes_its_whole_history(session):
    repo = RunRepository(session)
    run, _, vehicle = run_with_ambulance(repo)
    emergency = repo.dispatch_emergency(run.id, "ambulance", 1, 1, 0, vehicle.id, 10, 1)
    repo.open_incident(run.id, IncidentKind.CLOSURE, ROAD, 1, emergency_id=emergency.id)
    repo.add_surge(run.id, 1, 1, 500, "outbound", 5, 0, 60)
    repo.add_fleet_spawn(run.id, 0, 5, 5, 0, None, 0.1)
    repo.add_metrics(
        run.id, 60, vehicles_total=1, vehicles_finished=0, mean_speed_factor=None,
        congested_roads=0, closed_roads=1, active_accidents=0, reroutes_total=0,
    )
    kept = new_run(repo)

    assert repo.delete_run(run.id) is True
    assert repo.delete_run(run.id) is False

    for model in (Station, EmergencyVehicle, Emergency, Incident, Surge,
                  FleetSpawn, RunEvent, RunMetric):
        remaining = session.scalar(
            select(func.count()).select_from(model).where(model.run_id == run.id)
        )
        assert remaining == 0, model.__tablename__

    assert repo.get_run(kept.id) is not None


def test_deleting_a_run_keeps_optimisations_that_used_it(session):
    repo = RunRepository(session)
    run = new_run(repo)

    optimisation = OptimiserRun(
        id=uuid.uuid4(), kind="ranking", simulation_run_id=run.id, scenarios=10,
        seed=1, target_seconds=480, speed=20, candidate_source="default",
        candidates_considered=3, closed_roads=0, graph_sha256=GRAPH,
        request_key="k" * 64, explanation=["x"], compute_ms=1.0,
    )
    session.add(optimisation)
    session.flush()

    repo.delete_run(run.id)
    session.expire_all()

    assert session.get(OptimiserRun, optimisation.id).simulation_run_id is None
