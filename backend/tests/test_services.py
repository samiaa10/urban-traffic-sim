"""Emergency services (simulation/services.py): ambulance, fire and police,
drive-time dispatch, the queue, driving back, right of way, and
population-weighted emergency locations (simulation/population.py)."""

import random

import pytest

from backend.routing.graph import Graph
from backend.routing.road_attributes import MPH, RoadInfo
from backend.routing.travel_time import RoadNetwork
from backend.scenarios.runner import World, run_scenario
from backend.scenarios.spec import ScenarioSpec
from backend.simulation import dispatch
from backend.simulation.population import Sampler, junction_weights
from backend.simulation.road_model import make_road_model
from backend.simulation.services import (
    ORIGINAL_RULES,
    REALISTIC_RULES,
    SERVICES,
    rules_for,
    service,
)
from backend.simulation.simulation import Simulation
from backend.simulation.traffic_manager import TrafficManager
from backend.tests.helpers import make_grid_graph


def info(limit_mph=30):
    return RoadInfo("residential", limit_mph * MPH, True, 1, True, False, None)


def two_way(graph, a, b, length):
    for x, y in ((a, b), (b, a)):
        graph.add_edge(x, y, length)
        graph.road_info[(x, y)] = info()


def river_graph():
    """Emergency at junction 0. Station A at junction 1 is 150 m away in a
    straight line but across a river: by road it is 3 km. Station B at
    junction 3 is 400 m away and has a direct road."""

    graph = Graph()
    graph.add_node(0, 52.6200, 1.2800)      # the emergency
    graph.add_node(1, 52.62135, 1.2800)     # A: just across the river (north)
    graph.add_node(2, 52.6200, 1.3200)      # the bridge, far east
    graph.add_node(3, 52.6200, 1.2741)      # B: west, direct road
    two_way(graph, 1, 2, 1500)
    two_way(graph, 2, 0, 1500)
    two_way(graph, 3, 0, 420)
    return graph


def osm_simulation(graph):
    network = RoadNetwork(graph)
    point = network.point(0)
    simulation = Simulation([point, point], road_model=make_road_model("osm", network))
    simulation.vehicles[0].finished = True
    simulation.use_road_lengths(network.length_between_points)
    return simulation, network


def add_station(simulation, network, node, service_name="ambulance", vehicles=1):
    latitude, longitude = network.position[node]
    station = simulation.add_station(latitude, longitude, service_name)
    location = station.get_location()
    for _ in range(vehicles):
        simulation.add_emergency_vehicle(
            [location, location], station, speed=SERVICES[service_name].speed,
            vehicle_type=service_name,
        )
    return station


def run_until_resolved(simulation, emergencies, limit=2000):
    for _ in range(limit):
        simulation.step()
        dispatch.after_step(simulation)
        if all(e.resolved for e in emergencies):
            return
    raise AssertionError("emergencies not resolved")


# ------------------------------------------------------------
# The services
# ------------------------------------------------------------

def test_three_services_with_their_own_speeds_and_targets():
    assert set(SERVICES) == {"ambulance", "fire", "police"}
    assert service("fire").speed < service("ambulance").speed < service("police").speed
    assert service("ambulance").target_seconds == 420
    with pytest.raises(ValueError, match="ambulance, fire, police"):
        service("coastguard")


def test_the_realistic_rules_come_with_the_osm_model():
    network = RoadNetwork(make_grid_graph(size=3))
    assert rules_for(make_road_model("simple")) is ORIGINAL_RULES
    assert rules_for(make_road_model("osm", network)) is REALISTIC_RULES


def test_right_of_way_halves_the_slowdown():
    assert REALISTIC_RULES.emergency_factor(0.4) == pytest.approx(0.7)
    assert REALISTIC_RULES.emergency_factor(1.0) == 1.0
    assert ORIGINAL_RULES.emergency_factor(0.4) == 0.4


# ------------------------------------------------------------
# Dispatch
# ------------------------------------------------------------

def test_the_vehicle_that_arrives_first_is_sent_not_the_nearest_in_a_line():
    simulation, network = osm_simulation(river_graph())
    add_station(simulation, network, 1)      # A: across the river
    station_b = add_station(simulation, network, 3)

    outcome = dispatch.dispatch_emergency(network.graph, simulation, 52.62, 1.28)

    assert outcome["status"] == "dispatched"
    assert outcome["vehicle"].home_station is station_b
    assert outcome["path"] == [3, 0]

    # The original rules send A (nearest in a straight line)
    original = Simulation([network.point(0)] * 2)
    original.vehicles[0].finished = True
    add_station(original, network, 1)
    add_station(original, network, 3)
    sent = dispatch.dispatch_emergency(network.graph, original, 52.62, 1.28)["vehicle"]
    assert sent.home_station.latitude == network.position[1][0]


