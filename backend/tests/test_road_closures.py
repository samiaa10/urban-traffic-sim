from backend.routing.a_star import a_star, get_segment
from backend.simulation.simulation import Simulation
from backend.tests.helpers import make_diamond_graph, coords


def test_route_avoids_a_closed_road():
    graph = make_diamond_graph()
    closed = {get_segment(graph, "A", "B")}

    path, distance = a_star(graph, "A", "D", blocked_segments=closed)

    assert path == ["A", "C", "D"]      # forced to take the long way round
    assert distance == 1200


def test_closure_blocks_both_directions():
    graph = make_diamond_graph()
    closed = {get_segment(graph, "B", "A")}   # given in the opposite order

    path, _ = a_star(graph, "A", "D", blocked_segments=closed)
    reverse_path, _ = a_star(graph, "D", "A", blocked_segments=closed)

    assert "B" not in path or path[:2] != ["A", "B"]
    assert path == ["A", "C", "D"]
    assert reverse_path == ["D", "C", "A"]


def test_no_route_when_every_way_is_closed():
    graph = make_diamond_graph()
    closed = {
        get_segment(graph, "A", "B"),
        get_segment(graph, "A", "C"),
    }

    path, distance = a_star(graph, "A", "D", blocked_segments=closed)

    assert path == []
    assert distance == float("inf")


def test_reopening_a_road_restores_the_short_route():
    graph = make_diamond_graph()
    simulation = Simulation(coords(graph, ["A", "B", "D"]))

    segment = (
        (graph.nodes["A"]["latitude"], graph.nodes["A"]["longitude"]),
        (graph.nodes["B"]["latitude"], graph.nodes["B"]["longitude"]),
    )

    simulation.block_segment(segment)
    blocked_path, _ = a_star(
        graph, "A", "D", blocked_segments=simulation.get_blocked_segments()
    )

    simulation.unblock_segment(segment)
    open_path, _ = a_star(
        graph, "A", "D", blocked_segments=simulation.get_blocked_segments()
    )

    assert blocked_path == ["A", "C", "D"]
    assert open_path == ["A", "B", "D"]


def test_vehicle_waits_at_a_closed_road_until_it_reopens():
    graph = make_diamond_graph()
    simulation = Simulation(coords(graph, ["A", "B", "D"]))
    car = simulation.vehicles[0]

    # Close the second half of the journey (B -> D)
    segment = (
        (graph.nodes["B"]["latitude"], graph.nodes["B"]["longitude"]),
        (graph.nodes["D"]["latitude"], graph.nodes["D"]["longitude"]),
    )
    simulation.block_segment(segment)

    for _ in range(200):
        simulation.step()

    # It drove to B (340 m) and then stopped
    assert not car.finished
    assert car.position == 1
    assert car.distance_on_segment == 0

    simulation.unblock_segment(segment)

    for _ in range(200):
        simulation.step()

    assert car.finished
