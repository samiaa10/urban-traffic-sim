"""
Edge cases that the other test files do not cover:

    unreachable places, one-way roads, congestion combined with closures,
    congestion levels, and emergencies when no ambulance is free or the
    road is closed.
"""

from backend.routing.a_star import CONGESTION_PENALTY_METRES, a_star, get_segment
from backend.routing.dijkstra import dijkstra
from backend.routing.graph import Graph
from backend.simulation.rerouting import build_node_lookup, reroute_affected_vehicles
from backend.simulation.simulation import Simulation
from backend.tests.helpers import coords, make_diamond_graph


def make_one_way_graph():
    """
        A ===> B         A to B is one-way (you cannot drive B to A on it)
         \\    /
          C --             C-A and C-B are normal two-way roads
    """
    graph = Graph()

    graph.add_node("A", 52.6300, 1.2900)
    graph.add_node("B", 52.6300, 1.2950)
    graph.add_node("C", 52.6340, 1.2925)

    graph.add_edge("A", "B", 340)      # one-way

    for a, b, metres in [("A", "C", 600), ("C", "B", 450)]:
        graph.add_edge(a, b, metres)
        graph.add_edge(b, a, metres)

    return graph


def parked_ambulance_at(simulation, graph, node):
    """Put a station with one ambulance at a junction; return both."""
    point = graph.nodes[node]
    station = simulation.add_station(point["latitude"], point["longitude"])
    ambulance = simulation.add_emergency_vehicle(
        [station.get_location(), station.get_location()], station
    )
    return station, ambulance


# ------------------------------------------------------------
# Routing: unreachable places, same place, one-way roads
# ------------------------------------------------------------

def test_destination_with_no_road_to_it_gives_no_route():
    graph = make_diamond_graph()
    graph.add_node("Z", 52.7000, 1.3000)      # an island: no roads at all

    for search in (a_star, dijkstra):
        assert search(graph, "A", "Z") == ([], float("inf"))

        path, distance, nodes_explored = search(graph, "A", "Z", return_stats=True)
        assert path == []
        assert distance == float("inf")
        assert nodes_explored > 0      # it did search before giving up


def test_route_from_a_place_to_itself_is_just_that_place():
    graph = make_diamond_graph()

    assert a_star(graph, "A", "A") == (["A"], 0)
    assert dijkstra(graph, "A", "A") == (["A"], 0)


def test_one_way_road_cannot_be_driven_backwards():
    graph = make_one_way_graph()

    # With the one-way road: A to B is direct...
    assert a_star(graph, "A", "B")[0] == ["A", "B"]

    # ...but B to A must go round through C (it may not use A->B backwards)
    for search in (a_star, dijkstra):
        path, distance = search(graph, "B", "A")
        assert path == ["B", "C", "A"]
        assert distance == 450 + 600


def test_destination_only_reachable_against_a_one_way_road_gives_no_route():
    graph = Graph()
    graph.add_node("A", 52.6300, 1.2900)
    graph.add_node("B", 52.6300, 1.2950)
    graph.add_edge("A", "B", 340)      # one-way and nothing else

    assert a_star(graph, "A", "B")[0] == ["A", "B"]
    assert a_star(graph, "B", "A") == ([], float("inf"))
    assert dijkstra(graph, "B", "A") == ([], float("inf"))


def test_closing_a_one_way_road_blocks_it():
    graph = make_one_way_graph()
    closed = {get_segment(graph, "A", "B")}

    path, distance = a_star(graph, "A", "B", blocked_segments=closed)

    assert path == ["A", "C", "B"]
    assert distance == 600 + 450


# ------------------------------------------------------------
# Congestion-aware routing combined with closures
# ------------------------------------------------------------

def test_congestion_never_blocks_a_road_and_never_overrides_a_closure():
    graph = make_diamond_graph()
    busy = 20      # vehicles on A-B: a 1000 m penalty
    traffic = {get_segment(graph, "A", "B"): busy}

    # The long way (C) is closed, so the busy road is the only way:
    # the router still uses it and just pays the penalty.
    closed = {get_segment(graph, "A", "C")}
    path, distance = a_star(
        graph, "A", "D", traffic=traffic, blocked_segments=closed
    )

    assert path == ["A", "B", "D"]
    assert distance == 680 + busy * CONGESTION_PENALTY_METRES

    # With both ways closed there is still no route, however quiet the roads.
    closed.add(get_segment(graph, "A", "B"))
    assert a_star(graph, "A", "D", traffic={}, blocked_segments=closed)[0] == []


def test_traffic_on_a_road_costs_the_same_in_both_directions():
    graph = make_diamond_graph()
    traffic = {get_segment(graph, "A", "B"): 4}

    _, forward = a_star(graph, "A", "B", traffic=traffic)
    _, backward = a_star(graph, "B", "A", traffic=traffic)

    assert forward == backward == 340 + 4 * CONGESTION_PENALTY_METRES


# ------------------------------------------------------------
# Rerouting on one-way streets
# ------------------------------------------------------------