def test_only_the_service_the_emergency_needs_is_sent():
    simulation, network = osm_simulation(river_graph())
    add_station(simulation, network, 3, "ambulance")
    add_station(simulation, network, 1, "fire")

    outcome = dispatch.dispatch_emergency(network.graph, simulation, 52.62, 1.28, "fire")
    assert outcome["vehicle"].vehicle_type == "fire"

    # No police station at all: refused, not queued forever
    outcome = dispatch.dispatch_emergency(network.graph, simulation, 52.62, 1.28, "police")
    assert outcome["status"] == "no_vehicle"


def test_an_emergency_waits_in_the_queue_until_a_vehicle_is_free():
    simulation, network = osm_simulation(river_graph())
    station = add_station(simulation, network, 3)

    first = dispatch.dispatch_emergency(network.graph, simulation, 52.62, 1.28)
    second = dispatch.dispatch_emergency(network.graph, simulation, 52.62, 1.28)

    assert first["status"] == "dispatched"
    assert second["status"] == "queued"
    assert simulation.queued_emergencies() == [second["emergency"]]
    assert dispatch.emergency_status(simulation, second["emergency"])["state"] == "waiting"

    run_until_resolved(simulation, [first["emergency"], second["emergency"]])

    waited = second["emergency"].dispatched_at - second["emergency"].created_at
    assert waited > 0
    # Its response time includes the wait
    assert second["emergency"].response_time > first["emergency"].response_time
    status = dispatch.emergency_status(simulation, second["emergency"])
    assert status["waited_seconds"] == waited
    assert status["service"] == "ambulance"


def test_the_queue_is_answered_oldest_first():
    simulation, network = osm_simulation(river_graph())
    add_station(simulation, network, 3)
    dispatch.dispatch_emergency(network.graph, simulation, 52.62, 1.28)
    older = dispatch.dispatch_emergency(network.graph, simulation, 52.62, 1.28)["emergency"]
    simulation.time += 5
    newer = dispatch.dispatch_emergency(network.graph, simulation, 52.62, 1.28)["emergency"]

    while older.assigned_vehicle is None:
        simulation.step()
        dispatch.after_step(simulation)

    assert newer.assigned_vehicle is None


def test_vehicles_drive_back_to_their_station_after_a_call():
    simulation, network = osm_simulation(river_graph())
    station = add_station(simulation, network, 3)
    emergency = dispatch.dispatch_emergency(network.graph, simulation, 52.62, 1.28)["emergency"]
    vehicle = emergency.assigned_vehicle

    run_until_resolved(simulation, [emergency])
    dispatch.after_step(simulation)

    # On its way home: free to be sent, but not parked yet
    assert vehicle.going_home and vehicle.available
    assert vehicle not in station.available_vehicles
    seconds_home = 0
    while vehicle.going_home:
        simulation.step()
        dispatch.after_step(simulation)
        seconds_home += 1
    assert vehicle in station.available_vehicles
    # 420 m at 20 m/s (exempt from the 30 mph limit)
    assert 20 <= seconds_home <= 23


def test_the_original_rules_still_return_vehicles_instantly():
    simulation = Simulation([{"latitude": 52.62, "longitude": 1.28}] * 2)
    simulation.vehicles[0].finished = True
    graph = river_graph()
    network = RoadNetwork(graph)
    station = add_station(simulation, network, 3)
    emergency = dispatch.dispatch_emergency(graph, simulation, 52.62, 1.28)["emergency"]

    for _ in range(200):
        simulation.step()
        if emergency.resolved:
            break

    assert emergency.resolved
    assert station.has_available_vehicle()


