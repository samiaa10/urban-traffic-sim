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


def test_road_shapes_place_vehicles_on_the_real_road():
    import pytest

    from backend.routing.road_geometry import RoadGeometry

    # An L-shaped road: 100 m north, then 100 m east
    a, corner, b = (52.62, 1.28), (52.620898, 1.28), (52.620898, 1.281476)
    geometry = RoadGeometry([[a, corner, b]])
    lat, lon, heading = geometry.point_at(a, b, 0.25)
    assert lon == pytest.approx(1.28) and heading == pytest.approx(0, abs=1)
    lat, lon, heading = geometry.point_at(a, b, 0.75)
    assert lat == pytest.approx(corner[0]) and heading == pytest.approx(90, abs=1)
    # The other way round: same shape, facing back
    lat, lon, heading = geometry.point_at(b, a, 0.25)
    assert lat == pytest.approx(corner[0]) and heading == pytest.approx(270, abs=1)
    # A road it does not know: the straight line
    unknown = RoadGeometry([]).point_at(a, b, 0.5)
    assert unknown[0] == pytest.approx((a[0] + b[0]) / 2)


def test_the_norwich_road_shapes_match_the_road_lengths():
    import pickle
    import statistics

    from backend.routing.road_geometry import GRAPH_FILE, load
    from backend.routing.travel_time import RoadNetwork

    geometry = load()
    with open(GRAPH_FILE, "rb") as file:
        network = RoadNetwork(pickle.load(file))
    differences = []
    for (a, b), length in network.length.items():
        points = geometry.shape(network.position[a], network.position[b])
        if points is not None and length > 5:
            shape_length = geometry.shapes.get((network.position[a], network.position[b]),
                                               geometry.shapes.get((network.position[b], network.position[a])))[1][-1]
            differences.append(abs(shape_length - length) / length)
    assert len(differences) > 0.95 * len(network.length)
    assert statistics.median(differences) < 0.01
