from backend.routing.a_star import a_star, get_segment
from backend.routing.graph import Graph
from backend.optimiser.station_optimiser import (
    optimise_station_locations,
    shortest_distances,
    snap_to_road,
)
from backend.tests.helpers import make_diamond_graph


def make_line_graph():
    """Five junctions in a row, 500 m apart:  1 - 2 - 3 - 4 - 5"""
    graph = Graph()

    for number in range(1, 6):
        graph.add_node(number, 52.63, 1.29 + 0.0075 * number)

    for number in range(1, 5):
        graph.add_edge(number, number + 1, 500)
        graph.add_edge(number + 1, number, 500)

    return graph


def point_of(graph, node):
    return (graph.nodes[node]["latitude"], graph.nodes[node]["longitude"])


def test_one_search_gives_same_distances_as_a_star():
    graph = make_diamond_graph()

    distances = shortest_distances(graph, "A")

    for destination in ["B", "C", "D"]:
        _, a_star_distance = a_star(graph, "A", destination)
        assert distances[destination] == a_star_distance


def test_closed_road_makes_distances_longer():
    graph = make_diamond_graph()
    closed = {get_segment(graph, "A", "B")}

    distances = shortest_distances(graph, "A", blocked_segments=closed)

    assert distances["D"] == 1200     # forced round via C


def test_middle_station_beats_end_station():
    graph = make_line_graph()
    candidates = [point_of(graph, 1), point_of(graph, 3)]

    results = optimise_station_locations(graph, candidates, scenarios=100)

    best, other = results
    assert best["node"] == 3
    assert best["rank"] == 1
    assert best["worst_seconds"] < other["worst_seconds"]
    assert best["average_seconds"] < other["average_seconds"]


def test_percent_within_target():
    graph = make_line_graph()
    candidates = [point_of(graph, 3)]

    # 500 m at 20 m/s = 25 s per hop, so the furthest junction is 50 s away
    generous = optimise_station_locations(
        graph, candidates, scenarios=100, target_seconds=60
    )
    strict = optimise_station_locations(
        graph, candidates, scenarios=100, target_seconds=30
    )

    assert generous[0]["within_target_percent"] == 100
    assert strict[0]["within_target_percent"] < 100


def test_same_seed_gives_same_results():
    graph = make_line_graph()
    candidates = [point_of(graph, 1), point_of(graph, 3)]

    first = optimise_station_locations(graph, candidates, scenarios=50, seed=7)
    second = optimise_station_locations(graph, candidates, scenarios=50, seed=7)

    assert first == second


def test_cut_off_emergencies_are_counted_as_unreached():
    graph = make_line_graph()
    closed = {get_segment(graph, 3, 4)}   # cuts the road in half

    results = optimise_station_locations(
        graph,
        [point_of(graph, 1), point_of(graph, 5)],   # one station each side
        scenarios=100,
        blocked_segments=closed,
    )

    for result in results:
        # Each station can't reach the other half of the road
        assert result["unreachable"] > 0
        assert result["within_target_percent"] < 100


def test_closure_slows_the_response():
    graph = make_diamond_graph()
    candidates = [point_of(graph, "A")]

    open_roads = optimise_station_locations(graph, candidates, scenarios=200)
    closed = optimise_station_locations(
        graph,
        candidates,
        scenarios=200,
        blocked_segments={get_segment(graph, "A", "B")},
    )

    assert closed[0]["worst_seconds"] > open_roads[0]["worst_seconds"]


def test_snap_to_road_picks_the_nearest_junction():
    graph = make_line_graph()

    latitude, longitude = point_of(graph, 4)

    assert snap_to_road(graph, latitude + 0.0001, longitude) == 4