def test_planned_and_driven_times_agree_for_an_emergency_vehicle_in_traffic():
    graph = make_grid_graph(size=6, seed=3)
    for a, roads in graph.edges.items():
        for b, _ in roads:
            graph.road_info[(a, b)] = info(20)
    network = RoadNetwork(graph)
    point = network.point(0)
    simulation = Simulation([point, point], road_model=make_road_model("osm", network))
    simulation.use_road_lengths(network.length_between_points)
    manager = TrafficManager(network, simulation, landmarks=0)
    manager.spawn_vehicles(60, random.Random(2))
    add_station(simulation, network, 0)

    latitude, longitude = network.position[35]
    outcome = dispatch.dispatch_emergency(graph, simulation, latitude, longitude)
    vehicle = outcome["vehicle"]
    estimate = dispatch.remaining_seconds(simulation, vehicle)

    # Right of way and no speed limit for it; traffic still slows it a bit
    assert estimate < network.path_length(outcome["path"]) / (20 * MPH)
    while not outcome["emergency"].resolved:
        manager.step()
    # The estimate used the traffic at dispatch; traffic changes as it
    # drives, so allow a little either way
    assert outcome["emergency"].response_time == pytest.approx(estimate, rel=0.25, abs=3)


# ------------------------------------------------------------
# Where emergencies happen
# ------------------------------------------------------------

def test_population_weights_go_to_the_nearest_usable_junction():
    graph = make_grid_graph(size=4)
    network = RoadNetwork(graph)
    nodes = sorted(graph.nodes)
    corner = network.position[0]
    far = network.position[15]
    areas = [("A", 100, *corner), ("B", 300, *far), ("C", 50, corner[0] + 1e-5, corner[1])]

    weights = junction_weights(network, nodes, areas)

    assert weights == {0: 150, 15: 300}


def test_population_draws_follow_the_weights_and_uniform_draws_are_unchanged():
    graph = make_grid_graph(size=8)
    network = RoadNetwork(graph)
    nodes = sorted(network.main_component())

    uniform = Sampler(network, nodes, "uniform")
    a, b = random.Random(1), random.Random(1)
    assert [uniform.draw(a) for _ in range(20)] == [b.choice(nodes) for _ in range(20)]

    with pytest.raises(ValueError):
        Sampler(network, nodes, "everywhere")


