"""The osm traffic model (simulation/road_model.py): speed limits, lane
capacity and per-direction traffic, and that routing, movement, arrival
estimates and delay all agree on what a road costs."""

import heapq
import math
import random

import pytest

from backend.api.settings import traffic_model
from backend.routing.graph import Graph
from backend.routing.road_attributes import MPH, RoadInfo
from backend.routing.travel_time import RoadNetwork
from backend.scenarios.runner import World, run_scenario
from backend.scenarios.spec import ScenarioSpec, load_stored_spec
from backend.simulation import dispatch
from backend.simulation.accidents import Accident
from backend.simulation.road_model import (
    CAR_TOP_SPEED,
    FLOOR_FACTOR,
    MIN_ROAD_LENGTH,
    OsmRoads,
    SIMPLE_ROADS,
    make_road_model,
)
from backend.simulation.simulation import Simulation, speed_factor_on_road
from backend.simulation.traffic_manager import TrafficManager, car_speed
from backend.tests.helpers import make_grid_graph


def road(limit_mph=30, lanes=1, oneway=False, highway="residential", name=None):
    return RoadInfo(
        highway=highway, speed_limit=limit_mph * MPH, speed_limit_tagged=True,
        lanes=lanes, lanes_tagged=True, oneway=oneway, name=name,
    )


def two_way(graph, a, b, length, **info):
    graph.add_edge(a, b, length)
    graph.add_edge(b, a, length)
    graph.road_info[(a, b)] = road(**info)
    graph.road_info[(b, a)] = road(**info)


def choice_graph():
    """0 -> 3 two ways: a short 20 mph street (0-1-3, 600 m) or a longer
    40 mph road (0-2-3, 800 m). By distance the street wins; at the speed
    limits the road does (60 s against 67 s)."""

    graph = Graph()
    for node, (lat, lon) in enumerate([(52.62, 1.28), (52.6227, 1.28), (52.62, 1.2847), (52.6254, 1.28)]):
        graph.add_node(node, lat, lon)

    two_way(graph, 0, 1, 300, limit_mph=20, name="Short Street")
    two_way(graph, 1, 3, 300, limit_mph=20, name="Short Street")
    two_way(graph, 0, 2, 400, limit_mph=40, name="Long Road")
    two_way(graph, 2, 3, 400, limit_mph=40, name="Long Road")
    return graph


def points(network, path):
    return network.points(path)


# ------------------------------------------------------------
# The capacity model
# ------------------------------------------------------------

def osm_model(length=100.0, lanes=1, oneway=False):
    graph = Graph()
    graph.add_node(1, 52.62, 1.28)
    graph.add_node(2, 52.6209, 1.28)
    two_way(graph, 1, 2, length, lanes=lanes)
    network = RoadNetwork(graph)
    return OsmRoads(network), network


def key(network, a, b):
    return (network.position[a], network.position[b])


def test_a_lone_vehicle_is_never_slowed():
    model, network = osm_model(length=5)
    assert model.speed_factor(key(network, 1, 2), 1) == 1.0
    assert model.level(key(network, 1, 2), 1) == "light"
    assert model.level(key(network, 1, 2), 0) == "clear"


def test_speed_falls_as_the_road_fills_and_never_stops():
    model, network = osm_model(length=100)
    road_key = key(network, 1, 2)

    factors = [model.speed_factor(road_key, count) for count in range(1, 40)]

    assert all(a >= b for a, b in zip(factors, factors[1:]))
    assert factors[-1] == FLOOR_FACTOR
    # 100 m of one lane holds 100 / 7.5 = 13.3 cars: 3 others -> 1 - 3/13.3
    assert model.speed_factor(road_key, 4) == pytest.approx(1 - 3 / (100 / 7.5))


def test_a_long_road_absorbs_traffic_that_jams_a_short_one():
    long_model, long_net = osm_model(length=500)
    short_model, short_net = osm_model(length=20)

    # The example from the plan: 3 vehicles on each
    assert long_model.speed_factor(key(long_net, 1, 2), 3) == pytest.approx(1 - 2 / (500 / 7.5))
    assert long_model.speed_factor(key(long_net, 1, 2), 3) > 0.95
    # A 20 m road counts as MIN_ROAD_LENGTH (30 m: 4 cars)
    assert short_model.speed_factor(key(short_net, 1, 2), 3) == pytest.approx(
        1 - 2 / (MIN_ROAD_LENGTH / 7.5)
    )
    assert short_model.speed_factor(key(short_net, 1, 2), 3) == pytest.approx(0.5)


