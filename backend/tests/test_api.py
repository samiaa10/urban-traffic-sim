"""Tests for the FastAPI endpoints (backend/api/main.py).

These need FastAPI and httpx (httpx is what FastAPI's TestClient uses):

    pip install fastapi httpx pytest

If either is missing the whole file is skipped, so the rest of the test
suite still runs without them.

The API normally loads the real Norwich map when it starts. Here it is
pointed at a tiny hand-made map instead (see make_test_graph), so these
tests need neither the Norwich data file nor internet access.
"""

import importlib
import math
import pickle
import sys

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from backend.routing.graph import Graph  # noqa: E402


# ------------------------------------------------------------
# A tiny road network for the API to run on
# ------------------------------------------------------------
#
#     12 - 13 - 14 - 15          A 4 x 4 grid of two-way roads
#      |    |    |    |          (node = row * 4 + column).
#      8 -  9 - 10 - 11
#      |    |    |    |          Node 100 hangs off node 15 by a ONE-WAY
#      4 -  5 -  6 -  7          road (you can drive in, not back out).
#      |    |    |    |
#      0 -  1 -  2 -  3          Node 99 is not connected to anything.
#

def metres_between(lat1, lon1, lat2, lon2):
    """Great-circle distance, good enough for edge lengths in a test map."""
    radius = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def make_test_graph():
    graph = Graph()

    for row in range(4):
        for column in range(4):
            graph.add_node(row * 4 + column, 52.620 + 0.002 * row, 1.280 + 0.003 * column)

    graph.add_node(99, 52.627, 1.2845)     # not connected to anything
    graph.add_node(100, 52.6265, 1.2935)   # one-way dead end

    def two_way(a, b):
        length = metres_between(
            graph.nodes[a]["latitude"], graph.nodes[a]["longitude"],
            graph.nodes[b]["latitude"], graph.nodes[b]["longitude"],
        )
        graph.add_edge(a, b, length)
        graph.add_edge(b, a, length)

    for row in range(4):
        for column in range(4):
            node = row * 4 + column

            if column < 3:
                two_way(node, node + 1)

            if row < 3:
                two_way(node, node + 4)

    graph.add_edge(
        15, 100,
        metres_between(
            graph.nodes[15]["latitude"], graph.nodes[15]["longitude"],
            graph.nodes[100]["latitude"], graph.nodes[100]["longitude"],
        ),
    )

    return graph


def point(api, node):
    data = api.graph.nodes[node]
    return data["latitude"], data["longitude"]


def vehicle_road(api, index=1, vehicle=0):
    """Two junction ids of a road on the route a simulated vehicle is really using.

    (/route and the vehicle can pick different equally short paths across a
    grid, so closing a road from /route might miss the vehicle.)
    """
    route = api.simulation.vehicles[vehicle].route
    first = api.node_by_point[(route[index]["latitude"], route[index]["longitude"])]
    second = api.node_by_point[(route[index + 1]["latitude"], route[index + 1]["longitude"])]
    return first, second


def point_text(api, node):
    latitude, longitude = point(api, node)
    return f"{latitude},{longitude}"


# ------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------

@pytest.fixture(scope="module")
def api(tmp_path_factory):
    """The API module, loaded once against the tiny map."""

    graph_file = tmp_path_factory.mktemp("graph") / "test_graph.pkl"

    with open(graph_file, "wb") as file:
        pickle.dump(make_test_graph(), file)

    patch = pytest.MonkeyPatch()
    patch.setenv("NORWICH_SIM_GRAPH", str(graph_file))

    sys.modules.pop("backend.api.main", None)
    module = importlib.import_module("backend.api.main")

    yield module

    sys.modules.pop("backend.api.main", None)
    patch.undo()


@pytest.fixture
def client(api):
    """A client, with no simulation running at the start of every test."""

    api.simulation = None

    yield TestClient(api.app)

    api.simulation = None


