"""Dynamic rerouting, fleets, surges and the demand model
(backend/simulation/traffic_manager.py and demand.py)."""

import math
import random
import statistics

import pytest

from backend.routing.a_star import get_segment
from backend.routing.travel_time import RoadNetwork
from backend.simulation.demand import DemandModel, Zone, metres_between
from backend.simulation.simulation import Simulation
from backend.simulation.traffic_manager import ReroutePolicy, TrafficManager
from backend.tests.helpers import coords, make_diamond_graph, make_grid_graph


def diamond_manager(path, **policy):
    """A vehicle parked at the start of `path` on the diamond map.

    A-B-D is 680 m, A-C-D is 1,200 m (see helpers.make_diamond_graph).
    """
    graph = make_diamond_graph()
    network = RoadNetwork(graph)
    simulation = Simulation(coords(graph, path))
    manager = TrafficManager(network, simulation, ReroutePolicy(**policy))
    return graph, simulation, manager


def node_path(manager, vehicle):
    return [
        manager.network.node_at[(p["latitude"], p["longitude"])]
        for p in vehicle.route
    ]


# ------------------------------------------------------------
# Reacting to accidents
# ------------------------------------------------------------

def test_a_severe_accident_ahead_makes_the_vehicle_take_the_faster_way_at_once():
    graph, simulation, manager = diamond_manager(["A", "B", "D"])
    vehicle = simulation.vehicles[0]

    # B-D at 20% speed: 24.5 s + 122 s on the old route, 86 s the other way
    simulation.add_accident(get_segment(graph, "B", "D"), "severe")

    assert manager.process_events() == 1
    assert manager.reroute_pass() == 1
    assert node_path(manager, vehicle) == ["A", "C", "D"]
    assert vehicle.reroutes == 1


def test_a_minor_accident_is_not_worth_a_longer_detour():
    graph, simulation, manager = diamond_manager(["A", "B", "D"])

    # B-D at 70% speed: about 59 s in total, still faster than 86 s
    simulation.add_accident(get_segment(graph, "B", "D"), "minor")
    manager.process_events()
    manager.reroute_pass()

    assert node_path(manager, simulation.vehicles[0]) == ["A", "B", "D"]


def test_an_accident_elsewhere_does_not_trigger_any_search():
    graph, simulation, manager = diamond_manager(["A", "B", "D"])

    simulation.add_accident(get_segment(graph, "A", "C"), "severe")

    assert manager.process_events() == 0
    manager.reroute_pass()
    assert manager.stats["searches"] == 0


def test_events_are_only_handled_once():
    graph, simulation, manager = diamond_manager(["A", "B", "D"])
    simulation.add_accident(get_segment(graph, "B", "D"), "severe")

    assert manager.process_events() == 1
    assert manager.process_events() == 0


# ------------------------------------------------------------
# Roads getting better again
# ------------------------------------------------------------

def test_vehicle_switches_back_when_the_accident_clears():
    graph, simulation, manager = diamond_manager(["A", "B", "D"])
    road = get_segment(graph, "A", "B")
    simulation.add_accident(road, "severe")
    manager.process_events()
    manager.reroute_pass()
    assert node_path(manager, simulation.vehicles[0]) == ["A", "C", "D"]

    simulation.clear_accident(road)

    assert manager.process_events() == 1
    manager.reroute_pass()
    assert node_path(manager, simulation.vehicles[0]) == ["A", "B", "D"]


def test_vehicle_switches_back_when_a_closed_road_reopens():
    graph, simulation, manager = diamond_manager(["A", "B", "D"])
    road = get_segment(graph, "B", "D")
    simulation.block_segment(road)
    manager.process_events()
    manager.reroute_pass()
    assert node_path(manager, simulation.vehicles[0]) == ["A", "C", "D"]

    simulation.unblock_segment(road)
    assert manager.process_events() == 1
    manager.reroute_pass()

    assert node_path(manager, simulation.vehicles[0]) == ["A", "B", "D"]