def test_more_lanes_hold_more_traffic():
    one, net_one = osm_model(length=100, lanes=1)
    two, net_two = osm_model(length=100, lanes=2)

    assert two.speed_factor(key(net_two, 1, 2), 8) > one.speed_factor(key(net_one, 1, 2), 8)


def test_an_accident_lowers_capacity_and_speed():
    model, network = osm_model(length=100)
    road_key = key(network, 1, 2)
    accident = Accident(tuple(sorted(road_key)), "severe", 0)

    assert model.speed_factor(road_key, 1, accident) == pytest.approx(accident.speed_factor)
    clear = model.speed_factor(road_key, 4)
    blocked = model.speed_factor(road_key, 4, accident)
    capacity = 100 / 7.5 * accident.capacity_factor
    assert blocked == pytest.approx(max(FLOOR_FACTOR, 1 - 3 / capacity) * accident.speed_factor)
    assert blocked < clear


def test_the_simple_model_keeps_the_original_rules():
    for count in range(0, 6):
        assert SIMPLE_ROADS.speed_factor(None, count) == speed_factor_on_road(count)
    accident = Accident(((0, 0), (1, 1)), "moderate", 0)
    assert SIMPLE_ROADS.speed_factor(None, 3, accident) == speed_factor_on_road(3, accident)


def test_unknown_models_are_rejected():
    with pytest.raises(ValueError):
        make_road_model("fast")
    with pytest.raises(ValueError):
        make_road_model("osm")   # needs the network
    assert traffic_model(None) == "osm"
    assert traffic_model(" Simple ") == "simple"
    with pytest.raises(ValueError, match="TRAFFIC_MODEL"):
        traffic_model("fast")


# ------------------------------------------------------------
# Directions
# ------------------------------------------------------------

def test_vehicles_going_opposite_ways_do_not_slow_each_other():
    graph = Graph()
    graph.add_node(1, 52.62, 1.28)
    graph.add_node(2, 52.6209, 1.28)
    two_way(graph, 1, 2, 100)
    network = RoadNetwork(graph)
    there, back = network.points([1, 2]), network.points([2, 1])

    for name, expected in (("simple", 0.6), ("osm", 1.0)):
        simulation = Simulation(there, road_model=make_road_model(name, network))
        simulation.add_vehicle(back)
        simulation.use_road_lengths(network.length_between_points)
        simulation.step()

        assert [v.speed_factor for v in simulation.vehicles] == [expected, expected], name


def test_the_map_shows_each_road_once_with_its_busier_direction():
    model, network = osm_model(length=30)
    there, back = network.points([1, 2]), network.points([2, 1])
    simulation = Simulation(there, road_model=model)
    for _ in range(3):
        simulation.add_vehicle(there)
    simulation.add_vehicle(back)

    congestion = simulation.get_congestion()

    assert len(congestion) == 1
    (entry,) = congestion.values()
    assert entry["vehicle_count"] == 5
    # 4 one way on a 30 m road (3 others of 4 places): heavy
    assert entry["level"] == "heavy"


# ------------------------------------------------------------
# Speed limits: movement and routing agree
# ------------------------------------------------------------

def test_cars_keep_to_the_limit_and_emergency_vehicles_are_exempt():
    graph = choice_graph()
    network = RoadNetwork(graph)
    route = network.points([0, 1])   # 300 m at 20 mph
    simulation = Simulation(route, road_model=make_road_model("osm", network))
    simulation.use_road_lengths(network.length_between_points)
    car = simulation.vehicles[0]
    car.speed = car_speed(simulation)
    station = simulation.add_station(52.62, 1.28)
    # On the 40 mph road (17.9 m/s), alone: it drives at its own 20 m/s
    ambulance = simulation.add_emergency_vehicle(network.points([0, 2]), station, speed=20)

    simulation.step()

    assert car_speed(simulation) == CAR_TOP_SPEED
    assert car.distance_travelled == pytest.approx(20 * MPH)
    assert ambulance.distance_travelled == pytest.approx(20)


