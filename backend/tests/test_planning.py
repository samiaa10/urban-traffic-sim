"""Emergency infrastructure planning (optimiser/planning.py): hexagons,
road-network response times, coverage figures, the optimiser's trace, and
consistency with the optimiser's own answers."""

import math
import pickle

import pytest

from backend.optimiser import station_optimiser as optimiser
from backend.optimiser.planning import (
    HexGrid,
    build_cells,
    cell_values,
    junction_seconds,
    plan,
    summarise,
)
from backend.routing.travel_time import RoadNetwork
from backend.simulation.vehicle import straight_line_metres
from backend.tests.helpers import make_grid_graph


# ------------------------------------------------------------
# Hexagons
# ------------------------------------------------------------

def test_every_junction_is_in_exactly_one_hexagon_near_it():
    graph = make_grid_graph(8)
    nodes = list(graph.nodes)
    grid, cells = build_cells(graph, nodes, size=150)
    placed = [n for cell in cells for n in cell["nodes"]]
    assert sorted(placed) == sorted(nodes)
    for cell in cells:
        centre = grid.centre(*cell["key"])
        for node in cell["nodes"]:
            data = graph.nodes[node]
            # Inside a hexagon: no further from its centre than a corner
            assert straight_line_metres(centre, (data["latitude"], data["longitude"])) <= 150 * 1.001


def test_a_hexagon_is_a_closed_ring_round_its_centre():
    grid = HexGrid(52.63, 1.29, 200)
    ring = grid.polygon(2, -1)
    assert len(ring) == 7 and ring[0] == ring[-1]
    lat, lon = grid.centre(2, -1)
    for corner_lon, corner_lat in ring[:-1]:
        assert straight_line_metres((lat, lon), (corner_lat, corner_lon)) == pytest.approx(200, rel=0.01)
    # A point at a hexagon's centre falls in that hexagon
    assert grid.cell(lat, lon) == (2, -1)


# ------------------------------------------------------------
# Response times and coverage
# ------------------------------------------------------------

def test_response_time_is_the_nearest_stations_road_distance_over_speed():
    graph = make_grid_graph(6)
    nodes = list(graph.nodes)
    maps = [optimiser.shortest_distances(graph, 0), optimiser.shortest_distances(graph, 35)]
    times = junction_seconds(maps, nodes, speed=20)
    for node, seconds in zip(nodes, times):
        assert seconds == pytest.approx(min(maps[0][node], maps[1][node]) / 20)
    # Road distance, never shorter than the straight line
    for node, seconds in zip(nodes, times):
        a = graph.nodes[node]
        nearest = min(
            straight_line_metres((a["latitude"], a["longitude"]),
                                 (graph.nodes[s]["latitude"], graph.nodes[s]["longitude"]))
            for s in (0, 35)
        )
        assert seconds * 20 >= nearest - 1e-6


def test_unreachable_junctions_count_as_beyond_the_target():
    times = [60, 200, math.inf, 500]
    weights = [10, 20, 30, 40]
    summary = summarise(times, weights, target=240)
    assert summary["unreachable_junctions"] == 1
    assert summary["junctions_within_target_percent"] == 50
    assert summary["population_within_target_percent"] == 30
    assert summary["population_beyond_target"] == 70
    assert summary["worst_seconds"] == 500


def test_a_hexagon_shows_the_median_of_its_junctions():
    cells = [{"key": (0, 0), "nodes": ["a", "b", "c"]}, {"key": (1, 0), "nodes": ["d"]}]
    index = {"a": 0, "b": 1, "c": 2, "d": 3}
    values = cell_values(cells, index, [100, 300, 200, math.inf], [1, 2, 3, 4], target=250)
    assert values["seconds"] == [200, None]
    assert values["within"] == [round(2 / 3, 3), 0]
    assert values["beyond_people"] == [2, 4]


# ------------------------------------------------------------
# The plan
# ------------------------------------------------------------

