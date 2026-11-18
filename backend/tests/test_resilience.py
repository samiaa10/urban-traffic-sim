"""Disruptions and resilience: floods and their recovery, overlapping
incidents, rerouting around them, unreachable places, emergency dispatch
during a disruption, bridge failures, and the resilience measures."""

import pickle

import pytest

from backend.routing.travel_time import RoadNetwork
from backend.scenarios.runner import World, run_scenario
from backend.scenarios.spec import ScenarioSpec, validate_against_graph
from backend.simulation import dispatch
from backend.simulation.disruptions import FLOOD_SEVERITIES, REOPENED, combine, segments_in_area
from backend.simulation.road_model import make_road_model
from backend.simulation.simulation import Simulation
from backend.simulation.traffic_manager import TrafficManager
from backend.tests.helpers import coords, make_diamond_graph, make_grid_graph


def simulation_on(graph, model="osm"):
    network = RoadNetwork(graph)
    first = next(iter(graph.nodes))
    simulation = Simulation([network.point(first)] * 2, road_model=make_road_model(model, network, seed=1))
    simulation.vehicles[0].finished = True
    simulation.use_road_lengths(network.length_between_points)
    return network, simulation


def road(network, a, b):
    return tuple(sorted((network.position[a], network.position[b])))


def run_for(simulation, seconds):
    for _ in range(seconds):
        simulation.step()


# ------------------------------------------------------------
# Floods
# ------------------------------------------------------------

def test_a_partial_flood_slows_its_roads_then_recovers_gradually():
    network, simulation = simulation_on(make_grid_graph(3))
    segment = road(network, 0, 1)
    simulation.add_flood([segment], "moderate", duration=100, recovery_seconds=100)
    base = FLOOD_SEVERITIES["moderate"]["speed_factor"]

    run_for(simulation, 50)
    assert simulation.road_incident(segment).speed_factor == pytest.approx(base)
    assert segment not in simulation.get_blocked_segments()

    run_for(simulation, 100)   # half way through recovery (t = 150)
    assert simulation.road_incident(segment).speed_factor == pytest.approx(base + (1 - base) / 2)

    run_for(simulation, 60)    # past t = 200: over
    assert simulation.road_incident(segment) is None
    assert simulation.floods == []
    kinds = [e["type"] for e in simulation.events]
    assert kinds == ["flood", "flood_cleared"]


def test_a_closing_flood_closes_reopens_slowly_and_recovers():
    network, simulation = simulation_on(make_grid_graph(3))
    segment = road(network, 0, 1)
    simulation.add_flood([segment], "closed", duration=60, recovery_seconds=60)

    run_for(simulation, 30)
    assert segment in simulation.get_blocked_segments()
    run_for(simulation, 31)   # t = 61: receding
    assert segment not in simulation.get_blocked_segments()
    effect = simulation.road_incident(segment)
    assert REOPENED["speed_factor"] <= effect.speed_factor < 1
    run_for(simulation, 70)
    assert simulation.road_incident(segment) is None
    assert [e["type"] for e in simulation.events] == ["road_closed", "road_reopened", "flood_cleared"]


def test_a_flood_area_takes_every_road_inside_it():
    graph = make_grid_graph(5)
    network = RoadNetwork(graph)
    centre = graph.nodes[12]
    roads = segments_in_area(network, centre["latitude"], centre["longitude"], 50)
    assert roads == []   # no road has both ends within 50 m
    roads = segments_in_area(network, centre["latitude"], centre["longitude"], 400)
    inside = {n for n in graph.nodes if road(network, 12, n) in roads or road(network, n, 12) in roads}
    assert {7, 11, 13, 17} <= inside


# ------------------------------------------------------------
# Overlapping incidents
# ------------------------------------------------------------