def test_only_vehicles_that_avoided_a_road_are_rechecked_when_it_clears():
    graph, simulation, manager = diamond_manager(["A", "B", "D"])
    bystander = simulation.add_vehicle(coords(graph, ["A", "C", "D"]))
    road = get_segment(graph, "A", "B")

    simulation.add_accident(road, "severe")
    manager.process_events()
    manager.reroute_pass()

    simulation.clear_accident(road)

    # The first vehicle went round the accident; the second never used it
    assert manager.process_events() == 1
    assert not getattr(bystander, "avoided_roads", set())


def test_a_reopened_road_far_off_the_vehicle_s_way_is_ignored():
    graph = make_grid_graph(size=8)
    network = RoadNetwork(graph)
    # Along the bottom row, far from the top-right corner road
    simulation = Simulation(network.points(list(range(0, 8))))
    manager = TrafficManager(network, simulation)

    corner = network.segment[(62, 63)]
    simulation.block_segment(corner)
    manager.process_events()
    simulation.unblock_segment(corner)

    assert manager.process_events() == 0


def test_closed_road_with_no_way_round_keeps_the_route_and_waits():
    graph = make_diamond_graph()
    graph.add_node("E", 52.6300, 1.3050)
    graph.add_edge("D", "E", 340)
    graph.add_edge("E", "D", 340)
    network = RoadNetwork(graph)
    simulation = Simulation(coords(graph, ["A", "B", "D", "E"]))
    manager = TrafficManager(network, simulation)

    simulation.block_segment(get_segment(graph, "D", "E"))   # the only way to E
    manager.process_events()
    manager.reroute_pass()

    assert node_path(manager, simulation.vehicles[0]) == ["A", "B", "D", "E"]


# ------------------------------------------------------------
# Routine checks, thresholds and damping
# ------------------------------------------------------------

def test_routine_checks_are_staggered_by_vehicle():
    graph, simulation, manager = diamond_manager(["A", "C", "D"], check_interval=30)

    # A faster route exists (A-B-D), but there is no event: only the
    # routine check finds it, and only on this vehicle's turn
    for now in (1, 7, 29):
        simulation.time = now
        assert manager.reroute_pass() == 0

    simulation.time = 30
    assert manager.reroute_pass() == 1


def test_cooldown_stops_a_second_routine_switch_soon_after_the_first():
    graph, simulation, manager = diamond_manager(
        ["A", "C", "D"], check_interval=30, cooldown_seconds=60
    )
    simulation.time = 30
    manager.reroute_pass()                       # switches to A-B-D

    # Now make A-B-D bad, without telling the manager (routine path only)
    simulation.add_accident(get_segment(graph, "B", "D"), "severe")
    manager._events_seen = len(simulation.events)

    simulation.time = 60                          # 30 s after switching
    assert manager.reroute_pass() == 0

    simulation.time = 90                          # 60 s after switching
    assert manager.reroute_pass() == 1


def test_an_event_overrides_the_cooldown():
    graph, simulation, manager = diamond_manager(["A", "C", "D"], cooldown_seconds=600)
    simulation.time = 30
    manager.reroute_pass()

    simulation.time = 31
    simulation.add_accident(get_segment(graph, "B", "D"), "severe")
    manager.process_events()

    assert manager.reroute_pass() == 1


def test_small_savings_are_ignored():
    # A-C-D vs A-B-D saves 37 s; demand more than that
    graph, simulation, manager = diamond_manager(
        ["A", "C", "D"], check_interval=1, min_saving_seconds=40
    )

    assert manager.reroute_pass() == 0


def test_no_search_when_no_route_could_save_enough():
    graph, simulation, manager = diamond_manager(["A", "B", "D"], check_interval=1)

    manager.reroute_pass()

    assert manager.stats["skipped_no_possible_saving"] == 1
    assert manager.stats["searches"] == 0


def test_rerouting_can_be_switched_off():
    graph, simulation, manager = diamond_manager(["A", "B", "D"], enabled=False)
    simulation.add_accident(get_segment(graph, "B", "D"), "severe")
    manager.process_events()

    assert manager.reroute_pass() == 0
    assert node_path(manager, simulation.vehicles[0]) == ["A", "B", "D"]


