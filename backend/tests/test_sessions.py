"""Simulation runs with their own IDs (backend/api/sessions.py and the
/simulations/... endpoints).

The registry tests need nothing but the standard library. The API tests
build a fresh app with create_app on the tiny test map from test_api.py,
so each test starts with no runs, like a newly started server.
"""

import pickle
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.api.sessions import LiveRun, RunRegistry


# ------------------------------------------------------------
# The registry on its own
# ------------------------------------------------------------

class FakeVehicle:
    finished = False


class FakeSimulation:
    """Just enough of a Simulation for LiveRun.summary()."""

    def __init__(self):
        self.vehicles = [FakeVehicle()]
        self.time = 0
        self.accidents = {}

    def get_blocked_segments(self):
        return set()

    def get_emergencies(self):
        return []


def make_run():
    return LiveRun(FakeSimulation(), traffic=None, start=0, destination=1)


def test_each_run_gets_a_different_id():
    assert len({make_run().id for _ in range(50)}) == 50


def test_a_run_can_be_found_by_its_id():
    registry = RunRegistry()
    run = make_run()

    registry.add(run)

    assert registry.get(run.id) is run
    assert registry.get("no-such-id") is None


def test_there_is_no_default_run_until_one_is_made_default():
    registry = RunRegistry()
    registry.add(make_run())

    assert registry.default() is None


def test_a_new_default_run_stops_the_previous_default():
    registry = RunRegistry()
    first, second = make_run(), make_run()

    registry.add(first, make_default=True)
    stopped = registry.add(second, make_default=True)

    assert stopped == [(first, "replaced")]
    assert registry.default() is second
    assert registry.get(first.id) is None


def test_adding_a_run_by_id_leaves_the_default_alone():
    registry = RunRegistry()
    default, other = make_run(), make_run()

    registry.add(default, make_default=True)
    stopped = registry.add(other)

    assert stopped == []
    assert registry.default() is default
    assert registry.get(other.id) is other


def test_the_run_idle_longest_is_stopped_when_the_limit_is_reached():
    registry = RunRegistry(max_live=2)
    oldest, newer, newest = make_run(), make_run(), make_run()

    registry.add(oldest)
    registry.add(newer)

    oldest.last_used, newer.last_used = 100.0, 50.0   # newer is idle longest

    stopped = registry.add(newest)

    assert stopped == [(newer, "stopped")]
    assert len(registry) == 2
    assert registry.get(oldest.id) is oldest


def test_stopping_the_default_run_to_make_room_clears_the_default():
    registry = RunRegistry(max_live=1)
    first, second = make_run(), make_run()

    registry.add(first, make_default=True)
    registry.add(second)

    assert registry.default() is None


def test_removing_a_run():
    registry = RunRegistry()
    run = make_run()
    registry.add(run, make_default=True)

    assert registry.remove(run.id) is run
    assert registry.remove(run.id) is None
    assert registry.default() is None


def test_runs_are_listed_newest_first():
    registry = RunRegistry()
    runs = [make_run() for _ in range(3)]

    for run in runs:
        registry.add(run)

    assert registry.runs() == sorted(
        runs, key=lambda r: r.created_at, reverse=True
    )


def test_the_limit_must_allow_at_least_one_run():
    with pytest.raises(ValueError):
        RunRegistry(max_live=0)


# ------------------------------------------------------------
# Through the API
# ------------------------------------------------------------

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from backend.api.app import create_app  # noqa: E402
from backend.tests.test_api import make_test_graph  # noqa: E402


@pytest.fixture(scope="module")
def graph_file(tmp_path_factory):
    path = tmp_path_factory.mktemp("graph") / "test_graph.pkl"

    with open(path, "wb") as file:
        pickle.dump(make_test_graph(), file)

    return path


@pytest.fixture
def app(graph_file):
    return create_app(graph_file)


@pytest.fixture
def client(app):
    return TestClient(app)


def start_default(client, start=0, destination=15):
    response = client.post(
        "/simulation/start", params={"start": start, "destination": destination}
    )
    assert response.status_code == 200
    return response.json()["simulation_id"]


def create_run(client, start=0, destination=15):
    response = client.post(
        "/simulations", params={"start": start, "destination": destination}
    )
    assert response.status_code == 201
    return response.json()["simulation_id"]


def run_url(simulation_id, path):
    return f"/simulations/{simulation_id}{path}"


def test_start_still_answers_as_before_and_adds_an_id(client):
    response = client.post(
        "/simulation/start", params={"start": 0, "destination": 15}
    )
    body = response.json()

    assert body["status"] == "simulation started"
    assert {"distance_metres", "nodes_explored", "vehicle_position"} <= set(body)
    assert isinstance(body["simulation_id"], str)


def test_started_runs_are_listed(client):
    default_id = start_default(client)
    other_id = create_run(client)

    listed = client.get("/simulations").json()["simulations"]

    assert {run["simulation_id"] for run in listed} == {default_id, other_id}
    assert [run["is_default"] for run in listed if run["simulation_id"] == default_id] == [True]
    assert all(run["status"] == "active" for run in listed)