def test_the_router_prefers_the_faster_road_when_limits_apply():
    network = RoadNetwork(choice_graph())

    path, seconds, _ = network.fastest_path(0, 3, CAR_TOP_SPEED, limits=True)
    assert path == [0, 2, 3]
    assert seconds == pytest.approx(800 / (40 * MPH))

    # Without limits (the simple model) the shorter street is faster
    assert network.fastest_path(0, 3, 13.9)[0] == [0, 1, 3]


def reference_seconds(network, start, destination, speed, factor):
    """Plain Dijkstra on the same costs (no estimate, no shortcuts)."""

    best = {start: 0.0}
    queue = [(0.0, start)]
    while queue:
        seconds, node = heapq.heappop(queue)
        if node == destination:
            return seconds
        if seconds > best[node]:
            continue
        for (a, b), length in network.length.items():
            if a != node:
                continue
            road_speed = min(speed, network.limit[(a, b)])
            cost = seconds + length / (road_speed * factor((network.position[a], network.position[b])))
            if cost < best.get(b, math.inf):
                best[b] = cost
                heapq.heappush(queue, (cost, b))
    return math.inf


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_a_star_with_limits_finds_the_true_fastest_route(seed):
    """Random limits and random traffic, per direction: A* (both estimates)
    agrees with plain Dijkstra, so the estimate never overestimates."""

    generator = random.Random(seed)
    graph = make_grid_graph(size=6, seed=seed)
    for a, roads in graph.edges.items():
        for b, _ in roads:
            graph.road_info[(a, b)] = road(limit_mph=generator.choice([10, 20, 30, 40, 50]))
    network = RoadNetwork(graph)
    network.use_landmarks(4)
    factors = {
        (network.position[a], network.position[b]): generator.uniform(0.1, 1.0)
        for a, b in network.length
    }
    nodes = sorted(graph.nodes)

    for _ in range(25):
        start, destination = generator.sample(nodes, 2)
        expected = reference_seconds(network, start, destination, CAR_TOP_SPEED, factors.get)
        for heuristic in ("straight_line", "landmarks"):
            path, seconds, _ = network.fastest_path(
                start, destination, CAR_TOP_SPEED, factors.get,
                heuristic=heuristic, directed=True, limits=True,
            )
            assert seconds == pytest.approx(expected), (start, destination, heuristic)
            assert network.path_seconds(
                path, CAR_TOP_SPEED, factors.get, directed=True, limits=True
            ) == pytest.approx(seconds)


def test_planned_and_driven_times_agree_on_limited_roads():
    graph = make_grid_graph(size=8, seed=11)
    generator = random.Random(5)
    for a, roads in graph.edges.items():
        for b, _ in roads:
            graph.road_info[(a, b)] = road(limit_mph=generator.choice([20, 30, 40]))
    network = RoadNetwork(graph)
    nodes = sorted(graph.nodes)

    for _ in range(20):
        start, destination = generator.sample(nodes, 2)
        path, planned, _ = network.fastest_path(start, destination, CAR_TOP_SPEED, limits=True)
        simulation = Simulation(network.points(path), road_model=make_road_model("osm", network))
        simulation.use_road_lengths(network.length_between_points)
        vehicle = simulation.vehicles[0]
        vehicle.speed = CAR_TOP_SPEED

        estimate = dispatch.remaining_seconds(simulation, vehicle)
        while not vehicle.finished:
            simulation.step()

        assert estimate == pytest.approx(planned)
        # The journey clock counts whole seconds
        assert planned <= vehicle.journey_seconds < planned + 1


def test_the_traffic_manager_plans_with_limits_in_the_osm_model():
    graph = choice_graph()
    network = RoadNetwork(graph)

    for name, expected in (("simple", [0, 1, 3]), ("osm", [0, 2, 3])):
        simulation = Simulation(network.points([0, 1]), road_model=make_road_model(name, network))
        manager = TrafficManager(network, simulation, landmarks=0)
        path, _, _ = manager.plan(0, 3, car_speed(simulation))
        assert path == expected, name