@pytest.fixture
def running(client):
    """A client with a simulation already started from corner 0 to corner 15."""

    response = client.post(
        "/simulation/start", params={"start": 0, "destination": 15}
    )
    assert response.status_code == 200

    return client


def step(client, times=1):
    response = None

    for _ in range(times):
        response = client.post("/simulation/step")
        assert response.status_code == 200

    return response.json()


def add_ambulance(client, api, speed=20):
    """Station at node 0 with one ambulance in it."""

    latitude, longitude = point(api, 0)

    response = client.post(
        "/station/create",
        params={"latitude": latitude, "longitude": longitude},
    )
    assert response.status_code == 200

    response = client.post(
        "/station/add-vehicle", params={"station_id": 0, "speed": speed}
    )
    assert response.status_code == 200

    return response.json()


def dispatch_ambulance(client, api, destination=15):
    latitude, longitude = point(api, destination)

    return client.post(
        "/emergency/create",
        params={"latitude": latitude, "longitude": longitude},
    )


# ------------------------------------------------------------
# Health and map lookups
# ------------------------------------------------------------

def test_root_reports_online(client):
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {"name": "NORWICH//SIM", "status": "online"}


def test_nearest_node_finds_the_closest_junction(client, api):
    latitude, longitude = point(api, 5)

    # a little off the junction, still closest to 5
    response = client.get(
        "/nearest-node",
        params={"latitude": latitude + 0.0002, "longitude": longitude - 0.0002},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["node"] == 5
    assert body["latitude"] == latitude
    assert body["longitude"] == longitude


def test_nearest_node_far_outside_the_map_still_answers(client):
    response = client.get(
        "/nearest-node", params={"latitude": 0, "longitude": 0}
    )

    assert response.status_code == 200
    assert response.json()["node"] in (0, 1, 2, 3, 99, 100)


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"latitude": 52.6},
        {"longitude": 1.3},
        {"latitude": "north", "longitude": 1.3},
    ],
)
def test_nearest_node_rejects_missing_or_bad_numbers(client, params):
    assert client.get("/nearest-node", params=params).status_code == 422


# ------------------------------------------------------------
# /route
# ------------------------------------------------------------

def test_route_between_two_junctions(client):
    response = client.get("/route", params={"start": 0, "destination": 15})

    assert response.status_code == 200
    body = response.json()

    assert body["algorithm"] == "A*"
    assert body["path"][0] == 0
    assert body["path"][-1] == 15
    assert len(body["path"]) == 7                 # 6 roads across the grid
    assert len(body["path_coordinates"]) == len(body["path"])
    assert body["distance_metres"] > 0
    assert body["nodes_explored"] > 0


def test_route_to_the_same_junction_is_a_single_point(client):
    response = client.get("/route", params={"start": 5, "destination": 5})

    assert response.status_code == 200
    assert response.json()["path"] == [5]
    assert response.json()["distance_metres"] == 0


def test_route_unknown_start_is_404(client):
    response = client.get("/route", params={"start": 12345, "destination": 0})

    assert response.status_code == 404
    assert "Start node" in response.json()["detail"]


def test_route_unknown_destination_is_404(client):
    response = client.get("/route", params={"start": 0, "destination": 12345})

    assert response.status_code == 404
    assert "Destination node" in response.json()["detail"]


def test_route_to_a_disconnected_junction_is_404(client):
    response = client.get("/route", params={"start": 0, "destination": 99})

    assert response.status_code == 404
    assert "No route" in response.json()["detail"]


def test_route_one_way_road_works_in_one_direction_only(client):
    into = client.get("/route", params={"start": 0, "destination": 100})
    out_of = client.get("/route", params={"start": 100, "destination": 0})

    assert into.status_code == 200
    assert out_of.status_code == 404


@pytest.mark.parametrize(
    "params",
    [{}, {"start": 0}, {"start": "a", "destination": 1}, {"start": 0, "destination": 1.5}],
)
def test_route_rejects_missing_or_non_integer_ids(client, params):
    assert client.get("/route", params=params).status_code == 422