def test_one_run_can_be_looked_up(client):
    simulation_id = create_run(client, 0, 15)

    body = client.get(f"/simulations/{simulation_id}").json()

    assert body["simulation_id"] == simulation_id
    assert body["start"] == 0
    assert body["destination"] == 15
    assert body["simulation_time"] == 0
    assert body["is_default"] is False


def test_unknown_runs_are_404(client):
    assert client.get("/simulations/nope").status_code == 404
    assert client.post(run_url("nope", "/step")).status_code == 404
    assert client.delete("/simulations/nope").status_code == 404

    response = client.get(run_url("nope", "/incidents"))
    assert response.json()["detail"] == "Simulation not found"


def test_a_run_made_by_id_does_not_become_the_default(client):
    create_run(client)

    response = client.post("/simulation/step")

    assert response.status_code == 404
    assert response.json()["detail"] == "No simulation is running"


def test_a_new_start_replaces_the_previous_default_run(client):
    first = start_default(client)
    second = start_default(client)

    assert client.get(f"/simulations/{first}").status_code == 404
    assert client.get(f"/simulations/{second}").json()["is_default"] is True


def test_the_original_endpoints_act_on_the_default_run(client):
    default_id = start_default(client)
    other_id = create_run(client)

    for _ in range(3):
        assert client.post("/simulation/step").status_code == 200

    assert client.get(f"/simulations/{default_id}").json()["simulation_time"] == 3
    assert client.get(f"/simulations/{other_id}").json()["simulation_time"] == 0


def test_runs_do_not_share_closed_roads_or_time(client):
    a = create_run(client)
    b = create_run(client)

    closed = client.post(
        run_url(a, "/incidents/block"), params={"start": 0, "destination": 1}
    )
    assert closed.status_code == 200

    for _ in range(4):
        client.post(run_url(a, "/step"))

    assert len(client.get(run_url(a, "/incidents")).json()["incidents"]) == 1
    assert client.get(run_url(b, "/incidents")).json()["incidents"] == []

    assert client.post(run_url(a, "/step")).json()["simulation_time"] == 5
    assert client.post(run_url(b, "/step")).json()["simulation_time"] == 1


def test_runs_do_not_share_accidents_fleets_or_settings(client):
    a = create_run(client)
    b = create_run(client)

    client.post(
        run_url(a, "/incidents/accident"),
        params={"start": 5, "destination": 6, "severity": "severe"},
    )
    fleet = client.post(run_url(a, "/fleet"), params={"count": 5, "seed": 3}).json()
    client.post(run_url(a, "/traffic/rerouting"), params={"enabled": False})

    status_a = client.get(run_url(a, "/traffic/status")).json()
    status_b = client.get(run_url(b, "/traffic/status")).json()

    assert len(client.get(run_url(a, "/incidents")).json()["accidents"]) == 1
    assert client.get(run_url(b, "/incidents")).json()["accidents"] == []
    assert status_a != status_b
    assert fleet["spawned"] >= 1
    assert client.get(f"/simulations/{a}").json()["vehicles"] == 2 + fleet["spawned"]
    assert client.get(f"/simulations/{b}").json()["vehicles"] == 2


def test_emergencies_work_per_run(client, app):
    a = create_run(client)
    b = create_run(client)
    graph = app.state.context.graph
    station = graph.nodes[0]
    scene = graph.nodes[15]

    client.post(
        run_url(a, "/stations"),
        params={"latitude": station["latitude"], "longitude": station["longitude"]},
    )
    added = client.post(run_url(a, "/stations/add-vehicle"), params={"station_id": 0})
    assert added.status_code == 200

    dispatched = client.post(
        run_url(a, "/emergencies"),
        params={"latitude": scene["latitude"], "longitude": scene["longitude"]},
    )
    assert dispatched.status_code == 200

    # Run b has no station, so it has nothing to send
    refused = client.post(
        run_url(b, "/emergencies"),
        params={"latitude": scene["latitude"], "longitude": scene["longitude"]},
    )
    assert refused.status_code == 404

    for _ in range(300):
        status = client.get(run_url(a, "/emergencies/status")).json()
        if status["emergencies"][0]["state"] == "arrived":
            break
        client.post(run_url(a, "/step"))

    assert status["emergencies"][0]["state"] == "arrived"
    assert client.get(run_url(b, "/emergencies/status")).json()["emergencies"] == []


def without_timings(value):
    """A response with its wall-clock "seconds" fields removed."""

    if isinstance(value, dict):
        return {k: without_timings(v) for k, v in value.items() if k != "seconds"}

    if isinstance(value, list):
        return [without_timings(v) for v in value]

    return value


