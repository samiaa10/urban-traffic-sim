from backend.routing.a_star import get_segment
from backend.simulation.rerouting import (
    build_node_lookup,
    reroute_affected_vehicles,
    reroute_vehicle,
)
from backend.simulation.simulation import Simulation
from backend.tests.helpers import make_diamond_graph, coords


def segment_of(graph, a, b):
    return (
        (graph.nodes[a]["latitude"], graph.nodes[a]["longitude"]),
        (graph.nodes[b]["latitude"], graph.nodes[b]["longitude"]),
    )


def make_world():
    """Diamond map, one car going A -> B -> D."""
    graph = make_diamond_graph()
    simulation = Simulation(coords(graph, ["A", "B", "D"]))
    return graph, simulation, simulation.vehicles[0], build_node_lookup(graph)


def run_until_finished(simulation, vehicle, max_steps=500):
    for _ in range(max_steps):
        if vehicle.finished:
            break
        simulation.step()


def test_vehicle_heading_into_a_closure_gets_a_new_route():
    graph, simulation, car, lookup = make_world()

    simulation.block_segment(segment_of(graph, "B", "D"))
    stats = reroute_affected_vehicles(graph, simulation, lookup)

    assert car.route == coords(graph, ["A", "C", "D"])
    assert stats["affected"] == 1
    assert stats["rerouted"] == 1

    run_until_finished(simulation, car)
    assert car.finished


def test_vehicle_not_using_the_closed_road_is_left_alone():
    graph, simulation, car, lookup = make_world()
    other = simulation.add_vehicle(coords(graph, ["A", "C", "D"]))
    original_route = list(other.route)

    simulation.block_segment(segment_of(graph, "A", "B"))
    stats = reroute_affected_vehicles(graph, simulation, lookup)

    assert other.route == original_route
    assert stats["affected"] == 1          # only the first car
    assert stats["recalculations"] == 1    # so only one A* search


def test_checking_everything_does_more_work_than_checking_affected_only():
    graph, simulation, car, lookup = make_world()
    simulation.add_vehicle(coords(graph, ["A", "C", "D"]))
    simulation.add_vehicle(coords(graph, ["D", "C", "A"]))

    simulation.block_segment(segment_of(graph, "A", "B"))
    smart = reroute_affected_vehicles(graph, simulation, lookup)

    graph2, simulation2, _, lookup2 = make_world()
    simulation2.add_vehicle(coords(graph2, ["A", "C", "D"]))
    simulation2.add_vehicle(coords(graph2, ["D", "C", "A"]))
    simulation2.block_segment(segment_of(graph2, "A", "B"))
    everything = reroute_affected_vehicles(
        graph2, simulation2, lookup2, only_affected=False
    )

    assert smart["recalculations"] == 1
    assert everything["recalculations"] == 3


def test_vehicle_in_the_middle_of_a_road_does_not_teleport():
    graph, simulation, car, lookup = make_world()

    for _ in range(10):          # drive part of the way along A -> B
        simulation.step()

    before = car.get_current_location()

    simulation.block_segment(segment_of(graph, "B", "D"))
    reroute_affected_vehicles(graph, simulation, lookup)

    # New route starts exactly where the car is, then reaches junction B
    assert car.route[0] == before
    assert car.route[1] == coords(graph, ["B"])[0]
    assert car.get_current_location() == before

    run_until_finished(simulation, car)
    assert car.finished


def test_vehicle_with_no_other_way_keeps_its_route():
    graph, simulation, car, lookup = make_world()
    original_route = list(car.route)

    simulation.block_segment(segment_of(graph, "B", "D"))
    simulation.block_segment(segment_of(graph, "C", "D"))   # D is cut off
    stats = reroute_affected_vehicles(graph, simulation, lookup)

    assert stats["stranded"] == 1
    assert car.route == original_route


def test_rerouting_twice_gives_the_same_route():
    # The frontend calls the manual reroute right after the automatic one,
    # so doing it again must not change anything.
    graph, simulation, car, lookup = make_world()

    for _ in range(10):
        simulation.step()

    simulation.block_segment(segment_of(graph, "B", "D"))
    reroute_affected_vehicles(graph, simulation, lookup)
    first = list(car.route)

    result = reroute_vehicle(
        graph,
        car,
        lookup,
        traffic=simulation.get_traffic_density(),
        blocked_segments=simulation.get_blocked_segments(),
    )

    assert result["status"] == "rerouted"
    assert car.route == first


def test_finished_vehicles_are_ignored():
    graph, simulation, car, lookup = make_world()
    run_until_finished(simulation, car)

    simulation.block_segment(segment_of(graph, "B", "D"))
    stats = reroute_affected_vehicles(graph, simulation, lookup)

    assert stats["vehicles_checked"] == 0