def test_route_avoids_a_closed_road(running):
    before = running.get("/route", params={"start": 0, "destination": 15}).json()

    a, b = before["path"][1], before["path"][2]

    assert running.post(
        "/incident/block", params={"start": a, "destination": b}
    ).status_code == 200

    after = running.get("/route", params={"start": 0, "destination": 15}).json()

    pairs = list(zip(after["path"], after["path"][1:]))
    assert (a, b) not in pairs and (b, a) not in pairs


# ------------------------------------------------------------
# Every simulation endpoint refuses politely when nothing is running
# ------------------------------------------------------------

NEEDS_A_SIMULATION = [
    ("get", "/simulation/state", {}),
    ("post", "/simulation/step", {}),
    ("get", "/simulation/congestion", {}),
    ("post", "/simulation/reroute", {}),
    ("post", "/incident/block", {"start": 0, "destination": 1}),
    ("post", "/incident/unblock", {"start": 0, "destination": 1}),
    ("get", "/incidents", {}),
    ("post", "/station/create", {"latitude": 52.62, "longitude": 1.28}),
    ("post", "/station/add-vehicle", {}),
    ("post", "/emergency/create", {"latitude": 52.62, "longitude": 1.28}),
    ("get", "/emergency/status", {}),
    ("post", "/emergency/block-ahead", {}),
]


@pytest.mark.parametrize("method,path,params", NEEDS_A_SIMULATION)
def test_endpoints_need_a_running_simulation(client, method, path, params):
    response = getattr(client, method)(path, params=params)

    assert response.status_code == 404
    assert response.json()["detail"] == "No simulation is running"


# ------------------------------------------------------------
# Starting and stepping the simulation
# ------------------------------------------------------------

def test_start_creates_two_vehicles_when_there_is_a_way_back(client):
    response = client.post(
        "/simulation/start", params={"start": 0, "destination": 15}
    )

    assert response.status_code == 200
    body = response.json()

    assert body["status"] == "simulation started"
    assert body["distance_metres"] > 0
    assert set(body["vehicle_position"]) == {"latitude", "longitude"}

    assert len(step(client)["vehicles"]) == 2


def test_start_has_one_vehicle_when_the_way_back_is_one_way(client):
    response = client.post(
        "/simulation/start", params={"start": 0, "destination": 100}
    )

    assert response.status_code == 200
    assert len(step(client)["vehicles"]) == 1


def test_start_unknown_junction_is_404(client):
    for params in (
        {"start": 12345, "destination": 0},
        {"start": 0, "destination": 12345},
    ):
        assert client.post("/simulation/start", params=params).status_code == 404


def test_failed_start_does_not_create_a_simulation(client):
    response = client.post(
        "/simulation/start", params={"start": 0, "destination": 99}
    )

    assert response.status_code == 404
    assert client.get("/simulation/state").status_code == 404


def test_start_with_missing_parameters_is_422(client):
    assert client.post("/simulation/start").status_code == 422
    assert client.post("/simulation/start", params={"start": 0}).status_code == 422


def test_starting_again_replaces_the_simulation(running):
    step(running, 5)

    running.post("/simulation/start", params={"start": 3, "destination": 12})

    state = running.get("/simulation/state").json()

    assert state["distance_travelled"] == 0


def test_state_reports_vehicle_zero(running):
    state = running.get("/simulation/state").json()

    assert state["distance_travelled"] == 0
    assert state["finished"] is False

    step(running, 3)

    assert running.get("/simulation/state").json()["distance_travelled"] > 0


def test_step_moves_vehicles_and_reports_traffic_fields(running):
    body = step(running)

    for vehicle in body["vehicles"]:
        assert set(vehicle) >= {
            "vehicle_id", "latitude", "longitude", "distance_travelled",
            "finished", "vehicle_type", "on_emergency",
            "speed_factor", "journey_seconds",
        }
        assert vehicle["distance_travelled"] > 0
        assert vehicle["journey_seconds"] == 1
        assert vehicle["finished"] is False


