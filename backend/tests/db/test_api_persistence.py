"""The API saving runs to PostgreSQL, and reading them back.

Each test builds apps with create_app on the tiny test map, pointed at the
test database. A second create_app on the same database is what a server
restart looks like: the first app's memory is gone, the database is not.
"""

import uuid

import pytest
from sqlalchemy import event, text

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from backend.api.app import create_app  # noqa: E402


# ------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------

@pytest.fixture
def app(make_app):
    return make_app()


@pytest.fixture
def client(app):
    return TestClient(app)


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

def point(app, node):
    data = app.state.context.graph.nodes[node]
    return {"latitude": data["latitude"], "longitude": data["longitude"]}


def start(client, start=0, destination=15):
    response = client.post(
        "/simulation/start", params={"start": start, "destination": destination}
    )
    assert response.status_code == 200
    return response.json()["simulation_id"]


def steps(client, count, simulation_id=None):
    path = f"/simulations/{simulation_id}/step" if simulation_id else "/simulation/step"
    for _ in range(count):
        assert client.post(path).status_code == 200


def add_ambulance(client, app, node=0):
    client.post("/station/create", params=point(app, node))
    assert client.post("/station/add-vehicle", params={"station_id": 0}).status_code == 200


def history(client, simulation_id, part=""):
    response = client.get(f"/simulations/{simulation_id}/history{part}")
    assert response.status_code == 200, response.text
    return response.json()


def event_types(client, simulation_id):
    return [e["type"] for e in history(client, simulation_id, "/events")["events"]]


# ------------------------------------------------------------
# Recording a run
# ------------------------------------------------------------

def test_a_started_run_is_saved_and_listed(client):
    simulation_id = start(client)

    listed = client.get("/simulations").json()

    assert listed["persistence"] is True
    assert listed["total"] == 1
    run = listed["simulations"][0]
    assert run["simulation_id"] == simulation_id
    assert (run["status"], run["live"], run["is_default"], run["recording"]) == (
        "active", True, True, "on"
    )
    assert len(run["graph_sha256"]) == 64
    assert event_types(client, simulation_id) == ["run_started"]


def test_closures_and_reopenings_are_recorded(client):
    simulation_id = start(client)
    road = {"start": 5, "destination": 6}

    client.post("/incident/block", params=road)
    steps(client, 3)
    client.post("/incident/unblock", params=road)

    incidents = history(client, simulation_id, "/incidents")["incidents"]

    assert len(incidents) == 1
    closure = incidents[0]
    assert closure["kind"] == "closure"
    assert {closure["start_node"], closure["end_node"]} == {5, 6}
    assert (closure["started_at"], closure["ended_at"], closure["end_reason"]) == (
        0, 3, "reopened"
    )
    assert event_types(client, simulation_id)[-2:] == ["road_closed", "road_reopened"]


def test_accidents_record_whether_they_expired_or_were_cleared(client):
    simulation_id = start(client)

    client.post(
        "/incident/accident",
        params={"start": 5, "destination": 6, "severity": "minor", "duration_seconds": 4},
    )
    client.post(
        "/incident/accident",
        params={"start": 9, "destination": 10, "severity": "severe"},
    )
    steps(client, 6)                                 # the minor one runs out
    client.post("/incident/accident/clear", params={"start": 9, "destination": 10})

    accidents = {
        frozenset((a["start_node"], a["end_node"])): a
        for a in history(client, simulation_id, "/incidents?kind=accident")["incidents"]
    }

    minor = accidents[frozenset((5, 6))]
    severe = accidents[frozenset((9, 10))]

    assert (minor["severity"], minor["end_reason"], minor["ended_at"]) == ("minor", "expired", 4)
    assert minor["planned_end_at"] == 4
    assert (severe["severity"], severe["end_reason"], severe["ended_at"]) == ("severe", "cleared", 6)