def test_a_vehicle_part_way_along_a_road_finishes_it_before_turning():
    graph, simulation, manager = diamond_manager(["A", "B", "D"])
    vehicle = simulation.vehicles[0]
    manager.step()                                  # now part-way along A-B
    here = vehicle.get_current_location()

    simulation.add_accident(get_segment(graph, "B", "D"), "severe")
    manager.process_events()
    manager.reroute_pass()

    # From B the other way is B-A-C-D, much longer than crawling B-D, so the
    # vehicle keeps going; either way its route starts where it is now
    assert vehicle.route[0] == here or node_path(manager, vehicle)[0] == "A"


def congested_grid_run(policy, steps=600):
    """150 vehicles on an 8 x 8 grid (heavily congested), run for `steps` s."""

    network = RoadNetwork(make_grid_graph(size=8))
    simulation = Simulation(network.points([0, 0]))     # parked
    manager = TrafficManager(network, simulation, policy)
    manager.spawn_vehicles(150, random.Random(4))

    for _ in range(steps):
        manager.step()

    fleet = simulation.vehicles[1:]
    finished = [v for v in fleet if v.finished]

    return {
        "reroutes": [v.reroutes for v in fleet],
        "finished": len(finished),
        "mean_journey": statistics.mean(v.journey_seconds for v in finished),
    }


def test_damped_rerouting_shortens_journeys_without_constant_switching():
    steps = 600
    off = congested_grid_run(ReroutePolicy(enabled=False), steps)
    damped = congested_grid_run(ReroutePolicy(check_interval=10, cooldown_seconds=60), steps)
    undamped = congested_grid_run(
        ReroutePolicy(
            check_interval=1, cooldown_seconds=0, min_saving_seconds=1e-6,
            min_saving_fraction=0, smoothing_seconds=1,
        ),
        steps,
    )

    assert off["finished"] == damped["finished"] == 150

    # Re-planning around congestion gets people there sooner...
    assert damped["mean_journey"] < 0.9 * off["mean_journey"]

    # ...the cooldown caps how often any one vehicle can switch...
    assert max(damped["reroutes"]) <= 1 + steps // 60

    # ...and far fewer switches are made than with no damping at all
    assert sum(damped["reroutes"]) < sum(undamped["reroutes"]) / 5


# ------------------------------------------------------------
# Consistency: what the router plans is what the vehicle drives
# ------------------------------------------------------------

def test_planned_time_matches_the_simulated_journey_on_empty_roads():
    graph = make_grid_graph(size=8)
    network = RoadNetwork(graph)
    simulation = Simulation(network.points([0, 0]))   # a parked first vehicle
    manager = TrafficManager(network, simulation)

    path, planned, _ = manager.plan(0, 63, 13.9)
    vehicle = simulation.add_vehicle(network.points(path))

    while not vehicle.finished:
        simulation.step()

    assert vehicle.distance_travelled == pytest.approx(network.path_length(path))
    assert vehicle.journey_seconds == math.ceil(planned)


# ------------------------------------------------------------
# Fleets
# ------------------------------------------------------------

def grid_manager(size=8, **policy):
    graph = make_grid_graph(size=size)
    network = RoadNetwork(graph)
    simulation = Simulation(network.points([0, 1]))
    return network, simulation, TrafficManager(network, simulation, ReroutePolicy(**policy))


def test_spawned_vehicles_follow_real_roads_between_their_trip_ends():
    network, simulation, manager = grid_manager()

    outcome = manager.spawn_vehicles(40, random.Random(2))

    assert outcome["spawned"] == 40 and outcome["failed"] == 0
    assert len(simulation.vehicles) == 41

    for vehicle in outcome["vehicles"]:
        nodes = node_path(manager, vehicle)
        assert nodes[0] == vehicle.origin and nodes[-1] == vehicle.destination
        assert all((a, b) in network.length for a, b in zip(nodes, nodes[1:]))