def test_vehicles_eventually_finish(running):
    body = step(running, 200)

    assert all(vehicle["finished"] for vehicle in body["vehicles"])


def test_congestion_lists_roads_with_vehicles_on_them(running):
    running.post("/simulation/step")

    segments = running.get("/simulation/congestion").json()["congestion"]

    assert len(segments) >= 1

    for segment in segments:
        assert set(segment) == {"start", "end", "vehicle_count", "level"}
        assert segment["vehicle_count"] >= 1
        assert segment["level"] in ("light", "moderate", "heavy")


def test_congestion_levels_follow_vehicle_counts(running, api):
    route = api.simulation.vehicles[0].route

    for _ in range(3):
        api.simulation.add_vehicle(route)

    segments = running.get("/simulation/congestion").json()["congestion"]

    levels = {segment["vehicle_count"]: segment["level"] for segment in segments}

    assert levels[4] == "heavy"          # vehicle 0 and its three copies
    assert levels[1] == "light"          # the vehicle going the other way


def test_crowded_roads_slow_vehicles_down(running, api):
    route = api.simulation.vehicles[0].route

    for _ in range(3):
        api.simulation.add_vehicle(route)

    body = step(running)

    crowded = body["vehicles"][0]
    alone = body["vehicles"][1]          # driving the other way, on its own

    assert crowded["speed_factor"] < 1.0
    assert alone["speed_factor"] == 1.0
    assert crowded["distance_travelled"] < alone["distance_travelled"]


def test_step_after_everything_finished_is_harmless(running):
    step(running, 300)

    body = step(running, 3)

    assert all(vehicle["finished"] for vehicle in body["vehicles"])


# ------------------------------------------------------------
# Road closures and rerouting
# ------------------------------------------------------------

def test_block_a_road_then_list_and_reopen_it(running):
    route = running.get("/route", params={"start": 0, "destination": 15}).json()["path"]
    a, b = route[1], route[2]

    blocked = running.post("/incident/block", params={"start": a, "destination": b})

    assert blocked.status_code == 200
    assert blocked.json()["status"] == "incident created"
    assert "affected" in blocked.json()["rerouting"]

    incidents = running.get("/incidents").json()["incidents"]
    assert len(incidents) == 1

    reopened = running.post("/incident/unblock", params={"start": a, "destination": b})

    assert reopened.status_code == 200
    assert running.get("/incidents").json()["incidents"] == []


def test_closing_a_road_in_either_direction_is_the_same_road(running):
    running.post("/incident/block", params={"start": 5, "destination": 6})
    running.post("/incident/block", params={"start": 6, "destination": 5})

    assert len(running.get("/incidents").json()["incidents"]) == 1


def test_block_rejects_junctions_that_are_not_joined(running):
    response = running.post(
        "/incident/block", params={"start": 0, "destination": 15}
    )

    assert response.status_code == 400
    assert "not directly connected" in response.json()["detail"]


def test_block_unknown_junction_is_404(running):
    for params in (
        {"start": 12345, "destination": 0},
        {"start": 0, "destination": 12345},
    ):
        assert running.post("/incident/block", params=params).status_code == 404
        assert running.post("/incident/unblock", params=params).status_code == 404


def test_unblock_rejects_junctions_that_are_not_joined(running):
    response = running.post(
        "/incident/unblock", params={"start": 0, "destination": 15}
    )

    assert response.status_code == 400


def test_unblocking_a_road_that_was_never_closed_is_harmless(running):
    response = running.post(
        "/incident/unblock", params={"start": 0, "destination": 1}
    )

    assert response.status_code == 200
    assert running.get("/incidents").json()["incidents"] == []


def test_block_reroutes_a_vehicle_that_was_using_the_road(running, api):
    a, b = vehicle_road(api, index=1)

    body = running.post(
        "/incident/block", params={"start": a, "destination": b}
    ).json()

    assert body["rerouting"]["affected"] >= 1
    assert body["rerouting"]["rerouted"] >= 1