def test_an_emergency_is_recorded_from_dispatch_to_arrival(client, app):
    simulation_id = start(client)
    add_ambulance(client, app)

    dispatched = client.post("/emergency/create", params=point(app, 15))
    assert dispatched.status_code == 200

    for _ in range(400):
        status = client.get("/emergency/status").json()["emergencies"][0]
        if status["state"] == "arrived":
            break
        steps(client, 1)

    saved = history(client, simulation_id, "/emergencies")["emergencies"]

    assert len(saved) == 1
    assert saved[0]["status"] == "arrived"
    assert saved[0]["vehicle_id"] == dispatched.json()["vehicle_id"]
    assert saved[0]["station_id"] == 0
    assert saved[0]["response_time_seconds"] == status["response_time_seconds"]
    assert saved[0]["route_distance_metres"] == dispatched.json()["route_distance_metres"]

    stations = history(client, simulation_id, "/stations")["stations"]
    assert [v["vehicle_id"] for v in stations[0]["vehicles"]] == [saved[0]["vehicle_id"]]

    assert event_types(client, simulation_id) == [
        "run_started", "station_added", "vehicle_added",
        "emergency_dispatched", "emergency_arrived",
    ]
    assert history(client, simulation_id)["mean_response_seconds"] == saved[0]["response_time_seconds"]


def test_an_emergency_nothing_could_answer_is_recorded_as_refused(client, app):
    simulation_id = start(client)

    response = client.post("/emergency/create", params=point(app, 15))

    assert response.status_code == 404           # unchanged API behaviour
    saved = history(client, simulation_id, "/emergencies")["emergencies"]
    assert [(e["status"], e["cancel_reason"]) for e in saved] == [("cancelled", "no_vehicle")]


def test_a_road_closed_in_front_of_an_ambulance_points_at_its_emergency(client, app):
    simulation_id = start(client)
    add_ambulance(client, app)
    client.post("/emergency/create", params=point(app, 15))

    assert client.post("/emergency/block-ahead").status_code == 200

    emergency = history(client, simulation_id, "/emergencies")["emergencies"][0]
    closure = history(client, simulation_id, "/incidents")["incidents"][0]

    assert closure["emergency_id"] == emergency["emergency_id"]


def test_fleets_surges_and_rerouting_changes_are_recorded(client, app):
    simulation_id = start(client)

    client.post("/simulation/fleet", params={"count": 5, "seed": 2})
    surge = client.post(
        "/traffic/surge",
        params={**point(app, 5), "radius_metres": 400, "vehicles_per_minute": 120,
                "duration_seconds": 30},
    )
    assert surge.status_code == 200
    client.post("/traffic/rerouting", params={"enabled": False})
    steps(client, 20)

    demand = history(client, simulation_id, "/demand")
    run = client.get(f"/simulations/{simulation_id}").json()

    assert demand["fleets"][0]["requested"] == 5
    assert demand["fleets"][0]["seed"] == 2
    assert demand["surges"][0]["direction"] == "inbound"
    # Surge counts follow the simulation as it runs
    live = client.get("/traffic/status").json()
    assert demand["surges"][0]["spawned"] == live["surges"][0]["spawned"] > 0
    assert history(client, simulation_id)["rerouting_enabled"] is False
    # Saved progress matches the live run (the surge changed every step,
    # so every step was saved)
    saved = history(client, simulation_id)
    assert (saved["vehicles"], saved["simulation_time"]) == (run["vehicles"], 20)
    assert run["vehicles"] > 7


def test_metrics_are_snapshotted_every_interval(client):
    simulation_id = start(client)
    client.post("/simulation/fleet", params={"count": 5, "seed": 1})

    steps(client, 35)

    metrics = history(client, simulation_id, "/metrics")["metrics"]

    assert [m["simulation_time"] for m in metrics] == [10, 20, 30]
    assert all(m["vehicles"] >= 2 for m in metrics)
    assert [m["simulation_time"] for m in history(
        client, simulation_id, "/metrics?from_time=15&to_time=30")["metrics"]] == [20, 30]


def test_quiet_steps_do_not_touch_the_database(make_app):
    """Nothing changed and no snapshot is due: no SQL at all."""

    app = make_app(metrics_interval=1000)
    client = TestClient(app)
    start(client)

    statements = []
    engine = app.state.context.database.engine
    listener = lambda *args: statements.append(args[2])   # noqa: E731
    event.listen(engine, "before_cursor_execute", listener)

    try:
        steps(client, 25)
    finally:
        event.remove(engine, "before_cursor_execute", listener)

    assert statements == []


