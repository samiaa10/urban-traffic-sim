from backend.routing.graph import Graph
from backend.simulation import dispatch
from backend.simulation.rerouting import build_node_lookup
from backend.simulation.simulation import Simulation
from backend.tests.helpers import coords


def make_street_graph(with_bypass=True):
    """
    A main street with an optional bypass:

        A --- B --- C --- D --- E       (main street)
               \\         /
                -- X ----              (bypass, only if with_bypass)

    Each main-street block is about 340 m. Roads are two-way.
    """
    graph = Graph()

    for number, name in enumerate("ABCDE"):
        graph.add_node(name, 52.6300, 1.2900 + 0.005 * number)

    graph.add_node("X", 52.6340, 1.2950)

    def two_way(a, b, metres):
        graph.add_edge(a, b, metres)
        graph.add_edge(b, a, metres)

    for a, b in ["AB", "BC", "CD", "DE"]:
        two_way(a, b, 340)

    if with_bypass:
        two_way("B", "X", 450)
        two_way("X", "D", 450)

    return graph


def make_dispatched_ambulance(graph, speed=20):
    """A station at A, an emergency at E, and the ambulance already sent."""
    start = graph.nodes["A"]
    end = graph.nodes["E"]

    simulation = Simulation(coords(graph, ["A", "B"]))   # a civilian car
    station = simulation.add_station(start["latitude"], start["longitude"])
    ambulance = simulation.add_emergency_vehicle(
        [station.get_location(), station.get_location()],
        station,
        speed=speed,
    )

    emergency = simulation.create_emergency(end["latitude"], end["longitude"])
    simulation.dispatch_from_nearest_station(emergency)
    ambulance.set_route(coords(graph, list("ABCDE")), reset_distance=True)

    return simulation, ambulance, emergency


def run_until_resolved(simulation, emergency, max_steps=1000):
    for _ in range(max_steps):
        if emergency.resolved:
            break
        simulation.step()


def test_status_while_driving_then_arrived():
    graph = make_street_graph()
    simulation, ambulance, emergency = make_dispatched_ambulance(graph)

    status = dispatch.emergency_status(simulation, emergency)
    assert status["state"] == "en_route"
    assert status["vehicle_id"] == simulation.vehicles.index(ambulance)
    assert status["remaining_metres"] > 1000      # about 4 blocks to go
    assert status["response_time_seconds"] is None

    for _ in range(5):
        simulation.step()

    later = dispatch.emergency_status(simulation, emergency)
    assert later["elapsed_seconds"] == 5
    assert later["remaining_metres"] < status["remaining_metres"]

    run_until_resolved(simulation, emergency)

    done = dispatch.emergency_status(simulation, emergency)
    assert done["state"] == "arrived"
    assert done["remaining_metres"] == 0
    assert done["response_time_seconds"] == emergency.response_time


def test_status_is_waiting_before_a_vehicle_is_sent():
    graph = make_street_graph()
    simulation, _, _ = make_dispatched_ambulance(graph)

    nobody_sent = simulation.create_emergency(52.63, 1.29)

    assert dispatch.emergency_status(simulation, nobody_sent)["state"] == "waiting"


def test_closing_a_road_ahead_reroutes_the_ambulance_around_it():
    graph = make_street_graph()
    simulation, ambulance, emergency = make_dispatched_ambulance(graph)
    lookup = build_node_lookup(graph)

    result = dispatch.block_road_ahead(graph, simulation, lookup)

    assert result["outcome"] == "rerouted"
    # The middle road of the remaining journey (C-D) is the one closed
    assert len(simulation.get_blocked_segments()) == 1
    # The new route goes round via the bypass, so it is longer
    assert ambulance.route == coords(graph, ["A", "B", "X", "D", "E"])
    assert result["extra_metres"] > 0
    assert result["extra_seconds"] > 0
    assert ambulance.reroutes == 1

    # The ambulance still reaches the emergency, and does not use the closed road
    run_until_resolved(simulation, emergency)
    assert emergency.resolved
    assert dispatch.emergency_status(simulation, emergency)["reroutes"] == 1


def test_detour_makes_the_response_slower():
    graph = make_street_graph()

    direct_sim, _, direct_emergency = make_dispatched_ambulance(graph)
    run_until_resolved(direct_sim, direct_emergency)

    detour_sim, _, detour_emergency = make_dispatched_ambulance(graph)
    dispatch.block_road_ahead(graph, detour_sim, build_node_lookup(graph))
    run_until_resolved(detour_sim, detour_emergency)

    assert detour_emergency.response_time > direct_emergency.response_time


def test_nothing_is_closed_if_it_would_cut_the_ambulance_off():
    graph = make_street_graph(with_bypass=False)
    simulation, ambulance, emergency = make_dispatched_ambulance(graph)
    original_route = list(ambulance.route)

    result = dispatch.block_road_ahead(
        graph, simulation, build_node_lookup(graph)
    )

    assert result["outcome"] == "no_detour"
    assert len(simulation.get_blocked_segments()) == 0
    assert ambulance.route == original_route

    run_until_resolved(simulation, emergency)
    assert emergency.resolved


def test_no_ambulance_on_its_way_means_nothing_to_close():
    graph = make_street_graph()
    simulation, _, emergency = make_dispatched_ambulance(graph)
    run_until_resolved(simulation, emergency)

    result = dispatch.block_road_ahead(
        graph, simulation, build_node_lookup(graph)
    )

    assert result["outcome"] == "no_emergency"


def test_too_little_road_left_to_close_one():
    graph = make_street_graph()
    simulation, ambulance, _ = make_dispatched_ambulance(graph)

    # Only one block left: closing it would cut the emergency off
    ambulance.set_route(coords(graph, ["D", "E"]), reset_distance=True)

    result = dispatch.block_road_ahead(
        graph, simulation, build_node_lookup(graph)
    )

    assert result["outcome"] == "too_close"
    assert len(simulation.get_blocked_segments()) == 0


def test_status_says_blocked_when_the_ambulance_is_stopped_by_a_closure():
    graph = make_street_graph(with_bypass=False)
    simulation, ambulance, emergency = make_dispatched_ambulance(graph)

    # Close C-D by hand, with no detour available (the "stranded" case)
    c, d = graph.nodes["C"], graph.nodes["D"]
    simulation.block_segment((
        (c["latitude"], c["longitude"]),
        (d["latitude"], d["longitude"]),
    ))

    for _ in range(100):
        simulation.step()

    status = dispatch.emergency_status(simulation, emergency)
    assert status["state"] == "blocked"
    assert not emergency.resolved