def test_reroute_a_vehicle_after_a_closure(running):
    route = running.get("/route", params={"start": 0, "destination": 15}).json()["path"]
    running.post("/incident/block", params={"start": route[1], "destination": route[2]})

    response = running.post("/simulation/reroute", params={"vehicle_id": 0})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "vehicle rerouted"
    assert body["distance_metres"] > 0
    assert len(body["route"]) >= 2


@pytest.mark.parametrize("vehicle_id", [-1, 2, 999])
def test_reroute_unknown_vehicle_is_404(running, vehicle_id):
    response = running.post("/simulation/reroute", params={"vehicle_id": vehicle_id})

    assert response.status_code == 404
    assert response.json()["detail"] == "Vehicle not found"


def test_reroute_finished_vehicle_is_400(running):
    step(running, 300)

    response = running.post("/simulation/reroute", params={"vehicle_id": 0})

    assert response.status_code == 400
    assert "already finished" in response.json()["detail"]


# ------------------------------------------------------------
# Stations, emergencies, ambulances
# ------------------------------------------------------------

def test_create_station_and_add_an_ambulance(running, api):
    latitude, longitude = point(api, 0)

    created = running.post(
        "/station/create", params={"latitude": latitude, "longitude": longitude}
    )

    assert created.status_code == 200
    assert created.json()["status"] == "station created"

    added = running.post(
        "/station/add-vehicle", params={"station_id": 0, "speed": 25}
    )

    assert added.status_code == 200
    assert added.json()["vehicle_type"] == "ambulance"
    assert added.json()["vehicle_id"] >= 1
    assert api.simulation.vehicles[added.json()["vehicle_id"]].speed == 25