def test_a_road_closed_twice_reopens_only_when_both_are_over():
    network, simulation = simulation_on(make_grid_graph(3))
    segment = road(network, 0, 1)
    simulation.block_segment(segment)                      # manual
    simulation.add_flood([segment], "closed", duration=30, recovery_seconds=0)

    run_for(simulation, 40)   # the flood is over; the manual closure is not
    assert segment in simulation.get_blocked_segments()
    assert simulation.closed_by(segment) == {"manual"}

    simulation.unblock_segment(segment)
    assert segment not in simulation.get_blocked_segments()
    assert [e["type"] for e in simulation.events].count("road_closed") == 1
    assert [e["type"] for e in simulation.events].count("road_reopened") == 1


def test_reopening_by_hand_does_not_reopen_a_flooded_road():
    network, simulation = simulation_on(make_grid_graph(3))
    segment = road(network, 0, 1)
    simulation.add_flood([segment], "closed", duration=100, recovery_seconds=0)
    simulation.unblock_segment(segment)   # nobody closed it by hand
    assert segment in simulation.get_blocked_segments()


def test_an_accident_and_floods_on_one_road_combine():
    network, simulation = simulation_on(make_grid_graph(3))
    segment = road(network, 0, 1)
    accident = simulation.add_accident(segment, "minor", 600)
    simulation.add_flood([segment], "minor", duration=100, recovery_seconds=0)
    simulation.add_flood([segment], "moderate", duration=50, recovery_seconds=0)
    minor, moderate = FLOOD_SEVERITIES["minor"], FLOOD_SEVERITIES["moderate"]

    effect = simulation.road_incident(segment)
    assert effect.speed_factor == pytest.approx(accident.speed_factor * minor["speed_factor"] * moderate["speed_factor"])
    assert effect.capacity_factor == pytest.approx(
        accident.capacity_factor * minor["capacity_factor"] * moderate["capacity_factor"]
    )

    run_for(simulation, 60)   # the moderate flood is over; the rest remain
    assert simulation.road_incident(segment).speed_factor == pytest.approx(
        accident.speed_factor * minor["speed_factor"]
    )
    assert combine(None, None) is None


@pytest.mark.parametrize("model", ["simple", "osm", "ca"])
def test_every_traffic_model_feels_a_flood(model):
    network, simulation = simulation_on(make_grid_graph(3), model)
    segment = road(network, 0, 1)
    key = simulation.traffic_key(network.point(0), network.point(1))
    before = simulation.road_speed_factor(key, 1)
    simulation.add_flood([segment], "severe", duration=100, recovery_seconds=0)
    assert simulation.road_speed_factor(key, 1) < before


# ------------------------------------------------------------
# Rerouting, unreachable places and emergencies
# ------------------------------------------------------------

def test_vehicles_reroute_around_a_flooded_road():
    graph = make_diamond_graph()
    network, simulation = simulation_on(graph)
    traffic = TrafficManager(network, simulation)
    vehicle = simulation.add_vehicle(coords(graph, ["A", "B", "D"]), speed=13.9)
    simulation.add_flood([road(network, "B", "D")], "severe", duration=1800, recovery_seconds=0)
    traffic.step()
    points = [(p["latitude"], p["longitude"]) for p in vehicle.route]
    assert network.position["C"] in points


def test_a_closed_off_place_cannot_be_reached_and_trips_to_it_are_unroutable():
    world = World(make_grid_graph(5))
    corner = world.graph.nodes[0]
    base = {
        "name": "cut off", "seed": 1, "demand": {"vehicles": 150, "spread_seconds": 120},
        "traffic_model": "osm", "resilience": True,
    }
    flood = {"area": {"latitude": corner["latitude"], "longitude": corner["longitude"], "radius_metres": 400},
             "severity": "closed", "start_at": 0, "duration_seconds": 3000}
    normal = run_scenario(world, base)
    flooded = run_scenario(world, {**base, "floods": [flood]})

    assert normal["summary"]["trips_unroutable"] == 0
    assert flooded["summary"]["trips_unroutable"] > 0
    during = flooded["resilience"]["accessibility"]["during"]
    assert during["reachable_percent"] < 100 and during["unreachable_pairs"] > 0
    assert during["areas"] and during["areas"][0]["cut_off_pairs"] > 0
    assert normal["resilience"]["accessibility"]["during"]["reachable_percent"] == 100


