"""Tests for the fastest-route search (backend/routing/travel_time.py)."""

import heapq
import math
import random

import pytest

from backend.routing.dijkstra import dijkstra
from backend.routing.graph import Graph
from backend.routing.travel_time import RoadNetwork
from backend.tests.helpers import make_diamond_graph


def grid_graph(size=6, seed=3):
    """A size x size grid of two-way roads with slightly random lengths."""

    generator = random.Random(seed)
    graph = Graph()

    for row in range(size):
        for column in range(size):
            graph.add_node(row * size + column, 52.62 + 0.002 * row, 1.28 + 0.003 * column)

    def two_way(a, b):
        straight = RoadNetwork(graph).straight_line_metres(a, b)
        # Real roads are never shorter than the straight line
        length = straight * generator.uniform(1.0, 1.4)
        graph.add_edge(a, b, length)
        graph.add_edge(b, a, length)

    for row in range(size):
        for column in range(size):
            node = row * size + column
            if column < size - 1:
                two_way(node, node + 1)
            if row < size - 1:
                two_way(node, node + size)

    return graph


def reference_fastest_seconds(network, start, destination, speed, factor, blocked=()):
    """Plain Dijkstra on travel time: slow but obviously correct."""

    best = {start: 0.0}
    queue = [(0.0, start)]

    while queue:
        seconds, node = heapq.heappop(queue)

        if node == destination:
            return seconds

        if seconds > best[node]:
            continue

        for neighbour, _ in network.neighbours(node):
            segment = network.segment[(node, neighbour)]
            if segment in blocked:
                continue
            cost = seconds + network.length[(node, neighbour)] / (speed * factor(segment))
            if cost < best.get(neighbour, math.inf):
                best[neighbour] = cost
                heapq.heappush(queue, (cost, neighbour))

    return math.inf


# ------------------------------------------------------------
# Free-flowing roads
# ------------------------------------------------------------

def test_on_empty_roads_the_fastest_route_is_the_shortest_route():
    graph = make_diamond_graph()
    network = RoadNetwork(graph)

    path, seconds, _ = network.fastest_path("A", "D", speed=10)
    _, metres = dijkstra(graph, "A", "D")

    assert path == ["A", "B", "D"]
    assert seconds == pytest.approx(metres / 10)


def test_free_flow_matches_dijkstra_on_every_pair_of_a_grid():
    graph = grid_graph()
    network = RoadNetwork(graph)

    for start in (0, 7, 20):
        for destination in graph.nodes:
            _, seconds, _ = network.fastest_path(start, destination, speed=13.9)
            _, metres = dijkstra(graph, start, destination)
            assert seconds == pytest.approx(metres / 13.9)


def test_same_start_and_destination_is_a_zero_second_trip():
    network = RoadNetwork(make_diamond_graph())

    assert network.fastest_path("A", "A", speed=10) == (["A"], 0.0, 1)


def test_speed_must_be_positive():
    network = RoadNetwork(make_diamond_graph())

    with pytest.raises(ValueError):
        network.fastest_path("A", "D", speed=0)


# ------------------------------------------------------------
# Slow roads
# ------------------------------------------------------------

def test_avoids_a_slow_road_when_the_other_way_is_quicker():
    graph = make_diamond_graph()
    network = RoadNetwork(graph)
    slow = network.segment[("A", "B")]

    # A-B at 20% speed: 340 m takes as long as 1,700 m, so A-C-D (1,200 m) wins
    path, seconds, _ = network.fastest_path(
        "A", "D", speed=10, speed_factor=lambda s: 0.2 if s == slow else 1.0
    )

    assert path == ["A", "C", "D"]
    assert seconds == pytest.approx(1200 / 10)


def test_keeps_a_slow_road_when_the_detour_would_take_even_longer():
    graph = make_diamond_graph()
    network = RoadNetwork(graph)
    slow = network.segment[("A", "B")]

    # A-B at 60% speed: 340 / 0.6 + 340 = 907 m-equivalent, still under 1,200
    path, seconds, _ = network.fastest_path(
        "A", "D", speed=10, speed_factor=lambda s: 0.6 if s == slow else 1.0
    )

    assert path == ["A", "B", "D"]
    assert seconds == pytest.approx((340 / 0.6 + 340) / 10)


def test_matches_a_plain_dijkstra_with_random_slowdowns():
    """A* must stay exact (its estimate never overestimates)."""

    graph = grid_graph(size=7, seed=11)
    network = RoadNetwork(graph)
    generator = random.Random(5)

    factors = {
        segment: generator.choice([1.0, 1.0, 0.6, 0.3, 0.15])
        for segment in set(network.segment.values())
    }

    def factor(segment):
        return factors[segment]

    nodes = sorted(graph.nodes)

    for _ in range(60):
        start, destination = generator.sample(nodes, 2)
        path, seconds, _ = network.fastest_path(start, destination, 13.9, factor)

        assert seconds == pytest.approx(
            reference_fastest_seconds(network, start, destination, 13.9, factor)
        )
        # The reported time is the time of the path it returns
        assert network.path_seconds(path, 13.9, factor) == pytest.approx(seconds)


def test_explores_fewer_junctions_than_the_whole_map_on_a_short_trip():
    graph = grid_graph(size=10)
    network = RoadNetwork(graph)

    _, _, explored = network.fastest_path(0, 11, speed=13.9)

    assert explored < len(graph.nodes) / 2