def test_norwich_population_weighting_uses_the_census(tmp_path):
    import pickle
    from pathlib import Path

    from backend.simulation.population import output_areas

    areas = output_areas()
    assert len(areas) == 471
    assert sum(a[1] for a in areas) == 143901

    with open(Path(__file__).resolve().parents[2] / "data" / "osm" / "norwich_graph.pkl", "rb") as file:
        network = RoadNetwork(pickle.load(file))
    nodes = sorted(network.main_component())
    weights = junction_weights(network, nodes)
    # Every resident is placed somewhere usable
    assert sum(weights.values()) == 143901

    sampler = Sampler(network, nodes, "population")
    generator = random.Random(3)
    draws = [sampler.draw(generator) for _ in range(4000)]
    assert set(draws) <= set(weights)
    # The 10% most populated junctions get far more than 10% of draws
    top = set(sorted(weights, key=weights.get, reverse=True)[: len(weights) // 10])
    assert sum(d in top for d in draws) / len(draws) > 0.2


# ------------------------------------------------------------
# Scenarios
# ------------------------------------------------------------

@pytest.fixture(scope="module")
def world():
    return World(make_grid_graph(size=8, seed=7))


def test_existing_specs_keep_their_fingerprint():
    """The same spec, built on main before the services existed, had this
    fingerprint; the new fields at their defaults must not change it."""

    spec = ScenarioSpec(
        name="x", seed=5, demand={"vehicles": 30},
        stations=[{"latitude": 52.62, "longitude": 1.28, "ambulances": 2}],
        emergencies={"count": 3, "first_at": 10, "interval_seconds": 20},
        closures=[{"start": 1, "destination": 2}],
    )
    assert spec.fingerprint() == "6ab2bd58157aa511691d6f57d19155be2f1e16e498b484c3794d9f22a41387c0"
    assert spec.stations[0].speed == 20
    assert ScenarioSpec(stations=[{"latitude": 52.62, "longitude": 1.28, "service": "fire"}]).stations[0].speed == 17


def test_emergencies_need_a_station_of_their_service():
    with pytest.raises(ValueError, match="fire emergencies need a fire station"):
        ScenarioSpec(
            stations=[{"latitude": 52.62, "longitude": 1.28}],
            emergencies=[{"count": 2}, {"count": 1, "service": "fire"}],
        )


def test_a_scenario_with_several_services_and_population_weighting(world):
    spec = ScenarioSpec(
        name="mixed", seed=4, demand={"vehicles": 40}, traffic_model="osm",
        stations=[
            {"latitude": 52.623, "longitude": 1.289, "service": "ambulance", "ambulances": 1},
            {"latitude": 52.628, "longitude": 1.295, "service": "fire"},
            {"latitude": 52.626, "longitude": 1.283, "service": "police"},
        ],
        emergencies=[
            {"count": 4, "first_at": 10, "interval_seconds": 5, "target_seconds": 300},
            {"count": 2, "service": "fire", "first_at": 20, "interval_seconds": 30},
            {"count": 2, "service": "police", "first_at": 15, "interval_seconds": 30,
             "target_seconds": 900},
        ],
    )
    result = run_scenario(world, spec)
    emergencies = result["emergencies"]

    assert emergencies["requested"] == 8
    assert emergencies["dispatched"] == 8
    assert emergencies["arrived"] == 8
    detail = emergencies["detail"]
    # One ambulance for 4 calls 5 s apart: some had to wait
    assert detail["queued"] >= 2 and detail["still_queued"] == 0
    assert detail["mean_wait_seconds"] > 0
    services = {row["service"]: row for row in detail["by_service"]}
    assert set(services) == {"ambulance", "fire", "police"}
    assert services["fire"]["requested"] == 2 and services["police"]["arrived"] == 2

    # Same spec, same result
    again = run_scenario(world, spec)
    assert again["emergencies"] == emergencies


def test_a_one_group_spec_runs_exactly_as_before(world):
    """The emergency plan is drawn from the same stream in the same way."""

    spec = ScenarioSpec(
        name="one", seed=6, demand={"vehicles": 20},
        stations=[{"latitude": 52.623, "longitude": 1.289, "ambulances": 2}],
        emergencies={"count": 3, "first_at": 10, "interval_seconds": 20},
    )
    result = run_scenario(world, spec)
    assert result["emergencies"]["requested"] == 3
    assert result["emergencies"]["detail"]["queued"] == 0


# ------------------------------------------------------------
# Emergencies no vehicle can reach (found in the Stage 5 audit: they were
# queued for ever, with searches every simulated second)
# ------------------------------------------------------------

def island_graph():
    """river_graph plus junction 9, which no road reaches."""

    graph = river_graph()
    graph.add_node(9, 52.6300, 1.3000)
    return graph


def test_an_emergency_no_station_can_reach_is_refused_not_queued():
    simulation, network = osm_simulation(island_graph())
    station = add_station(simulation, network, 3)
    latitude, longitude = network.position[9]

    outcome = dispatch.dispatch_emergency(network.graph, simulation, latitude, longitude)

    assert outcome["status"] == "no_route"
    assert outcome["emergency"].cancel_reason == "no_route"
    assert simulation.queued_emergencies() == []
    # The vehicle was never taken: still free at its station
    assert station.has_available_vehicle()
    assert dispatch.dispatch_emergency(network.graph, simulation, 52.62, 1.28)["status"] == "dispatched"


def test_it_waits_when_only_a_busy_vehicle_could_reach_it():
    """Station B's road to the emergency is closed, so only station A can
    get there; while A's vehicle is out the emergency waits for it."""

    simulation, network = osm_simulation(river_graph())
    add_station(simulation, network, 1)          # A: the long way round
    add_station(simulation, network, 3)          # B: direct road...
    simulation.block_segment(network.segment[(3, 0)])   # ...now closed

    first = dispatch.dispatch_emergency(network.graph, simulation, 52.62, 1.28)
    assert first["status"] == "dispatched"
    assert first["vehicle"].home_station.latitude == network.position[1][0]

    second = dispatch.dispatch_emergency(network.graph, simulation, 52.62, 1.28)
    assert second["status"] == "queued"

    run_until_resolved(simulation, [first["emergency"], second["emergency"]], limit=5000)
    assert second["emergency"].assigned_vehicle.home_station.latitude == network.position[1][0]


def test_a_queued_emergency_cut_off_by_a_closure_is_refused():
    simulation, network = osm_simulation(river_graph())
    add_station(simulation, network, 3)
    first = dispatch.dispatch_emergency(network.graph, simulation, 52.62, 1.28)
    second = dispatch.dispatch_emergency(network.graph, simulation, 52.62, 1.28)
    assert second["status"] == "queued"

    # Every way to the emergency closes while it waits
    simulation.block_segment(network.segment[(3, 0)])
    simulation.block_segment(network.segment[(2, 0)])

    for _ in range(600):
        simulation.step()
        dispatch.after_step(simulation)
        if second["emergency"].cancel_reason is not None:
            break

    assert second["emergency"].cancel_reason == "no_route"
    assert simulation.queued_emergencies() == []
    assert first["emergency"].resolved or first["emergency"].active