# ------------------------------------------------------------
# Scenarios
# ------------------------------------------------------------

@pytest.fixture(scope="module")
def world():
    return World(make_grid_graph(size=8, seed=7))


def test_a_lone_vehicle_loses_no_time_in_the_osm_model(world):
    result = run_scenario(world, ScenarioSpec(
        name="alone", seed=4, demand={"vehicles": 1}, traffic_model="osm",
    ))
    summary = result["summary"]

    assert summary["trips_finished"] == 1
    assert summary["total_delay_vehicle_hours"] == 0
    assert 0 <= summary["mean_increase_seconds"] < 1
    assert result["roads"] == []


def test_road_delay_adds_up_to_trip_delay_in_the_osm_model(world):
    """Every second of delay is counted on exactly one road (as in the
    simple model, test_scenarios.py)."""

    from backend.scenarios.impact import ImpactTracker

    captured = {}
    original = ImpactTracker.finish

    def keep(self):
        original(self)
        captured["roads"] = self.total_road_delay()

    ImpactTracker.finish = keep
    try:
        result = run_scenario(world, ScenarioSpec(
            name="busy", seed=4, demand={"vehicles": 150}, traffic_model="osm",
            accidents=[{"start": 9, "destination": 10, "severity": "severe"}],
        ))
    finally:
        ImpactTracker.finish = original

    trip_delay = sum(d for d in result["trips"]["delay_seconds"] if d is not None)
    assert trip_delay > 0
    assert captured["roads"] == pytest.approx(trip_delay)


def test_the_same_osm_spec_gives_the_same_result(world):
    spec = ScenarioSpec(name="again", seed=9, demand={"vehicles": 80}, traffic_model="osm")
    first, second = run_scenario(world, spec), run_scenario(world, spec)
    strip = lambda r: {k: v for k, v in r.items() if k != "execution"}

    assert strip(first) == strip(second)


def test_old_specs_keep_the_simple_model_and_their_fingerprint():
    old = {"name": "old", "seed": 3, "demand": {"vehicles": 10}}

    loaded = load_stored_spec(old)
    assert loaded.traffic_model is None
    assert loaded.effective_traffic_model == "simple"
    assert loaded.fingerprint() == ScenarioSpec(**old, traffic_model="simple").fingerprint()
    assert loaded.fingerprint() != ScenarioSpec(**old, traffic_model="osm").fingerprint()
    # The model does not change which trips are made
    assert loaded.trip_signature() == ScenarioSpec(**old, traffic_model="osm").trip_signature()


def test_the_travel_time_bound_never_exceeds_the_fastest_time():
    """lower_bound_seconds lets the traffic manager skip searches that could
    not save enough; it must never be above the true fastest time, with or
    without limits, under any traffic."""

    generator = random.Random(9)
    graph = make_grid_graph(size=7, seed=9)
    for a, roads in graph.edges.items():
        for b, _ in roads:
            graph.road_info[(a, b)] = road(limit_mph=generator.choice([10, 20, 30, 40]))
    network = RoadNetwork(graph)
    network.use_landmarks(4)
    traffic = {
        (network.position[a], network.position[b]): generator.uniform(0.2, 1.0)
        for a, b in network.length
    }
    nodes = sorted(graph.nodes)
    tighter = 0

    for _ in range(60):
        start, destination = generator.sample(nodes, 2)
        for speed, limits in ((CAR_TOP_SPEED, True), (13.9, True), (20.0, False)):
            fastest = network.fastest_path(
                start, destination, speed, traffic.get, directed=True, limits=limits
            )[1]
            bound = network.lower_bound_seconds(start, destination, speed, limits)
            assert bound <= fastest + 1e-9, (start, destination, speed, limits)
        distance_only = network.lower_bound_metres(start, destination) / network.top_speed(CAR_TOP_SPEED, True)
        tighter += network.lower_bound_seconds(start, destination, CAR_TOP_SPEED, True) > distance_only + 1e-9

    # The travel-time landmarks do make it tighter
    assert tighter > 30