def test_per_run_endpoints_answer_the_same_as_the_originals(client):
    """A run driven by ID and the default run, given the same requests,
    give the same answers (apart from where the run is named)."""

    default_id = start_default(client)
    other_id = create_run(client)

    pairs = [
        ("post", "/incident/block", "/incidents/block", {"start": 0, "destination": 1}),
        ("post", "/simulation/step", "/step", {}),
        ("get", "/simulation/state", "/state", {}),
        ("get", "/simulation/congestion", "/congestion", {}),
        ("get", "/incidents", "/incidents", {}),
        ("post", "/incident/unblock", "/incidents/unblock", {"start": 0, "destination": 1}),
        ("post", "/simulation/step", "/step", {}),
    ]

    for method, legacy, scoped, params in pairs:
        original = getattr(client, method)(legacy, params=params)
        by_id = getattr(client, method)(run_url(other_id, scoped), params=params)

        assert original.status_code == by_id.status_code == 200, legacy
        assert without_timings(original.json()) == without_timings(by_id.json()), legacy

    assert default_id != other_id


def test_stopping_a_run(client):
    default_id = start_default(client)

    response = client.delete(f"/simulations/{default_id}")

    assert response.json() == {
        "status": "simulation stopped", "simulation_id": default_id
    }
    assert client.get(f"/simulations/{default_id}").status_code == 404
    assert client.post("/simulation/step").status_code == 404


def test_the_live_run_limit_stops_the_idlest_run(graph_file):
    client = TestClient(create_app(graph_file, max_live=2))

    first = create_run(client)
    second = create_run(client)
    client.post(run_url(first, "/step"))      # first is now the busier one
    third = create_run(client)

    live = {run["simulation_id"] for run in client.get("/simulations").json()["simulations"]}

    assert live == {first, third}
    assert second not in live
    assert client.get("/simulations").json()["max_live"] == 2


def test_the_optimiser_avoids_the_named_runs_closed_roads(client, app):
    a = create_run(client)
    b = create_run(client)
    client.post(run_url(a, "/incidents/block"), params={"start": 0, "destination": 1})

    graph = app.state.context.graph
    candidates = ";".join(
        f"{graph.nodes[n]['latitude']},{graph.nodes[n]['longitude']}" for n in (0, 15)
    )
    params = {"candidates": candidates, "scenarios": 20}

    with_a = client.get("/optimiser/stations", params={**params, "simulation_id": a})
    with_b = client.get("/optimiser/stations", params={**params, "simulation_id": b})
    unknown = client.get("/optimiser/stations", params={**params, "simulation_id": "x"})

    assert with_a.json()["closed_roads"] == 1
    assert with_b.json()["closed_roads"] == 0
    assert unknown.status_code == 404


def test_routes_avoid_the_named_runs_closed_roads(client):
    a = create_run(client)
    b = create_run(client)
    client.post(run_url(a, "/incidents/block"), params={"start": 0, "destination": 1})

    params = {"start": 0, "destination": 1}

    around = client.get("/route", params={**params, "simulation_id": a}).json()
    direct = client.get("/route", params={**params, "simulation_id": b}).json()

    assert direct["path"] == [0, 1]
    assert around["path"] != [0, 1]
    assert client.get("/route", params={**params, "simulation_id": "x"}).status_code == 404


def test_requests_for_one_run_from_many_threads_all_count(client):
    """Every step is applied exactly once, however the requests overlap."""

    simulation_id = create_run(client)
    barrier = threading.Barrier(4)

    def drive():
        barrier.wait()
        for _ in range(10):
            assert client.post(run_url(simulation_id, "/step")).status_code == 200

    with ThreadPoolExecutor(max_workers=4) as pool:
        for future in [pool.submit(drive) for _ in range(4)]:
            future.result()

    assert client.get(f"/simulations/{simulation_id}").json()["simulation_time"] == 40


def test_separate_apps_have_separate_runs(graph_file):
    """What a server restart looks like without a database: nothing survives.
    (Stage 3 adds the database so the history does.)"""

    first = TestClient(create_app(graph_file))
    simulation_id = create_run(first)

    second = TestClient(create_app(graph_file))

    assert second.get(f"/simulations/{simulation_id}").status_code == 404
    assert second.get("/simulations").json()["simulations"] == []


def test_without_a_database_history_is_unavailable_but_runs_work(client):
    simulation_id = create_run(client)

    response = client.get(f"/simulations/{simulation_id}/history/incidents")

    assert response.status_code == 503
    assert "DATABASE_URL" in response.json()["detail"]
    assert client.get("/simulations").json()["persistence"] is False
    assert client.get(f"/simulations/{simulation_id}").json()["recording"] == "off"


def test_without_a_database_optimiser_answers_are_not_saved(client):
    answer = client.get("/optimiser/stations", params={"scenarios": 10}).json()

    assert answer["optimiser_run_id"] is None
    assert answer["reused"] is False
    assert client.get("/optimiser/runs").status_code == 503


def test_restarting_needs_a_database(client):
    assert client.post(
        "/simulations/00000000-0000-0000-0000-000000000000/restart"
    ).status_code == 503