def test_the_timeline_can_be_read_a_page_at_a_time(client):
    simulation_id = start(client)
    for node in (1, 2, 3):
        client.post("/incident/block", params={"start": node + 4, "destination": node + 8})

    first = history(client, simulation_id, "/events?limit=2")["events"]
    rest = history(client, simulation_id, f"/events?after_seq={first[-1]['seq']}")["events"]

    assert [e["seq"] for e in first + rest] == [1, 2, 3, 4]
    assert all("incident_id" in e for e in rest)


# ------------------------------------------------------------
# Ending runs
# ------------------------------------------------------------

def test_a_new_start_records_the_old_run_as_replaced(client):
    first = start(client)
    second = start(client)

    assert client.get(f"/simulations/{first}").json()["status"] == "replaced"
    assert client.get(f"/simulations/{second}").json()["status"] == "active"
    assert event_types(client, first)[-1] == "run_ended"


def test_stopping_keeps_the_history_and_purging_deletes_it(client):
    simulation_id = start(client)
    client.post("/incident/block", params={"start": 5, "destination": 6})

    stopped = client.delete(f"/simulations/{simulation_id}")
    assert stopped.json()["status"] == "simulation stopped"

    run = client.get(f"/simulations/{simulation_id}").json()
    assert (run["status"], run["live"]) == ("stopped", False)
    assert run["ended_at"] is not None

    # The closure ended with the run
    closure = history(client, simulation_id, "/incidents")["incidents"][0]
    assert closure["end_reason"] == "run_ended"

    # Stopping twice is a conflict, not a silent success
    assert client.delete(f"/simulations/{simulation_id}").status_code == 409

    purged = client.delete(f"/simulations/{simulation_id}", params={"purge": True})
    assert purged.json()["status"] == "simulation deleted"
    assert client.get(f"/simulations/{simulation_id}").status_code == 404
    assert client.get(f"/simulations/{simulation_id}/history").status_code == 404


def test_a_live_run_can_be_purged_directly(client):
    simulation_id = start(client)

    response = client.delete(f"/simulations/{simulation_id}", params={"purge": True})

    assert response.json()["status"] == "simulation deleted"
    assert client.get("/simulations").json()["total"] == 0


def test_a_run_stopped_to_make_room_is_recorded_as_stopped(make_app):
    client = TestClient(make_app(max_live=1))

    first = client.post("/simulations", params={"start": 0, "destination": 15}).json()["simulation_id"]
    client.post("/simulations", params={"start": 0, "destination": 15})

    assert client.get(f"/simulations/{first}").json()["status"] == "stopped"


def test_an_ended_run_cannot_be_driven_but_can_be_read(client):
    simulation_id = start(client)
    client.delete(f"/simulations/{simulation_id}")

    for method, path in (
        ("post", "/step"), ("get", "/incidents"), ("post", "/emergencies"),
    ):
        response = getattr(client, method)(f"/simulations/{simulation_id}{path}")
        assert response.status_code == 409, path
        assert "stopped" in response.json()["detail"]

    route = client.get("/route", params={"start": 0, "destination": 1,
                                         "simulation_id": simulation_id})
    assert route.status_code == 409
    assert history(client, simulation_id)["status"] == "stopped"


def test_runs_can_be_filtered_by_status_and_paged(client):
    ids = [start(client) for _ in range(4)]      # 3 replaced, 1 active

    replaced = client.get("/simulations", params={"status": "replaced"}).json()
    page = client.get("/simulations", params={"limit": 2, "offset": 1}).json()

    assert replaced["total"] == 3
    assert {r["simulation_id"] for r in replaced["simulations"]} == set(ids[:3])
    assert page["total"] == 4 and len(page["simulations"]) == 2
    assert client.get("/simulations", params={"status": "nonsense"}).status_code == 400


# ------------------------------------------------------------
# Restarts
# ------------------------------------------------------------

