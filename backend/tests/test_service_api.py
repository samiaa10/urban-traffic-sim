"""The emergency services through the API: validation of services, service
vehicles and speeds, and the queue in an osm run (no database needed)."""

import pickle

import pytest

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


def client_for(graph_file, model):
    client = TestClient(create_app(graph_file, traffic_model=model))
    response = client.post("/simulation/start", params={"start": 0, "destination": 15})
    assert response.status_code == 200
    return client


def corner(client, node):
    graph = client.app.state.context.graph
    return graph.nodes[node]["latitude"], graph.nodes[node]["longitude"]


def test_unknown_services_are_refused(graph_file):
    client = client_for(graph_file, "simple")
    lat, lon = corner(client, 0)

    response = client.post("/station/create", params={"latitude": lat, "longitude": lon, "station_type": "coastguard"})
    assert response.status_code == 400
    assert "ambulance, fire, police" in response.json()["detail"]

    response = client.post("/emergency/create", params={"latitude": lat, "longitude": lon, "emergency_type": "taxi"})
    assert response.status_code == 400


def test_a_station_holds_only_its_own_service_at_that_services_speed(graph_file):
    client = client_for(graph_file, "simple")
    lat, lon = corner(client, 0)
    client.post("/station/create", params={"latitude": lat, "longitude": lon, "station_type": "fire"})

    added = client.post("/station/add-vehicle", params={"station_id": 0})
    assert added.status_code == 200
    assert added.json()["vehicle_type"] == "fire"
    vehicle = client.app.state.context.registry.default().simulation.vehicles[added.json()["vehicle_id"]]
    assert vehicle.speed == 17

    wrong = client.post("/station/add-vehicle", params={"station_id": 0, "vehicle_type": "police"})
    assert wrong.status_code == 400

    # The original default stays: an ambulance station's vehicle at 20 m/s
    client.post("/station/create", params={"latitude": lat, "longitude": lon})
    ambulance = client.post("/station/add-vehicle", params={"station_id": 1}).json()
    assert ambulance["vehicle_type"] == "ambulance"


def test_in_an_osm_run_an_emergency_with_nothing_free_is_queued(graph_file):
    client = client_for(graph_file, "osm")
    lat, lon = corner(client, 0)
    client.post("/station/create", params={"latitude": lat, "longitude": lon})
    client.post("/station/add-vehicle", params={"station_id": 0})
    far = corner(client, 15)

    first = client.post("/emergency/create", params={"latitude": far[0], "longitude": far[1]})
    second = client.post("/emergency/create", params={"latitude": far[0], "longitude": far[1]})

    assert first.status_code == 200
    assert second.status_code == 202
    assert second.json()["status"] == "emergency queued"
    assert second.json()["queue_position"] == 1

    states = []
    for _ in range(600):
        client.post("/simulation/step")
        statuses = client.get("/emergency/status").json()["emergencies"]
        states = [s["state"] for s in statuses]
        if states == ["arrived", "arrived"]:
            break
    assert states == ["arrived", "arrived"]
    assert statuses[1]["waited_seconds"] > 0


def test_in_a_simple_run_nothing_free_is_still_refused(graph_file):
    client = client_for(graph_file, "simple")
    lat, lon = corner(client, 0)
    client.post("/station/create", params={"latitude": lat, "longitude": lon})
    client.post("/station/add-vehicle", params={"station_id": 0})

    assert client.post("/emergency/create", params={"latitude": lat, "longitude": lon}).status_code == 200
    assert client.post("/emergency/create", params={"latitude": lat, "longitude": lon}).status_code == 404


def test_in_an_osm_run_an_unreachable_emergency_is_404_and_the_vehicle_stays_free(graph_file):
    """The pre-Stage 5 behaviour (test_api.py) holds with the osm model too."""

    client = client_for(graph_file, "osm")
    lat, lon = corner(client, 0)
    client.post("/station/create", params={"latitude": lat, "longitude": lon})
    client.post("/station/add-vehicle", params={"station_id": 0})
    island = corner(client, 99)

    response = client.post("/emergency/create", params={"latitude": island[0], "longitude": island[1]})

    assert response.status_code == 404
    assert "No route" in response.json()["detail"]
    far = corner(client, 15)
    assert client.post("/emergency/create", params={"latitude": far[0], "longitude": far[1]}).status_code == 200
