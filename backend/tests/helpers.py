"""Small helpers that build tiny road networks for the tests."""

from backend.routing.graph import Graph


def make_diamond_graph():
    """
    A tiny town with two ways from A to D:

        A --- B --- D      (short way, via B)
         \\         /
          \\- C ---/        (longer way, via C)

    Roads are two-way, like real streets.
    Coordinates are real-looking Norwich points so the A* heuristic works.
    """
    graph = Graph()

    graph.add_node("A", 52.6300, 1.2900)
    graph.add_node("B", 52.6300, 1.2950)
    graph.add_node("C", 52.6340, 1.2950)
    graph.add_node("D", 52.6300, 1.3000)

    def two_way(a, b, metres):
        graph.add_edge(a, b, metres)
        graph.add_edge(b, a, metres)

    two_way("A", "B", 340)
    two_way("B", "D", 340)
    two_way("A", "C", 600)
    two_way("C", "D", 600)

    return graph


def coords(graph, path):
    """Turn a list of node ids into the {latitude, longitude} points vehicles use."""
    return [
        {
            "latitude": graph.nodes[node]["latitude"],
            "longitude": graph.nodes[node]["longitude"],
        }
        for node in path
    ]


def make_grid_graph(size=8, seed=7, spacing_lat=0.002, spacing_lon=0.003):
    """A size x size grid of two-way roads near Norwich.

    Road lengths are the straight-line distance times a random factor
    between 1.0 and 1.3 (real roads are never shorter than the straight
    line), fixed by `seed`. Node ids are row * size + column.
    """

    import math
    import random

    generator = random.Random(seed)
    graph = Graph()

    for row in range(size):
        for column in range(size):
            graph.add_node(
                row * size + column,
                52.62 + spacing_lat * row,
                1.28 + spacing_lon * column,
            )

    def straight(a, b):
        p1 = math.radians(graph.nodes[a]["latitude"])
        p2 = math.radians(graph.nodes[b]["latitude"])
        dl = math.radians(graph.nodes[b]["longitude"] - graph.nodes[a]["longitude"])
        h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        return 2 * 6_371_000 * math.atan2(math.sqrt(h), math.sqrt(1 - h))

    def two_way(a, b):
        length = straight(a, b) * generator.uniform(1.0, 1.3)
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