def test_spawning_is_repeatable_with_the_same_seed():
    def trips(seed):
        _, _, manager = grid_manager()
        return [
            (v.origin, v.destination)
            for v in manager.spawn_vehicles(20, random.Random(seed))["vehicles"]
        ]

    assert trips(9) == trips(9)
    assert trips(9) != trips(10)


def test_spawned_vehicles_avoid_closed_roads():
    network, simulation, manager = grid_manager()
    closed = {network.segment[(27, 28)], network.segment[(35, 36)]}
    for segment in closed:
        simulation.block_segment(segment)

    for vehicle in manager.spawn_vehicles(60, random.Random(3))["vehicles"]:
        nodes = node_path(manager, vehicle)
        assert not {network.segment[(a, b)] for a, b in zip(nodes, nodes[1:])} & closed


def test_a_large_fleet_slows_traffic_down():
    network, simulation, manager = grid_manager(size=6, enabled=False)
    manager.spawn_vehicles(300, random.Random(5))

    for _ in range(20):
        simulation.step()

    driving = [v for v in simulation.vehicles if not v.finished]
    assert statistics.mean(v.speed_factor for v in driving) < 0.9


# ------------------------------------------------------------
# Surges
# ------------------------------------------------------------

def test_an_inbound_surge_sends_trips_into_the_zone_for_its_duration():
    network, simulation, manager = grid_manager(size=8)
    zone = Zone(52.627, 1.2905, 400)
    manager.add_surge(zone, "inbound", vehicles_per_minute=30, duration=120)

    for _ in range(200):
        manager.step()

    surge = manager.surges[0]
    assert surge.spawned == 60          # 30 a minute for 2 minutes
    assert not surge.active(simulation.time)

    for vehicle in simulation.vehicles[1:]:
        latitude, longitude = network.position[vehicle.destination]
        assert metres_between(latitude, longitude, zone.latitude, zone.longitude) <= 400


def test_an_outbound_surge_starts_trips_in_the_zone():
    network, simulation, manager = grid_manager(size=8)
    zone = Zone(52.627, 1.2905, 400)
    manager.add_surge(zone, "outbound", vehicles_per_minute=60, duration=30)

    for _ in range(40):
        manager.step()

    new = simulation.vehicles[1:]
    assert len(new) == 30
    for vehicle in new:
        latitude, longitude = network.position[vehicle.origin]
        assert metres_between(latitude, longitude, zone.latitude, zone.longitude) <= 400


def test_surge_settings_are_checked():
    network, simulation, manager = grid_manager()
    zone = Zone(52.627, 1.2905, 400)

    with pytest.raises(ValueError):
        manager.add_surge(zone, "sideways")
    with pytest.raises(ValueError):
        manager.add_surge(zone, vehicles_per_minute=0)
    with pytest.raises(ValueError):
        manager.add_surge(zone, duration=-1)
    with pytest.raises(ValueError):
        manager.add_surge(Zone(51.5, -0.12, 500))       # London: no junctions


# ------------------------------------------------------------
# Demand model
# ------------------------------------------------------------

def test_trips_are_at_least_the_minimum_length_and_inside_the_main_component():
    network = RoadNetwork(make_grid_graph(size=8))
    demand = DemandModel(network, min_trip_metres=900)
    generator = random.Random(1)

    for _ in range(200):
        origin, destination = demand.random_trip(generator)
        assert network.straight_line_metres(origin, destination) >= 900
        assert {origin, destination} <= network.main_component()


def mean_distance_to(network, centre, nodes):
    return statistics.mean(
        metres_between(*network.position[node], *centre) for node in nodes
    )


def test_destinations_lean_towards_the_centre():
    network = RoadNetwork(make_grid_graph(size=10))
    centre = network.position[55]
    demand = DemandModel(network, centre=centre, centre_share=0.7, centre_scale_metres=400)
    generator = random.Random(8)

    trips = [demand.random_trip(generator) for _ in range(2000)]

    origins = mean_distance_to(network, centre, (o for o, _ in trips))
    destinations = mean_distance_to(network, centre, (d for _, d in trips))

    assert destinations < 0.75 * origins


