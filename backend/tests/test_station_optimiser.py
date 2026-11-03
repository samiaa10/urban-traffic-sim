from backend.routing.a_star import a_star, get_segment
from backend.routing.graph import Graph
from backend.optimiser.station_optimiser import (
    dense_candidates,
    explain_best,
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


def test_station_cut_off_from_most_of_the_map_does_not_rank_first():
    """Regression test: a station that can only reach one junction used to
    win, because its average was taken over just the emergencies it could
    reach (a 0 second "response" to its own junction)."""
    graph = make_line_graph()
    closed = {get_segment(graph, 1, 2)}   # junction 1 is now an island

    results = optimise_station_locations(
        graph,
        [point_of(graph, 1), point_of(graph, 3)],
        scenarios=100,
        blocked_segments=closed,
    )

    best, cut_off = results

    assert best["node"] == 3
    assert cut_off["node"] == 1
    # Neither station can reach the island itself, but the cut-off one
    # misses almost the whole map
    assert cut_off["unreachable"] > best["unreachable"]
    assert best["ranking_seconds"] < cut_off["ranking_seconds"]


def test_candidates_on_the_same_junction_are_only_ranked_once():
    graph = make_line_graph()
    point = point_of(graph, 3)

    results = optimise_station_locations(
        graph, [point, point, point_of(graph, 1)], scenarios=20
    )

    assert sorted(result["node"] for result in results) == [1, 3]


def test_dense_candidates_are_unique_and_well_connected():
    graph = make_line_graph()

    points = dense_candidates(graph, rows=3, columns=5)

    assert len(points) == len(set(points))
    assert len(points) >= 2

    for latitude, longitude in points:
        node = snap_to_road(graph, latitude, longitude)
        assert len(shortest_distances(graph, node)) == len(graph.nodes)


def test_dense_candidates_skip_poorly_connected_junctions():
    # Two separate pieces of road: no junction can reach 90% of the map
    graph = Graph()

    for number in range(1, 7):
        graph.add_node(number, 52.63, 1.29 + 0.0075 * number)

    for first, second in [(1, 2), (2, 3), (4, 5), (5, 6)]:
        graph.add_edge(first, second, 500)
        graph.add_edge(second, first, 500)

    assert dense_candidates(graph, rows=2, columns=3) == []


def test_explanation_says_why_the_winner_won():
    graph = make_line_graph()
    results = optimise_station_locations(
        graph,
        [point_of(graph, 1), point_of(graph, 3), point_of(graph, 5)],
        scenarios=100,
    )

    sentences = explain_best(results, scenarios=100, target_seconds=480)
    text = " ".join(sentences)

    assert "lowest average" in text
    assert "faster than #2" in text
    assert "within the 8.0 min target" in text
    assert "could not reach" not in text      # nothing was cut off


def test_explanation_mentions_emergencies_the_winner_could_not_reach():
    graph = make_line_graph()
    results = optimise_station_locations(
        graph,
        [point_of(graph, 1), point_of(graph, 3)],
        scenarios=100,
        blocked_segments={get_segment(graph, 1, 2)},
    )

    text = " ".join(explain_best(results, scenarios=100, target_seconds=480))

    assert "could not reach" in text