def grid_plan(**kwargs):
    graph = make_grid_graph(8)
    corner = graph.nodes[0]
    existing = [{"service": "fire", "name": "Corner", "osm": "node/1",
                 "latitude": corner["latitude"], "longitude": corner["longitude"]}]
    return graph, plan(graph, RoadNetwork(graph), "fire", existing_stations=existing, **kwargs)


def test_the_plan_compares_the_existing_layout_with_each_step_of_the_optimised_one():
    _, result = grid_plan(count=3, target_seconds=120)
    labels = [stage["label"] for stage in result["stages"]]
    assert labels == ["existing", "optimised:1", "optimised:2", "optimised:3"]
    # Every stage has a value for every hexagon
    assert all(len(stage["cells"]["seconds"]) == len(result["cells"]) for stage in result["stages"])
    # Adding stations never makes coverage worse
    means = [stage["summary"]["mean_seconds"] for stage in result["stages"][1:]]
    assert means == sorted(means, reverse=True)
    # The optimised three beat one station in a corner
    assert result["stages"][-1]["summary"]["mean_seconds"] < result["stages"][0]["summary"]["mean_seconds"]
    assert [s["order"] for s in result["optimised"]] == [1, 2, 3]
    assert result["search"]["possible_sets"] == math.comb(result["search"]["candidates"], 3)


def test_far_away_existing_stations_are_left_out():
    graph = make_grid_graph(6)
    far = [{"service": "fire", "name": "Elsewhere", "osm": "node/2", "latitude": 51.5, "longitude": -0.1}]
    result = plan(graph, RoadNetwork(graph), "fire", 2, existing_stations=far)
    assert result["existing"] == []
    assert result["existing_left_out"][0]["name"] == "Elsewhere"
    assert [s["label"] for s in result["stages"]] == ["optimised:1", "optimised:2"]


def test_the_plan_is_repeatable_and_checks_its_settings():
    _, a = grid_plan(count=2, seed=3)
    _, b = grid_plan(count=2, seed=3)
    assert a == b
    graph = make_grid_graph(5)
    network = RoadNetwork(graph)
    for bad in ({"count": 0}, {"count": 9}, {"disruption": "volcano"}, {"service": "lifeboat"}):
        with pytest.raises(ValueError):
            plan(graph, network, **{"service": "fire", "count": 2, **bad})


@pytest.fixture(scope="module")
def norwich():
    from backend.optimiser.planning import _SEARCHES

    with open(optimiser.__file__.replace("backend/optimiser/station_optimiser.py",
                                         "data/osm/norwich_graph.pkl"), "rb") as file:
        graph = pickle.load(file)
    yield graph, RoadNetwork(graph)
    _SEARCHES.clear()


def test_the_optimised_layout_is_the_optimisers_own_answer(norwich):
    graph, network = norwich
    result = plan(graph, network, "fire", 3, target_seconds=240)
    answer = optimiser.optimise_station_set(
        graph, optimiser.dense_candidates(graph), 3, scenarios=200, seed=1,
        target_seconds=240, speed=17.0, distribution="population",
    )
    chosen = {(s["latitude"], s["longitude"]) for s in answer["stations"]}
    assert {(s["latitude"], s["longitude"]) for s in result["optimised"]} == chosen
    assert result["optimised"][-1]["average_seconds"] == pytest.approx(answer["result"]["average_seconds"])


def test_a_flood_cuts_places_off_without_removing_them_from_the_map(norwich):
    graph, network = norwich
    normal = plan(graph, network, "fire", 3, target_seconds=240)
    flooded = plan(graph, network, "fire", 3, target_seconds=240, disruption="flood")
    assert len(flooded["cells"]) == len(normal["cells"])
    assert flooded["closed_roads"] > 0
    assert (flooded["stages"][-1]["summary"]["unreachable_junctions"]
            > normal["stages"][-1]["summary"]["unreachable_junctions"])
    # The real stations: three fire stations from OpenStreetMap
    assert len(normal["existing"]) == 3