# ------------------------------------------------------------
# Closed roads and unreachable places
# ------------------------------------------------------------

def test_closed_roads_are_never_used():
    graph = make_diamond_graph()
    network = RoadNetwork(graph)
    closed = {network.segment[("B", "D")]}

    path, seconds, _ = network.fastest_path("A", "D", 10, blocked_segments=closed)

    assert path == ["A", "C", "D"]
    assert seconds == pytest.approx(120)


def test_no_route_gives_an_empty_path_and_infinite_time():
    graph = make_diamond_graph()
    graph.add_node("E", 52.64, 1.31)
    network = RoadNetwork(graph)

    path, seconds, _ = network.fastest_path("A", "E", 10)

    assert path == []
    assert seconds == math.inf


def test_a_road_with_zero_speed_is_treated_as_impassable():
    graph = make_diamond_graph()
    network = RoadNetwork(graph)
    stopped = network.segment[("A", "B")]

    path, _, _ = network.fastest_path(
        "A", "D", 10, speed_factor=lambda s: 0.0 if s == stopped else 1.0
    )

    assert path == ["A", "C", "D"]


# ------------------------------------------------------------
# Lookups
# ------------------------------------------------------------

def test_segment_keys_match_the_simulation_format():
    graph = make_diamond_graph()
    network = RoadNetwork(graph)

    a = (graph.nodes["A"]["latitude"], graph.nodes["A"]["longitude"])
    b = (graph.nodes["B"]["latitude"], graph.nodes["B"]["longitude"])

    # Sorted pair of coordinates, the same for both directions
    assert network.segment[("A", "B")] == tuple(sorted((a, b)))
    assert network.segment[("B", "A")] == network.segment[("A", "B")]


def test_parallel_roads_use_the_shorter_length():
    graph = make_diamond_graph()
    graph.add_edge("A", "B", 200)

    assert RoadNetwork(graph).length[("A", "B")] == 200


def test_main_component_leaves_out_one_way_pockets_and_islands():
    graph = make_diamond_graph()
    graph.add_node("island", 52.65, 1.33)
    graph.add_node("dead_end", 52.631, 1.301)
    graph.add_edge("D", "dead_end", 50)   # in, but no way back out

    component = RoadNetwork(graph).main_component()

    assert component == {"A", "B", "C", "D"}


# ------------------------------------------------------------
# Landmarks (ALT)
# ------------------------------------------------------------

def test_landmark_bound_never_exceeds_the_true_distance():
    graph = grid_graph(size=8, seed=4)
    network = RoadNetwork(graph)
    network.use_landmarks(4)

    for start in graph.nodes:
        for destination in (0, 9, 27, 63):
            _, metres = dijkstra(graph, start, destination)
            assert network.lower_bound_metres(start, destination) <= metres + 1e-6


def test_landmark_bound_is_at_least_the_straight_line():
    graph = grid_graph(size=8, seed=4)
    network = RoadNetwork(graph)
    network.use_landmarks(4)

    for start, destination in ((0, 63), (5, 40), (17, 22)):
        assert network.lower_bound_metres(start, destination) >= (
            network.straight_line_metres(start, destination) - 1e-9
        )


def test_landmarks_are_spread_out_and_distinct():
    network = RoadNetwork(grid_graph(size=8))
    network.use_landmarks(4)

    assert len(set(network.landmarks)) == 4
    # A grid's farthest-apart junctions are its corners
    assert set(network.landmarks) >= {0, 63} or set(network.landmarks) >= {7, 56}


def test_landmark_heuristic_stays_exact_and_explores_less():
    graph = grid_graph(size=12, seed=2)
    plain = RoadNetwork(graph)
    alt = RoadNetwork(graph)
    alt.use_landmarks(6)

    generator = random.Random(3)
    factors = {s: generator.choice([1.0, 0.6, 0.3]) for s in set(plain.segment.values())}

    def factor(segment):
        return factors[segment]

    explored_plain = explored_alt = 0
    nodes = sorted(graph.nodes)

    for _ in range(40):
        start, destination = generator.sample(nodes, 2)
        _, seconds_plain, n_plain = plain.fastest_path(start, destination, 13.9, factor)
        _, seconds_alt, n_alt = alt.fastest_path(
            start, destination, 13.9, factor, heuristic="landmarks"
        )

        assert seconds_alt == pytest.approx(seconds_plain)
        assert seconds_alt == pytest.approx(
            reference_fastest_seconds(alt, start, destination, 13.9, factor)
        )
        explored_plain += n_plain
        explored_alt += n_alt

    assert explored_alt < explored_plain


def test_landmarks_on_a_map_with_one_way_pockets_stay_valid():
    graph = make_diamond_graph()
    graph.add_node("dead_end", 52.631, 1.301)
    graph.add_edge("D", "dead_end", 50)
    network = RoadNetwork(graph)
    network.use_landmarks(3)

    path, seconds, _ = network.fastest_path("A", "dead_end", 10)

    assert path == ["A", "B", "D", "dead_end"]
    assert seconds == pytest.approx(730 / 10)


def test_unknown_heuristic_is_refused():
    with pytest.raises(ValueError):
        RoadNetwork(make_diamond_graph()).fastest_path("A", "D", 10, heuristic="guess")