def test_with_no_centre_share_destinations_are_spread_like_origins():
    network = RoadNetwork(make_grid_graph(size=10))
    centre = network.position[55]
    demand = DemandModel(network, centre=centre, centre_share=0.0)
    generator = random.Random(8)

    trips = [demand.random_trip(generator) for _ in range(2000)]

    origins = mean_distance_to(network, centre, (o for o, _ in trips))
    destinations = mean_distance_to(network, centre, (d for _, d in trips))

    assert destinations == pytest.approx(origins, rel=0.1)


def test_impossible_trip_returns_none():
    network = RoadNetwork(make_grid_graph(size=4))
    demand = DemandModel(network, min_trip_metres=100_000)

    assert demand.random_trip(random.Random(1)) is None


def test_zone_must_have_a_positive_radius():
    with pytest.raises(ValueError):
        Zone(52.62, 1.28, 0)


# ------------------------------------------------------------
# The per-step cache of slowed roads
# ------------------------------------------------------------

def test_slowed_roads_follow_changes_between_steps():
    graph, simulation, manager = diamond_manager(["A", "B", "D"])
    road = get_segment(graph, "B", "D")
    factor = manager.joining_speed_factor(manager.recent_traffic)

    assert factor(road) == 1.0

    # Same simulated second, but an accident appears: must not be served stale
    simulation.add_accident(road, "severe")
    factor = manager.joining_speed_factor(manager.recent_traffic)
    assert factor(road) == pytest.approx(0.2)

    simulation.clear_accident(road)
    factor = manager.joining_speed_factor(manager.recent_traffic)
    assert factor(road) == 1.0


def test_a_traffic_table_passed_in_is_never_served_from_the_cache():
    graph, simulation, manager = diamond_manager(["A", "B", "D"])
    road = get_segment(graph, "B", "D")

    quiet = manager.joining_speed_factor({})
    busy = manager.joining_speed_factor({road: 2})     # joining makes 3: heavy

    assert quiet(road) == 1.0
    assert busy(road) == pytest.approx(0.3)


# ------------------------------------------------------------
# Timing of reactions
# ------------------------------------------------------------

def test_a_flagged_vehicle_re_plans_before_it_moves():
    graph, simulation, manager = diamond_manager(["A", "B", "D"])
    vehicle = simulation.vehicles[0]

    # An accident appears between steps (as from the API)
    simulation.add_accident(get_segment(graph, "A", "B"), "severe")
    manager.step()

    # It turned off before driving onto A-B, so it is heading for C
    assert vehicle.route[1] == coords(graph, ["C"])[0]
    assert vehicle.distance_travelled > 0


def test_planning_sees_vehicles_that_changed_road_since_the_last_step():
    graph, simulation, manager = diamond_manager(["A", "B", "D"])
    simulation.add_vehicle(coords(graph, ["A", "B", "D"]))
    road = get_segment(graph, "A", "B")

    # Both vehicles move off A-B without a step (as a closure reroute does)
    for vehicle in simulation.vehicles:
        vehicle.set_route(coords(graph, ["A", "C", "D"]))

    factor = manager.joining_speed_factor(manager.planning_traffic())

    assert factor(road) == 1.0          # nobody is on A-B any more


def test_a_re_check_for_an_improved_road_only_switches_onto_that_road():
    graph, simulation, manager = diamond_manager(["A", "C", "D"])
    vehicle = simulation.vehicles[0]
    traffic = manager.planning_traffic()

    # A-B-D is faster, but the road that improved (A-C) is not on it, so
    # this out-of-schedule re-check does not switch
    assert not manager.consider(
        vehicle, traffic, urgent=True, must_use={get_segment(graph, "A", "C")}
    )
    assert node_path(manager, vehicle) == ["A", "C", "D"]

    # The improved road is on the faster route: switch
    assert manager.consider(
        vehicle, traffic, urgent=True, must_use={get_segment(graph, "A", "B")}
    )
    assert node_path(manager, vehicle) == ["A", "B", "D"]