def test_history_survives_a_restart_and_live_runs_become_interrupted(make_app):
    before = TestClient(make_app())
    app = before.app

    simulation_id = start(before)
    add_ambulance(before, app)
    before.post("/incident/block", params={"start": 5, "destination": 6})
    before.post("/incident/accident", params={"start": 9, "destination": 10})
    before.post("/emergency/create", params=point(app, 15))
    steps(before, 12)

    incidents_before = history(before, simulation_id, "/incidents")["incidents"]
    events_before = history(before, simulation_id, "/events")["events"]

    # The server stops without warning (no shutdown) and starts again
    after = TestClient(make_app())

    run = after.get(f"/simulations/{simulation_id}").json()
    assert (run["status"], run["live"]) == ("interrupted", False)
    assert run["simulation_time"] >= 10          # progress saved by then
    assert run["ended_at"] is not None

    incidents = history(after, simulation_id, "/incidents")["incidents"]
    assert [i["incident_id"] for i in incidents] == [i["incident_id"] for i in incidents_before]
    assert {i["end_reason"] for i in incidents} == {"run_ended"}

    emergency = history(after, simulation_id, "/emergencies")["emergencies"][0]
    assert emergency["status"] in ("run_ended", "arrived")

    events = history(after, simulation_id, "/events")["events"]
    assert events[:len(events_before)] == events_before
    assert events[-1]["type"] == "run_ended"

    # It can be read but not driven, and nothing is live any more
    assert after.post(f"/simulations/{simulation_id}/step").status_code == 409
    assert after.post("/simulation/step").status_code == 404
    assert after.get("/simulations").json()["simulations"][0]["live"] is False


def test_a_clean_shutdown_records_live_runs_as_interrupted(make_app, engine):
    app = make_app()

    with TestClient(app) as client:
        simulation_id = start(client)

    with engine.connect() as connection:
        status = connection.scalar(
            text("SELECT status FROM simulation_runs WHERE id = :id"),
            {"id": uuid.UUID(simulation_id)},
        )

    assert status == "interrupted"


def test_the_api_refuses_to_start_on_an_unmigrated_database(engine, graph_file, database_url_for_tests):
    from backend.db.database import SchemaOutOfDate
    from backend.tests.db.conftest import downgrade, migrate

    downgrade(engine, "base")

    try:
        with pytest.raises(SchemaOutOfDate, match="alembic upgrade head"):
            create_app(graph_file, database_url=database_url_for_tests)
    finally:
        migrate(engine, "head")


# ------------------------------------------------------------
# When the database fails
# ------------------------------------------------------------

def broken_transaction():
    raise RuntimeError("database went away")


def test_a_run_is_not_started_if_it_cannot_be_saved(client, app, monkeypatch):
    monkeypatch.setattr(app.state.context.database, "transaction", broken_transaction)

    response = client.post("/simulations", params={"start": 0, "destination": 15})

    assert response.status_code == 503
    assert len(app.state.context.registry) == 0


def test_a_run_keeps_going_but_says_so_when_saving_fails(client, app, monkeypatch):
    simulation_id = start(client)
    monkeypatch.setattr(app.state.context.database, "transaction", broken_transaction)

    # The simulation itself carries on
    assert client.post("/incident/block", params={"start": 5, "destination": 6}).status_code == 200
    steps(client, 3)
    monkeypatch.undo()

    run = client.get(f"/simulations/{simulation_id}").json()
    assert run["recording"] == "failed"

    # And it does not quietly resume with a gap in its history
    client.post("/incident/block", params={"start": 9, "destination": 10})
    assert history(client, simulation_id, "/incidents")["incidents"] == []