def test_rerouting_does_not_drive_the_wrong_way_down_a_one_way_street():
    graph = make_diamond_graph()
    graph.add_edge("C", "B", 400)      # extra one-way street: C to B only

    # A car at B heading for D; the road B-D is then closed.
    simulation = Simulation(coords(graph, ["B", "D"]))
    simulation.block_segment(get_segment(graph, "B", "D"))

    stats = reroute_affected_vehicles(
        graph, simulation, build_node_lookup(graph)
    )

    # B->C would be shorter, but that street only runs C->B, so the car
    # has to go back through A.
    assert stats["rerouted"] == 1
    assert simulation.vehicles[0].route == coords(graph, ["B", "A", "C", "D"])


# ------------------------------------------------------------
# Congestion levels
# ------------------------------------------------------------

def test_congestion_level_follows_the_number_of_vehicles_on_a_road():
    graph = make_diamond_graph()
    road = get_segment(graph, "A", "B")

    simulation = Simulation(coords(graph, ["A", "B"]))
    assert simulation.get_congestion()[road] == {"vehicle_count": 1, "level": "light"}

    # A car going the opposite way is on the same road
    simulation.add_vehicle(coords(graph, ["B", "A"]))
    assert simulation.get_congestion()[road] == {"vehicle_count": 2, "level": "moderate"}

    simulation.add_vehicle(coords(graph, ["A", "B"]))
    assert simulation.get_congestion()[road] == {"vehicle_count": 3, "level": "heavy"}

    simulation.add_vehicle(coords(graph, ["A", "B"]))
    assert simulation.get_congestion()[road]["level"] == "heavy"


def test_vehicles_that_have_finished_do_not_count_as_traffic():
    graph = make_diamond_graph()
    simulation = Simulation(coords(graph, ["A", "B"]))      # 340 m, about 25 s

    for _ in range(40):
        simulation.step()

    assert simulation.vehicles[0].finished
    assert simulation.get_traffic_density() == {}
    assert simulation.get_congestion() == {}


def test_an_ambulance_back_at_its_station_does_not_count_as_traffic():
    graph = make_diamond_graph()
    simulation = Simulation(coords(graph, ["A", "B"]))
    _, ambulance = parked_ambulance_at(simulation, graph, "A")

    destination = graph.nodes["D"]
    emergency = simulation.create_emergency(
        destination["latitude"], destination["longitude"]
    )
    simulation.dispatch_from_nearest_station(emergency)
    ambulance.set_route(coords(graph, ["A", "B", "D"]), reset_distance=True)

    for _ in range(200):
        if emergency.resolved:
            break
        simulation.step()

    # The emergency is over and the ambulance is parked again.
    # (The civilian car has finished its short trip by now too.)
    assert emergency.resolved
    assert ambulance.finished and ambulance.available
    assert simulation.get_traffic_density() == {}


# ------------------------------------------------------------
# Emergencies when things go wrong
# ------------------------------------------------------------

def test_emergency_stays_unassigned_when_no_ambulance_is_free():
    graph = make_diamond_graph()
    simulation = Simulation(coords(graph, ["A", "B"]))
    point = graph.nodes["A"]

    # No station at all
    no_station = simulation.create_emergency(point["latitude"], point["longitude"])
    assert simulation.dispatch_from_nearest_station(no_station) is None
    assert no_station.assigned_vehicle is None
    assert no_station.active

    # A station whose only ambulance is already out on a call
    parked_ambulance_at(simulation, graph, "A")
    first = simulation.create_emergency(point["latitude"], point["longitude"])
    assert simulation.dispatch_from_nearest_station(first) is not None

    second = simulation.create_emergency(point["latitude"], point["longitude"])
    assert simulation.dispatch_from_nearest_station(second) is None
    assert second.assigned_vehicle is None
    assert second.active


def test_dispatch_skips_the_nearest_station_if_its_ambulance_is_busy():
    graph = make_diamond_graph()
    simulation = Simulation(coords(graph, ["A", "B"]))

    near_station, near_ambulance = parked_ambulance_at(simulation, graph, "A")
    far_station, far_ambulance = parked_ambulance_at(simulation, graph, "D")

    here = graph.nodes["A"]      # an emergency right next to the near station

    first = simulation.create_emergency(here["latitude"], here["longitude"])
    assert simulation.dispatch_from_nearest_station(first) is near_ambulance

    second = simulation.create_emergency(here["latitude"], here["longitude"])
    assert simulation.dispatch_from_nearest_station(second) is far_ambulance
    assert far_ambulance.home_station is far_station


def test_emergency_is_answered_after_a_blocked_road_reopens():
    graph = make_diamond_graph()
    simulation = Simulation(coords(graph, ["A", "C"]))
    _, ambulance = parked_ambulance_at(simulation, graph, "A")

    destination = graph.nodes["D"]
    emergency = simulation.create_emergency(
        destination["latitude"], destination["longitude"]
    )
    simulation.dispatch_from_nearest_station(emergency)
    ambulance.set_route(coords(graph, ["A", "B", "D"]), reset_distance=True)

    closed_road = get_segment(graph, "B", "D")
    simulation.block_segment(closed_road)

    # It drives to the closed road, stops, and the emergency is not resolved
    for _ in range(100):
        simulation.step()

    assert not emergency.resolved
    assert emergency.active
    assert not ambulance.finished

    # Once the road reopens it carries on and the emergency is resolved
    simulation.unblock_segment(closed_road)

    for _ in range(200):
        if emergency.resolved:
            break
        simulation.step()

    assert emergency.resolved
    assert emergency.response_time > 100      # includes the time spent waiting
