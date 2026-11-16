"""Sweeps (backend/scenarios/sweep.py) and the scenario endpoints that work
without a database (presets, metrics, nearest road; studies answer 503)."""

import pickle

import pytest

from backend.scenarios import sweep
from backend.scenarios.runner import World
from backend.scenarios.spec import ScenarioSpec
from backend.simulation.demand import Zone
from backend.tests.helpers import make_grid_graph


BASE = ScenarioSpec.model_validate({"name": "Base", "seed": 3, "demand": {"vehicles": 50}})


@pytest.fixture(scope="module")
def world():
    return World(make_grid_graph(size=8, seed=7))


# ------------------------------------------------------------
# Sweeps
# ------------------------------------------------------------

def test_seed_sweeps_change_only_the_seed():
    specs = sweep.seeds(BASE, 3, first_seed=10)

    assert [s.seed for s in specs] == [10, 11, 12]
    assert [s.name for s in specs] == ["Base (seed 10)", "Base (seed 11)", "Base (seed 12)"]
    assert {s.demand.vehicles for s in specs} == {50}


def test_each_closure_keeps_the_baseline_first_and_the_same_trips():
    specs = sweep.each_closure(BASE, [(1, 2), (9, 10)])

    assert [s.name for s in specs] == ["Base: baseline", "Close 1-2", "Close 9-10"]
    assert specs[0].closures == []
    assert [(c.start, c.destination) for c in specs[2].closures] == [(9, 10)]
    assert len({s.trip_signature() for s in specs}) == 1


def test_candidate_roads_are_real_roads_and_can_be_limited_to_a_zone(world):
    roads = sweep.candidate_roads(world.network)
    centre = world.network.position[27]
    near = sweep.candidate_roads(world.network, Zone(centre[0], centre[1], 300))

    assert roads == sorted(roads)
    assert all(a < b for a, b in roads)
    assert all(world.graph.has_edge(a, b) or world.graph.has_edge(b, a) for a, b in roads)
    assert 0 < len(near) < len(roads)
    assert set(near) <= set(roads)


def test_random_closures_are_reproducible(world):
    roads = sweep.candidate_roads(world.network)

    first = sweep.random_closures(BASE, roads, 5, 3, sweep_seed=7)
    again = sweep.random_closures(BASE, roads, 5, 3, sweep_seed=7)
    other = sweep.random_closures(BASE, roads, 5, 3, sweep_seed=8)

    assert [s.model_dump() for s in first] == [s.model_dump() for s in again]
    assert [s.closures for s in first] != [s.closures for s in other]
    assert len(first) == 6 and first[0].closures == []
    assert all(len(s.closures) == 3 for s in first[1:])
    with pytest.raises(ValueError):
        sweep.random_closures(BASE, roads[:2], 1, 3)


def test_station_layouts_name_each_layout():
    specs = sweep.station_layouts(BASE, [
        ("North", [{"latitude": 52.64, "longitude": 1.29}]),
        ("Two", [{"latitude": 52.64, "longitude": 1.29}, {"latitude": 52.61, "longitude": 1.3}]),
    ])

    assert [s.name for s in specs] == ["North", "Two"]
    assert [len(s.stations) for s in specs] == [1, 2]


# ------------------------------------------------------------
# Endpoints without a database
# ------------------------------------------------------------

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from backend.api.app import create_app  # noqa: E402


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    path = tmp_path_factory.mktemp("grid") / "grid.pkl"
    with open(path, "wb") as file:
        pickle.dump(make_grid_graph(size=8, seed=7), file)
    return TestClient(create_app(path))


def test_metrics_are_described(client):
    body = client.get("/scenarios/metrics").json()

    assert body["default"] == "total_delay_vehicle_hours"
    keys = {m["key"] for m in body["metrics"]}
    assert {"total_delay_vehicle_hours", "mean_increase_seconds", "emergency_mean_response_seconds"} <= keys


def test_presets_are_valid_scenarios(client):
    presets = client.get("/scenarios/presets").json()["presets"]

    assert {p["key"] for p in presets} == {"baseline", "closure", "accident", "surge", "stations"}
    for preset in presets:
        ScenarioSpec.model_validate(preset["spec"])


def test_the_nearest_road_to_a_point_on_a_road(client):
    graph = client.app.state.context.graph
    a, b = graph.nodes[27], graph.nodes[28]
    middle = {"latitude": (a["latitude"] + b["latitude"]) / 2,
              "longitude": (a["longitude"] + b["longitude"]) / 2}

    road = client.get("/scenarios/nearest-road", params=middle).json()

    assert (road["start"], road["destination"]) == (27, 28)
    assert road["start_point"]["latitude"] == a["latitude"]
    assert road["distance_metres"] < 1


def test_studies_need_a_database(client):
    response = client.post("/scenarios/studies", json={"scenarios": [{"name": "A"}]})

    assert response.status_code == 503
    assert "DATABASE_URL" in response.json()["detail"]
    assert client.get("/scenarios/studies").status_code == 503


def test_the_frontend_ranks_only_by_metrics_the_backend_knows():
    """frontend/src/scenarios.ts lists the metrics the study can be ranked
    by, so the page needs no request on load; each must exist here."""

    import re
    from pathlib import Path

    from backend.scenarios.compare import METRICS

    source = (Path(__file__).resolve().parents[2] / "frontend/src/scenarios.ts").read_text()
    block = source[source.index("export const METRIC_OPTIONS"):]
    block = block[: block.index("]\n")]
    keys = re.findall(r'key: "([a-z0-9_]+)"', block)

    assert keys, "no metrics found in METRIC_OPTIONS"
    assert set(keys) <= set(METRICS)
    for key in keys:
        assert f'better: "{METRICS[key]["better"]}"' in block.split(f'key: "{key}"')[1].split("}")[0]
