from backend.routing.a_star import a_star, get_segment
from backend.routing.dijkstra import dijkstra
from backend.tests.helpers import make_diamond_graph


def test_dijkstra_and_a_star_agree():
    graph = make_diamond_graph()

    d_path, d_distance = dijkstra(graph, "A", "D")
    a_path, a_distance = a_star(graph, "A", "D")

    assert d_path == a_path == ["A", "B", "D"]
    assert d_distance == a_distance == 680


def test_has_edge():
    graph = make_diamond_graph()

    assert graph.has_edge("A", "B")
    assert not graph.has_edge("A", "D")  # no direct road
