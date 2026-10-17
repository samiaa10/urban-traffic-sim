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