def test_an_emergency_during_a_flood_is_reached_around_it():
    graph = make_diamond_graph()
    network, simulation = simulation_on(graph)
    station = simulation.add_station(*network.position["A"])
    simulation.add_emergency_vehicle(coords(graph, ["A", "A"]), station)
    simulation.add_flood([road(network, "B", "D")], "closed", duration=1800, recovery_seconds=0)

    outcome = dispatch.dispatch_emergency(graph, simulation, *network.position["D"], "ambulance")
    assert outcome["status"] == "dispatched"
    vehicle = outcome["emergency"].assigned_vehicle
    points = [(p["latitude"], p["longitude"]) for p in vehicle.route]
    assert network.position["C"] in points and network.position["B"] not in points


# ------------------------------------------------------------
# Scenarios
# ------------------------------------------------------------

def test_overlapping_scenario_closures_end_independently():
    world = World(make_grid_graph(4))
    spec = {
        "name": "two", "seed": 1, "demand": {"vehicles": 60, "spread_seconds": 600},
        "closures": [{"start": 0, "destination": 1, "end_at": 60}, {"start": 0, "destination": 1}],
        "max_seconds": 300,
    }
    result = run_scenario(world, spec)
    # The second closure never ends: the road was closed all along
    assert result["timeseries"][-1]["closed_roads"] == 1


def test_specs_without_disruptions_keep_their_fingerprint():
    plain = {"name": "a", "seed": 1, "demand": {"vehicles": 10}}
    assert ScenarioSpec(**plain).fingerprint() == ScenarioSpec(**plain, floods=[], failures=[], resilience=False).fingerprint()


def test_floods_need_roads_on_the_map_or_an_area():
    with pytest.raises(ValueError):
        ScenarioSpec(name="a", seed=1, floods=[{"severity": "closed"}])
    spec = ScenarioSpec(name="a", seed=1, floods=[{"roads": [{"start": 0, "destination": 99}]}])
    assert validate_against_graph(spec, make_grid_graph(3))


def test_a_resilience_scenario_is_repeatable():
    world = World(make_grid_graph(5))
    spec = {
        "name": "r", "seed": 4, "demand": {"vehicles": 120, "spread_seconds": 300}, "traffic_model": "ca",
        "resilience": True,
        "stations": [{"latitude": world.graph.nodes[12]["latitude"], "longitude": world.graph.nodes[12]["longitude"]}],
        "emergencies": {"count": 4, "first_at": 30, "interval_seconds": 40},
        "floods": [{"roads": [{"start": 6, "destination": 7}, {"start": 7, "destination": 8}],
                    "severity": "severe", "start_at": 20, "duration_seconds": 120, "recovery_seconds": 60}],
        "accidents": [{"start": 7, "destination": 12, "severity": "moderate", "start_at": 10}],
    }
    a, b = run_scenario(world, spec), run_scenario(world, spec)
    assert a["trips"]["journey_seconds"] == b["trips"]["journey_seconds"]
    assert a["resilience"]["accessibility"] == b["resilience"]["accessibility"]
    emergency = a["resilience"]["emergency_access"]["during"]["ambulance"]
    assert 0 <= emergency["within_target_percent"] <= 100


@pytest.fixture(scope="module")
def norwich():
    from backend.simulation.signal_sites import GRAPH_FILE

    with open(GRAPH_FILE, "rb") as file:
        return World(pickle.load(file))


def test_a_bridge_failure_closes_the_named_bridges_and_restores_them(norwich):
    from backend.scenarios.runner import failed_roads
    from backend.scenarios.spec import Failure

    roads = failed_roads(norwich, Failure(names=["Whitefriars", "Fye Bridge Street"]))
    assert len(roads) == 2
    spec = {
        "name": "bridges", "seed": 1, "demand": {"vehicles": 40, "spread_seconds": 120},
        "failures": [{"names": ["Whitefriars", "Fye Bridge Street"], "start_at": 0, "end_at": 120}],
        "max_seconds": 240,
    }
    result = run_scenario(norwich, spec)
    closed = [row["closed_roads"] for row in result["timeseries"]]
    assert closed[0] == 2 and closed[-1] == 0