def test_a_surges_final_counts_are_saved_when_it_ends(make_app):
    """Surge counts are not written every step, but the final ones always
    are, even with no metrics snapshot due."""

    app = make_app(metrics_interval=1000)
    client = TestClient(app)
    simulation_id = start(client)

    client.post(
        "/traffic/surge",
        params={**point(app, 5), "radius_metres": 400, "vehicles_per_minute": 300,
                "duration_seconds": 5},
    )
    steps(client, 3)

    # Mid-surge: nothing forced a write yet
    assert history(client, simulation_id, "/demand")["surges"][0]["spawned"] == 0

    steps(client, 5)

    live = client.get("/traffic/status").json()["surges"][0]
    saved = history(client, simulation_id, "/demand")["surges"][0]
    assert live["active"] is False
    assert (saved["spawned"], saved["failed"]) == (live["spawned"], live["failed"])
    assert saved["spawned"] > 0


# ------------------------------------------------------------
# Restarting an ended run from its saved setup
# ------------------------------------------------------------

def test_an_ended_run_restarts_from_its_saved_setup(make_app):
    before = TestClient(make_app())
    app = before.app
    old_id = start(before)
    add_ambulance(before, app)
    before.post("/incident/block", params={"start": 5, "destination": 6})
    before.post("/incident/accident", params={
        "start": 9, "destination": 10, "severity": "severe", "duration_seconds": 100,
    })
    before.post("/incident/block", params={"start": 1, "destination": 2})
    before.post("/incident/unblock", params={"start": 1, "destination": 2})
    before.post("/simulation/fleet", params={"count": 4, "seed": 7})
    before.post("/traffic/rerouting", params={"enabled": False})
    steps(before, 30)

    # The server stops; the run is interrupted
    after = TestClient(make_app())
    assert after.get(f"/simulations/{old_id}").json()["status"] == "interrupted"

    response = after.post(f"/simulations/{old_id}/restart")
    assert response.status_code == 201, response.text
    body = response.json()
    new_id = body["simulation_id"]

    assert body["restarted_from"] == old_id
    # Only what was still in place when it ended: the reopened road is not
    assert body["applied"] == {
        "stations": 1, "ambulances": 1, "closures": 1, "accidents": 1,
        "fleet_vehicles": body["applied"]["fleet_vehicles"],
    }
    assert body["applied"]["fleet_vehicles"] >= 1
    assert len(body["route"]) >= 2

    run = after.get(f"/simulations/{new_id}").json()
    assert (run["status"], run["live"], run["is_default"]) == ("active", True, True)
    assert run["restarted_from"] == old_id
    assert run["rerouting_enabled"] is False

    incidents = history(after, new_id, "/incidents")["incidents"]
    assert {(i["kind"], frozenset((i["start_node"], i["end_node"]))) for i in incidents} == {
        ("closure", frozenset((5, 6))), ("accident", frozenset((9, 10))),
    }
    accident = [i for i in incidents if i["kind"] == "accident"][0]
    # It had 100 s and was 30 s in: 70 s left
    assert accident["planned_end_at"] == 70
    assert len(history(after, new_id, "/stations")["stations"][0]["vehicles"]) == 1

    # The restarted run works like any other
    assert after.post("/simulation/step").status_code == 200


def test_a_restarted_fleet_has_the_same_trips(make_app):
    client = TestClient(make_app())
    old_id = start(client)
    client.post("/simulation/fleet", params={"count": 5, "seed": 3})
    before = [(v["vehicle_id"], v["latitude"], v["longitude"]) for v in client.post("/simulation/step").json()["vehicles"]]
    client.delete(f"/simulations/{old_id}")

    client.post(f"/simulations/{old_id}/restart")
    after = [(v["vehicle_id"], v["latitude"], v["longitude"]) for v in client.post("/simulation/step").json()["vehicles"]]

    assert after == before


def test_only_an_ended_run_can_be_restarted(client):
    live_id = start(client)

    assert client.post(f"/simulations/{live_id}/restart").status_code == 409
    assert client.post("/simulations/00000000-0000-0000-0000-000000000000/restart").status_code == 404


def test_deleting_the_old_run_keeps_the_restarted_one(client):
    old_id = start(client)
    client.delete(f"/simulations/{old_id}")
    new_id = client.post(f"/simulations/{old_id}/restart").json()["simulation_id"]

    client.delete(f"/simulations/{old_id}", params={"purge": True})

    run = client.get(f"/simulations/{new_id}").json()
    assert run["status"] == "active" and run["restarted_from"] is None