def test_add_vehicle_to_unknown_station_is_404(running):
    for station_id in (0, 7, -1):
        response = running.post(
            "/station/add-vehicle", params={"station_id": station_id}
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "Station not found"


# Regression: a vehicle with speed 0 was accepted. It never moved, and
# /emergency/status then crashed with a ZeroDivisionError.
@pytest.mark.parametrize("speed", [0, -5, "nan", "inf"])
def test_ambulance_that_cannot_move_is_rejected(running, api, speed):
    latitude, longitude = point(api, 0)
    running.post(
        "/station/create", params={"latitude": latitude, "longitude": longitude}
    )

    response = running.post(
        "/station/add-vehicle", params={"station_id": 0, "speed": speed}
    )

    # 400 from our own check (or 422 if the framework refuses the number first)
    assert response.status_code in (400, 422)
    assert len(api.simulation.vehicles) == 2           # no ambulance was added


def test_status_still_works_after_a_rejected_zero_speed_ambulance(running, api):
    latitude, longitude = point(api, 0)
    running.post(
        "/station/create", params={"latitude": latitude, "longitude": longitude}
    )
    running.post("/station/add-vehicle", params={"station_id": 0, "speed": 0})

    assert running.get("/emergency/status").status_code == 200


def test_emergency_without_any_ambulance_is_404_and_leaves_no_trace(running, api):
    latitude, longitude = point(api, 15)

    response = running.post(
        "/emergency/create", params={"latitude": latitude, "longitude": longitude}
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "No available emergency vehicle"
    assert running.get("/emergency/status").json()["emergencies"] == []


def test_dispatch_an_ambulance_and_measure_the_response(running, api):
    add_ambulance(running, api)

    response = dispatch_ambulance(running, api, destination=15)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "emergency dispatched"
    assert body["route_distance_metres"] > 0
    assert len(body["route"]) >= 2

    status = running.get("/emergency/status").json()
    emergency = status["emergencies"][0]

    assert emergency["state"] == "en_route"
    assert emergency["eta_seconds"] > 0
    assert emergency["response_time_seconds"] is None

    for _ in range(400):
        step(running)
        emergency = running.get("/emergency/status").json()["emergencies"][0]

        if emergency["state"] == "arrived":
            break

    assert emergency["state"] == "arrived"
    assert emergency["response_time_seconds"] > 0
    assert emergency["eta_seconds"] == 0


def test_ambulance_can_answer_a_second_emergency_after_the_first(running, api):
    add_ambulance(running, api)

    dispatch_ambulance(running, api, destination=3)
    step(running, 200)

    second = dispatch_ambulance(running, api, destination=12)

    assert second.status_code == 200


def test_only_one_ambulance_means_a_second_simultaneous_call_is_refused(running, api):
    add_ambulance(running, api)

    first = dispatch_ambulance(running, api, destination=15)
    second = dispatch_ambulance(running, api, destination=12)

    assert first.status_code == 200
    assert second.status_code == 404
    assert len(running.get("/emergency/status").json()["emergencies"]) == 1


def test_emergency_to_a_closed_off_location_is_404_and_frees_the_ambulance(running, api):
    add_ambulance(running, api)

    # an emergency at the unconnected junction 99: no route there
    latitude, longitude = point(api, 99)
    response = running.post(
        "/emergency/create", params={"latitude": latitude, "longitude": longitude}
    )

    assert response.status_code == 404
    assert "No route" in response.json()["detail"]

    # the ambulance went back to the station, so it can still be sent
    assert dispatch_ambulance(running, api, destination=15).status_code == 200


def test_emergency_create_needs_coordinates(running):
    assert running.post("/emergency/create").status_code == 422


def test_block_ahead_closes_a_road_and_reroutes_the_ambulance(running, api):
    add_ambulance(running, api)
    dispatch_ambulance(running, api, destination=15)

    response = running.post("/emergency/block-ahead")

    assert response.status_code == 200
    body = response.json()

    assert body["status"] == "road closed, ambulance rerouted"
    assert set(body["blocked_segment"]) == {"start", "end"}
    assert body["distance_after_metres"] > 0
    assert len(body["route"]) >= 2

    assert len(running.get("/incidents").json()["incidents"]) == 1


def test_block_ahead_without_an_ambulance_on_its_way_is_404(running):
    response = running.post("/emergency/block-ahead")

    assert response.status_code == 404
    assert "No ambulance" in response.json()["detail"]


def test_emergency_status_is_empty_before_any_emergency(running):
    body = running.get("/emergency/status").json()

    assert body["emergencies"] == []
    assert body["simulation_time"] == 0


def test_emergency_status_reports_simulated_time(running):
    step(running, 7)

    assert running.get("/emergency/status").json()["simulation_time"] == 7


# ------------------------------------------------------------
# Station optimiser: one station
# ------------------------------------------------------------

def test_optimiser_ranks_candidates_best_first(client):
    response = client.get("/optimiser/stations", params={"scenarios": 100})

    assert response.status_code == 200
    body = response.json()

    ranks = [result["rank"] for result in body["results"]]
    assert ranks == list(range(1, len(ranks) + 1))

    averages = [result["average_seconds"] for result in body["results"]]
    assert averages == sorted(averages)

    assert body["scenarios"] == 100
    assert body["closed_roads"] == 0
    assert len(body["explanation"]) >= 1

    for field in (
        "node", "latitude", "longitude", "average_seconds", "worst_seconds",
        "within_target_percent", "unreachable", "rank",
    ):
        assert field in body["results"][0]


def test_optimiser_with_custom_candidates(client, api):
    candidates = f"{point_text(api, 0)};{point_text(api, 15)};{point_text(api, 5)}"

    response = client.get(
        "/optimiser/stations",
        params={"candidates": candidates, "scenarios": 50},
    )

    assert response.status_code == 200
    assert {result["node"] for result in response.json()["results"]} == {0, 15, 5}


def test_optimiser_is_repeatable_with_the_same_seed(client):
    params = {"scenarios": 80, "seed": 9}

    first = client.get("/optimiser/stations", params=params).json()
    second = client.get("/optimiser/stations", params=params).json()

    assert first == second


def test_optimiser_target_time_changes_coverage(client):
    tight = client.get(
        "/optimiser/stations", params={"scenarios": 100, "target_seconds": 1}
    ).json()
    generous = client.get(
        "/optimiser/stations", params={"scenarios": 100, "target_seconds": 100000}
    ).json()

    assert tight["results"][0]["within_target_percent"] < 100
    assert generous["results"][0]["within_target_percent"] == 100


def test_optimiser_counts_closed_roads(running, api):
    running.post("/incident/block", params={"start": 5, "destination": 6})

    body = running.get("/optimiser/stations", params={"scenarios": 50}).json()

    assert body["closed_roads"] == 1


@pytest.mark.parametrize(
    "candidates",
    [
        "not-a-point",
        "52.62",                              # only one number
        "52.62,1.28,5;52.62,1.29",            # three numbers in a pair
        "52.62,abc;52.62,1.29",
        "52.62,1.28",                         # only one candidate
        "52.62,nan;52.62,1.29",
        "52.62,inf;52.62,1.29",
        ";",
        "",
    ],
)
def test_optimiser_rejects_bad_candidates(client, candidates):
    response = client.get("/optimiser/stations", params={"candidates": candidates})

    assert response.status_code == 400


def test_optimiser_rejects_too_many_candidates(client):
    candidates = ";".join(f"52.62,{1.28 + 0.001 * i}" for i in range(21))

    response = client.get("/optimiser/stations", params={"candidates": candidates})

    assert response.status_code == 400
    assert "between 2 and 20" in response.json()["detail"]


@pytest.mark.parametrize("scenarios", [0, -1, 5001])
def test_optimiser_rejects_scenario_counts_out_of_range(client, scenarios):
    response = client.get("/optimiser/stations", params={"scenarios": scenarios})

    assert response.status_code == 400


def test_optimiser_accepts_the_largest_allowed_scenario_count(client, api):
    candidates = f"{point_text(api, 0)};{point_text(api, 15)}"

    response = client.get(
        "/optimiser/stations",
        params={"candidates": candidates, "scenarios": 5000},
    )

    assert response.status_code == 200


# Regression: NaN / infinite / non-positive settings used to be accepted
# (0 or less for the target) or slip through the speed check (NaN).
@pytest.mark.parametrize(
    "settings",
    [
        {"speed": 0},
        {"speed": -3},
        {"speed": "nan"},
        {"speed": "inf"},
        {"target_seconds": 0},
        {"target_seconds": -60},
        {"target_seconds": "nan"},
    ],
)
def test_optimiser_rejects_nonsense_settings(client, settings):
    response = client.get("/optimiser/stations", params=settings)

    # 400 from our own checks (or 422 if the framework refuses the number first)
    assert response.status_code in (400, 422)


@pytest.mark.parametrize("params", [{"scenarios": "many"}, {"seed": 1.5}, {"speed": "fast"}])
def test_optimiser_rejects_non_numbers(client, params):
    assert client.get("/optimiser/stations", params=params).status_code == 422


# ------------------------------------------------------------
# Station optimiser: several stations
# ------------------------------------------------------------

def test_station_set_default_request(client):
    response = client.get("/optimiser/station-set", params={"scenarios": 100})

    assert response.status_code == 200
    body = response.json()

    assert body["count"] == 3
    assert len(body["stations"]) == 3
    assert [pick["order"] for pick in body["stations"]] == [1, 2, 3]
    assert len({pick["node"] for pick in body["stations"]}) == 3
    assert body["scenarios"] == 100
    assert body["target_seconds"] == 480
    assert len(body["explanation"]) >= 2

    for field in (
        "average_seconds", "worst_seconds", "within_target_percent",
        "unreachable", "ranking_seconds",
    ):
        assert field in body["result"]


def test_station_set_includes_all_three_baselines(client):
    body = client.get(
        "/optimiser/station-set", params={"stations": 2, "scenarios": 100}
    ).json()

    assert [b["name"] for b in body["baselines"]] == [
        "best_single", "top_individual", "random",
    ]
    assert [b["stations"] for b in body["baselines"]] == [1, 2, 2]


def test_two_stations_are_at_least_as_fast_as_the_best_single_station(client):
    body = client.get(
        "/optimiser/station-set", params={"stations": 2, "scenarios": 200}
    ).json()

    single = body["baselines"][0]

    assert body["result"]["average_seconds"] <= single["average_seconds"]


def test_station_set_one_station_matches_the_single_optimiser(client):
    single = client.get("/optimiser/stations", params={"scenarios": 100}).json()
    group = client.get(
        "/optimiser/station-set", params={"stations": 1, "scenarios": 100}
    ).json()

    assert group["stations"][0]["node"] == single["results"][0]["node"]
    assert group["result"]["average_seconds"] == pytest.approx(
        single["results"][0]["average_seconds"]
    )


def test_station_set_is_repeatable(client):
    params = {"stations": 3, "scenarios": 100, "seed": 4}

    assert (
        client.get("/optimiser/station-set", params=params).json()
        == client.get("/optimiser/station-set", params=params).json()
    )


def test_station_set_with_custom_candidates(client, api):
    candidates = ";".join(point_text(api, node) for node in (0, 3, 12, 15, 5))

    body = client.get(
        "/optimiser/station-set",
        params={"stations": 2, "candidates": candidates, "scenarios": 100},
    ).json()

    assert {pick["node"] for pick in body["stations"]} <= {0, 3, 12, 15, 5}
    assert body["candidates_considered"] == 5


def test_station_set_reports_closed_roads(running):
    running.post("/incident/block", params={"start": 5, "destination": 6})

    body = running.get(
        "/optimiser/station-set", params={"stations": 2, "scenarios": 50}
    ).json()

    assert body["closed_roads"] == 1


@pytest.mark.parametrize("stations", [0, -1, 11, 100])
def test_station_set_rejects_station_counts_out_of_range(client, stations):
    response = client.get("/optimiser/station-set", params={"stations": stations})

    assert response.status_code == 400
    assert "between 1 and 10" in response.json()["detail"]


def test_station_set_more_stations_than_candidates_is_400(client, api):
    candidates = f"{point_text(api, 0)};{point_text(api, 15)}"

    response = client.get(
        "/optimiser/station-set",
        params={"stations": 5, "candidates": candidates},
    )

    assert response.status_code == 400
    assert "distinct" in response.json()["detail"]


def test_station_set_candidates_snapping_to_one_junction_count_once(client, api):
    near = f"{point_text(api, 5)};{point_text(api, 5)}"

    response = client.get(
        "/optimiser/station-set",
        params={"stations": 2, "candidates": near},
    )

    assert response.status_code == 400


@pytest.mark.parametrize(
    "params",
    [
        {"scenarios": 0},
        {"scenarios": 5001},
        {"speed": 0},
        {"target_seconds": 0},
        {"target_seconds": -1},
        {"candidates": "garbage"},
        {"candidates": "52.62,1.28"},
    ],
)
def test_station_set_rejects_bad_settings(client, params):
    assert client.get("/optimiser/station-set", params=params).status_code == 400


@pytest.mark.parametrize("params", [{"stations": "three"}, {"stations": 2.5}, {"seed": "x"}])
def test_station_set_rejects_non_numbers(client, params):
    assert client.get("/optimiser/station-set", params=params).status_code == 422


# ------------------------------------------------------------
# Cross-origin requests (the React app runs on a different port)
# ------------------------------------------------------------

def test_frontend_origin_is_allowed(client):
    response = client.get("/", headers={"Origin": "http://localhost:5173"})

    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_other_origins_are_not_allowed(client):
    response = client.get("/", headers={"Origin": "http://evil.example"})

    assert "access-control-allow-origin" not in response.headers


def test_unknown_path_is_404(client):
    assert client.get("/no-such-endpoint").status_code == 404
