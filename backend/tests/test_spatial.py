"""The grid spatial index (routing/spatial.py) gives exactly the answers of
the brute-force scans it replaced, ties and far-away points included, and
the faster distance searches give exactly the old routes."""

import pickle
import random
from pathlib import Path

import pytest

from backend.routing.graph import Graph
from backend.routing.spatial import GridIndex, index_for
from backend.simulation.dispatch import nearest_junction, nearest_junction_scan
from backend.tests.helpers import make_grid_graph


MAP_FILE = Path(__file__).resolve().parents[2] / "data" / "osm" / "norwich_graph.pkl"


def scan_k(graph, latitude, longitude, k):
    """The optimiser's original snapping order."""

    def gap(node):
        data = graph.nodes[node]
        return (data["latitude"] - latitude) ** 2 + (data["longitude"] - longitude) ** 2

    return sorted(graph.nodes, key=gap)[:k]


@pytest.fixture(scope="module")
def norwich():
    with open(MAP_FILE, "rb") as file:
        return pickle.load(file)


def test_the_index_finds_the_same_nearest_junction_as_the_scan(norwich):
    generator = random.Random(3)
    lats = [d["latitude"] for d in norwich.nodes.values()]
    lons = [d["longitude"] for d in norwich.nodes.values()]

    for _ in range(3000):
        # Inside the map, and well outside it on every side
        latitude = generator.uniform(min(lats) - 0.05, max(lats) + 0.05)
        longitude = generator.uniform(min(lons) - 0.05, max(lons) + 0.05)
        assert nearest_junction(norwich, latitude, longitude) == nearest_junction_scan(
            norwich, latitude, longitude
        )


def test_the_index_lists_the_k_nearest_in_the_same_order_as_the_scan(norwich):
    generator = random.Random(4)
    index = index_for(norwich)

    for _ in range(300):
        latitude = generator.uniform(52.58, 52.68)
        longitude = generator.uniform(1.20, 1.38)
        for k in (1, 7, 50):
            assert index.nearest(latitude, longitude, k, squared=True) == scan_k(
                norwich, latitude, longitude, k
            )


def test_ties_go_to_the_first_junction_in_the_graph():
    graph = Graph()
    # Four junctions the same distance from (0, 0), added in this order
    for node, (lat, lon) in enumerate([(1, 0), (0, 1), (-1, 0), (0, -1)], start=10):
        graph.add_node(node, lat, lon)
    graph.add_node(99, 5, 5)

    assert nearest_junction(graph, 0, 0) == nearest_junction_scan(graph, 0, 0) == 10
    assert GridIndex(graph).nearest(0, 0, 3, squared=True) == scan_k(graph, 0, 0, 3)


def test_on_points_exactly_on_junctions_and_on_a_grid_map():
    graph = make_grid_graph(size=9, seed=2)
    for node, data in graph.nodes.items():
        assert nearest_junction(graph, data["latitude"], data["longitude"]) == node


def test_an_empty_or_one_junction_map():
    assert GridIndex(Graph()).nearest(52.6, 1.3) == []
    graph = Graph()
    graph.add_node(1, 52.6, 1.3)
    assert nearest_junction(graph, 0, 0) == 1


def test_the_index_is_rebuilt_when_junctions_are_added():
    graph = Graph()
    graph.add_node(1, 52.6, 1.3)
    assert nearest_junction(graph, 52.7, 1.4) == 1
    graph.add_node(2, 52.7, 1.4)
    assert nearest_junction(graph, 52.7, 1.4) == 2


def test_faster_distance_searches_give_the_same_routes(norwich):
    """a_star and dijkstra fill their tables lazily now; checked against a
    reference Dijkstra written the old way."""

    import heapq

    from backend.routing.a_star import a_star
    from backend.routing.dijkstra import dijkstra

    def reference(start, destination):
        best = {node: float("inf") for node in norwich.nodes}
        best[start] = 0
        queue = [(0, start)]
        while queue:
            metres, node = heapq.heappop(queue)
            if node == destination:
                return metres
            if metres > best[node]:
                continue
            for neighbour, length in norwich.get_neighbours(node):
                if metres + length < best[neighbour]:
                    best[neighbour] = metres + length
                    heapq.heappush(queue, (metres + length, neighbour))
        return float("inf")

    generator = random.Random(5)
    nodes = sorted(norwich.nodes)
    for _ in range(60):
        start, destination = generator.sample(nodes, 2)
        expected = reference(start, destination)
        assert dijkstra(norwich, start, destination)[1] == pytest.approx(expected)
        assert a_star(norwich, start, destination)[1] == pytest.approx(expected)


def test_reach_counts_match_full_searches(norwich):
    """The optimiser's snapping counts reachable junctions with a cached
    breadth-first search; it must equal the old full Dijkstra count."""

    from backend.optimiser.station_optimiser import reach_count, shortest_distances

    generator = random.Random(6)
    for node in generator.sample(sorted(norwich.nodes), 40):
        assert reach_count(norwich, node) == len(shortest_distances(norwich, node))


def test_a_map_that_is_a_line_stays_exact_and_quick():
    """A straight-line map once made the grid thousands of cells long, and
    queries off the line searched millions of empty cells."""

    import time

    graph = Graph()
    for node in range(9):
        graph.add_node(node, 52.62, 1.28 + 0.003 * node)

    generator = random.Random(8)
    started = time.perf_counter()
    for _ in range(500):
        latitude = generator.uniform(52.5, 52.75)
        longitude = generator.uniform(1.2, 1.4)
        assert nearest_junction(graph, latitude, longitude) == nearest_junction_scan(
            graph, latitude, longitude
        )
        assert GridIndex(graph).nearest(latitude, longitude, 4, squared=True) == scan_k(
            graph, latitude, longitude, 4
        )
    assert time.perf_counter() - started < 5
