"""The cellular-automaton traffic model (simulation/cellular.py): each
Nagel-Schreckenberg rule, the textbook behaviour on a ring road, and the
model on a road network (junctions, road ends, closures, accidents, lanes,
no collisions, repeatable with a seed, and the speeds it feeds back to
routing)."""

import pytest

from backend.api.settings import traffic_model
from backend.routing.graph import Graph
from backend.routing.road_attributes import MPH, RoadInfo
from backend.routing.travel_time import RoadNetwork
from backend.scenarios.runner import World, run_scenario
from backend.scenarios.spec import ScenarioSpec
from backend.simulation.cellular import (
    CaParameters,
    CaRoads,
    OpenRoad,
    RingRoad,
    accelerate,
    brake,
    nasch_speed,
    randomise,
)
from backend.simulation.road_model import (
    CAR_TOP_SPEED,
    FLOOR_FACTOR,
    TRAFFIC_MODELS,
    make_road_model,
)
from backend.simulation.services import REALISTIC_RULES, rules_for
from backend.simulation.simulation import Simulation
from backend.simulation.traffic_manager import TrafficManager
from backend.tests.helpers import make_grid_graph


# ------------------------------------------------------------
# The four rules
# ------------------------------------------------------------

def test_acceleration_adds_one_cell_per_step_up_to_vmax():
    assert accelerate(0, 5) == 1
    assert accelerate(4, 5) == 5
    assert accelerate(5, 5) == 5


def test_braking_never_moves_further_than_the_gap():
    assert brake(5, 2) == 2
    assert brake(2, 5) == 2
    assert brake(3, 0) == 0
    # A negative gap (should not happen) still means standing still
    assert brake(3, -1) == 0


def test_randomisation_slows_by_one_cell_and_never_below_zero():
    assert randomise(3, True) == 2
    assert randomise(3, False) == 3
    assert randomise(0, True) == 0


def test_the_rules_apply_in_order():
    # accelerate 2 -> 3, the gap allows 3, then the random draw takes one off
    assert nasch_speed(2, 5, 3, True) == 2
    # braking after accelerating: 4 -> 5, the gap allows only 1
    assert nasch_speed(4, 5, 1, False) == 1
    # a car stuck behind another stays stuck, slow draw or not
    assert nasch_speed(0, 5, 0, True) == 0


@pytest.mark.parametrize("bad", [
    {"cell_metres": 0}, {"vehicle_cells": 0}, {"brake_probability": 1.5},
    {"time_step": 0.3}, {"time_step": 0},
])
def test_parameters_are_checked(bad):
    with pytest.raises(ValueError):
        CaParameters(**bad)


def test_speeds_become_whole_cells_per_step():
    p = CaParameters()
    assert p.cells_per_step(30 * MPH) == 5     # 13.4 m/s -> 12.5 m/s
    assert p.cells_per_step(20 * MPH) == 4
    assert p.cells_per_step(0.1) == 1          # never 0: a car can always move
    assert CaParameters(time_step=0.5).cells_per_step(30 * MPH) == 3
    assert CaParameters(time_step=0.5).steps_per_second == 2


# ------------------------------------------------------------
# A ring road: the textbook results
# ------------------------------------------------------------

def run_ring(cells, cars, vmax=5, p=0.0, seed=0, warm=200, measure=500):
    ring = RingRoad(cells, cars, vmax, p, seed)
    for _ in range(warm):
        ring.step()
    passed = ring.passed
    speeds = 0
    for _ in range(measure):
        ring.step()
        speeds += sum(ring.speed)
    return ring, (ring.passed - passed) / measure, speeds / (measure * cars)


def test_ring_cars_never_share_a_cell_or_overtake():
    ring = RingRoad(200, 70, 5, 0.3, seed=4)
    order = list(range(70))
    for _ in range(300):
        ring.step()
        assert len(set(ring.position)) == 70
        # each car's leader is still the next one round the ring
        assert all(gap >= 0 for gap in ring.gaps())
        assert sorted(order, key=lambda i: ring.position[i]) == sorted(
            order, key=lambda i: ring.position[i]
        )


def test_without_randomness_light_traffic_flows_at_vmax():
    # Density 0.1 < 1 / (vmax + 1): every car reaches vmax and stays there
    _, flow, speed = run_ring(200, 20)
    assert speed == 5
    assert flow == pytest.approx(0.1 * 5, abs=0.01)


def test_without_randomness_dense_traffic_flows_at_one_minus_density():
    # The deterministic model's jammed branch: flow = 1 - density
    _, flow, _ = run_ring(200, 100)
    assert flow == pytest.approx(0.5, abs=0.01)


def test_random_braking_costs_about_p_cells_per_step_in_free_flow():
    # A lone car's mean speed is vmax - p
    _, _, speed = run_ring(1000, 1, p=0.25, seed=2, measure=4000)
    assert speed == pytest.approx(5 - 0.25, abs=0.05)


def test_random_braking_lowers_the_maximum_flow():
    best_without = max(run_ring(300, n)[1] for n in (40, 50, 60))
    best_with = max(run_ring(300, n, p=0.5, seed=1)[1] for n in (30, 40, 50, 60))
    assert best_with < best_without - 0.1


def test_random_braking_makes_jams_appear_on_their_own():
    # Density 0.15 flows freely without randomness, but with it cars stop
    ring = RingRoad(400, 60, 5, 0.5, seed=3)
    stopped = 0
    for _ in range(400):
        ring.step()
        stopped += sum(1 for v in ring.speed if v == 0)
    assert stopped > 0
    calm, _, _ = run_ring(400, 60)
    assert all(v == 5 for v in calm.speed)


def test_a_seed_makes_a_ring_repeatable():
    a = run_ring(200, 50, p=0.3, seed=7)[0]
    b = run_ring(200, 50, p=0.3, seed=7)[0]
    c = run_ring(200, 50, p=0.3, seed=8)[0]
    assert a.position == b.position and a.speed == b.speed
    assert a.position != c.position


def test_a_queue_builds_behind_a_blockage_and_clears_after_it():
    road = OpenRoad(300, inflow=0.3, vmax=5, brake_probability=0.2, seed=1)
    for _ in range(200):
        road.step()
    road.blocked_at = 250
    for _ in range(150):
        road.step()
    blocked_queue = road.queue()
    assert blocked_queue > 20
    road.blocked_at = None
    for _ in range(300):
        road.step()
    assert road.queue() < blocked_queue / 4


# ------------------------------------------------------------
# On a road network
# ------------------------------------------------------------

def info(limit_mph=30, lanes=1):
    return RoadInfo(
        highway="residential", speed_limit=limit_mph * MPH, speed_limit_tagged=True,
        lanes=lanes, lanes_tagged=True, oneway=True, name=None,
    )


def line_graph(lengths=(100, 100, 100), limit_mph=30, lanes=1):
    """Junctions 0, 1, 2, ... due north of each other, one-way roads of
    the given lengths between them."""
    graph = Graph()
    latitude = 52.62
    graph.add_node(0, latitude, 1.28)
    for node, length in enumerate(lengths, start=1):
        latitude += length / 111_320
        graph.add_node(node, latitude, 1.28)
        graph.add_edge(node - 1, node, length)
        graph.road_info[(node - 1, node)] = info(limit_mph, lanes)
    return graph


def setup(graph, p=0.0, seed=0, **parameters):
    network = RoadNetwork(graph)
    model = CaRoads(network.osm_roads(), CaParameters(brake_probability=p, **parameters), seed)
    parked = network.point(next(iter(graph.nodes)))
    simulation = Simulation([parked, parked], road_model=model)
    simulation.vehicles[0].finished = True
    simulation.use_road_lengths(network.length_between_points)
    return network, model, simulation


def add_car(simulation, network, path):
    return simulation.add_vehicle(network.points(path), speed=CAR_TOP_SPEED)


def car_of(model, vehicle):
    return model.engine.cars[id(vehicle)]


def assert_no_collisions(model):
    size = model.parameters.vehicle_cells
    for road in model.engine.roads.values():
        for lane in road.lanes:
            for ahead, behind in zip(lane, lane[1:]):
                assert ahead.front - size >= behind.front
            for car in lane:
                assert car.road is road and car.lane is lane
                assert 0 <= car.front < road.cells


def test_a_lone_car_accelerates_to_the_limit_and_crosses_junctions():
    network, model, simulation = setup(line_graph())
    vehicle = add_car(simulation, network, [0, 1, 2, 3])
    fronts = []

    while not vehicle.finished:
        simulation.step()
        if not vehicle.finished:
            car = car_of(model, vehicle)
            fronts.append((car.index, car.front, car.v))

    # 1, 2, 3, 4, then 5 cells a second (30 mph at 2.5 m cells)
    assert [v for _, _, v in fronts[:6]] == [1, 2, 3, 4, 5, 5]
    # It moved onto road 1, then road 2, in order, keeping its route index
    assert [index for index, _, _ in fronts] == sorted(index for index, _, _ in fronts)
    assert {index for index, _, _ in fronts} == {0, 1, 2}
    # 119 cells from the first cell to the last: 15 in the first 5 s, then
    # 5 a second
    assert vehicle.journey_seconds == 26
    assert vehicle.distance_travelled == pytest.approx(119 * 2.5)
    # It arrived during the last second, not at its end
    assert 0 < vehicle.last_move_seconds < 1


def test_the_vehicle_follows_the_lattice():
    network, model, simulation = setup(line_graph())
    vehicle = add_car(simulation, network, [0, 1, 2, 3])
    for _ in range(12):
        simulation.step()
    car = car_of(model, vehicle)
    assert vehicle.position == car.index
    assert vehicle.distance_on_segment == pytest.approx((car.front + 1) * 2.5)
    location = vehicle.get_current_location()
    start = network.point(car.index)
    assert location["latitude"] > start["latitude"]


def test_cars_queue_without_colliding_and_all_arrive():
    network, model, simulation = setup(line_graph((60, 40, 200)), p=0.3, seed=2)
    cars = [add_car(simulation, network, [0, 1, 2, 3]) for _ in range(25)]

    for _ in range(400):
        simulation.step()
        assert_no_collisions(model)
        if all(car.finished for car in cars):
            break

    assert all(car.finished for car in cars)
    # They left one at a time: no two arrived in the same instant
    assert len({(car.journey_seconds, car.last_move_seconds) for car in cars}) == 25


def test_a_car_waits_to_enter_a_full_road():
    network, model, simulation = setup(line_graph((40,)))
    first = add_car(simulation, network, [0, 1])
    second = add_car(simulation, network, [0, 1])
    simulation.step()
    # The first car moved one cell; the second would overlap it
    assert car_of(model, first).front == 1
    assert car_of(model, second).road is None
    assert second.distance_travelled == 0
    # The clock runs while it waits
    assert second.journey_seconds == 1


def test_cars_take_free_lanes_side_by_side():
    network, model, simulation = setup(line_graph((100,), lanes=2))
    a = add_car(simulation, network, [0, 1])
    b = add_car(simulation, network, [0, 1])
    simulation.step()
    assert car_of(model, a).lane is not car_of(model, b).lane


def test_a_closed_road_ahead_stops_a_car_at_the_junction():
    network, model, simulation = setup(line_graph())
    vehicle = add_car(simulation, network, [0, 1, 2, 3])
    simulation.block_segment((network.position[1], network.position[2]))

    for _ in range(40):
        simulation.step()
        assert_no_collisions(model)

    car = car_of(model, vehicle)
    assert (car.index, car.front, car.v) == (0, car.road.cells - 1, 0)
    assert not vehicle.finished

    simulation.unblock_segment((network.position[1], network.position[2]))
    for _ in range(60):
        simulation.step()
    assert vehicle.finished


def test_a_car_on_a_closed_road_does_not_move():
    network, model, simulation = setup(line_graph())
    vehicle = add_car(simulation, network, [0, 1, 2, 3])
    for _ in range(4):
        simulation.step()
    front = car_of(model, vehicle).front
    simulation.block_segment((network.position[0], network.position[1]))
    for _ in range(5):
        simulation.step()
    assert car_of(model, vehicle).front == front
    assert vehicle.speed_factor == 0


def test_an_accident_lowers_the_speed_on_its_road():
    def journey(with_accident):
        network, _, simulation = setup(line_graph((200,)))
        vehicle = add_car(simulation, network, [0, 1])
        if with_accident:
            simulation.add_accident((network.position[0], network.position[1]), "moderate")
        while not vehicle.finished:
            simulation.step()
        return vehicle.journey_seconds

    # 79 cells. Free: 15 in the first 5 s, then 5 a second. Moderate
    # accident (speed factor 0.45): vmax 5 -> 2 cells a second.
    assert journey(False) == 18
    assert journey(True) == 40


def test_a_reroute_from_mid_road_keeps_the_car_where_it_is():
    network, model, simulation = setup(line_graph())
    vehicle = add_car(simulation, network, [0, 1, 2, 3])
    for _ in range(4):
        simulation.step()
    car = car_of(model, vehicle)
    road, front = car.road, car.front

    # What rerouting.py does: [where it is now, the next junction, ...]
    vehicle.set_route([vehicle.get_current_location()] + network.points([1, 2, 3]))
    simulation.step()

    assert car.road is road and car.front > front
    assert vehicle.position == 0


def test_emergency_vehicles_are_not_on_the_lattice_but_still_drive():
    network, model, simulation = setup(line_graph())
    ambulance = simulation.add_vehicle(network.points([0, 1, 2, 3]), speed=20, vehicle_type="ambulance")
    for _ in range(5):
        simulation.step()
    assert id(ambulance) not in model.engine.cars
    assert ambulance.distance_travelled > 0


def test_a_seed_makes_a_network_run_repeatable():
    def journeys(seed):
        graph = make_grid_graph(6)
        network, model, simulation = setup(graph, p=0.25, seed=seed)
        manager = TrafficManager(network, simulation, seed=1)
        manager.spawn_vehicles(150)
        while not all(v.finished for v in simulation.vehicles) and simulation.time < 2000:
            manager.step()
            assert_no_collisions(model)
        return [v.journey_seconds for v in simulation.vehicles]

    assert journeys(5) == journeys(5)
    assert journeys(5) != journeys(6)


def test_routing_reads_the_speeds_the_ca_measured():
    network, model, simulation = setup(line_graph((100, 100)))
    key = (network.position[0], network.position[1])
    # Nothing measured on an empty road: the osm estimate (free flow)
    assert simulation.road_speed_factor(key, 0) == 1.0

    simulation.block_segment((network.position[1], network.position[2]))
    for _ in range(12):
        add_car(simulation, network, [0, 1, 2])
    for _ in range(80):
        simulation.step()

    measured = model.engine.measured[key]
    assert measured < 0.2
    assert simulation.road_speed_factor(key, 12) == max(FLOOR_FACTOR, measured)
    state = model.road_state()[key]
    assert state["vehicles"] == 12 and state["stopped"] == 12
    assert simulation.get_congestion()[tuple(sorted(key))]["level"] == "heavy"


def test_several_updates_per_second_with_a_shorter_time_step():
    network, model, simulation = setup(line_graph(), time_step=0.5)
    vehicle = add_car(simulation, network, [0, 1, 2, 3])
    simulation.step()
    # two updates: speeds 1 then 2 (cells per half second)
    assert car_of(model, vehicle).front == 3


# ------------------------------------------------------------
# A selectable traffic model
# ------------------------------------------------------------

def test_ca_is_a_traffic_model_like_the_others():
    assert "ca" in TRAFFIC_MODELS
    assert traffic_model("ca") == "ca"
    network = RoadNetwork(line_graph())
    a, b = make_road_model("ca", network), make_road_model("ca", network)
    # One per simulation: each holds its own cars
    assert a is not b and a.osm is b.osm is network.osm_roads()
    assert rules_for(a) is REALISTIC_RULES
    with pytest.raises(ValueError):
        make_road_model("ca")


def test_scenarios_run_the_ca_model_repeatably():
    world = World(make_grid_graph(6))
    spec = {"name": "ca", "seed": 3, "demand": {"vehicles": 80, "spread_seconds": 60}, "traffic_model": "ca"}

    first, second = run_scenario(world, spec), run_scenario(world, spec)
    osm = run_scenario(world, {**spec, "traffic_model": "osm"})

    assert first["summary"]["trips_finished"] == 80
    assert first["trips"]["journey_seconds"] == second["trips"]["journey_seconds"]
    assert first["trips"]["journey_seconds"] != osm["trips"]["journey_seconds"]
    assert ScenarioSpec(**spec).fingerprint() != ScenarioSpec(**{**spec, "traffic_model": "osm"}).fingerprint()


def test_traffic_is_counted_on_the_road_the_car_is_really_on():
    network, model, simulation = setup(line_graph())
    vehicle = add_car(simulation, network, [0, 1, 2, 3])
    for _ in range(4):
        simulation.step()
    road = (network.position[0], network.position[1])

    # After a reroute from mid-road the route starts with a made-up stretch,
    # but the car (and the congestion map) stay on the real road
    vehicle.set_route([vehicle.get_current_location()] + network.points([1, 2, 3]))
    simulation.step()
    assert simulation.get_traffic_density() == {road: 1}
    assert set(simulation.get_congestion()) == {tuple(sorted(road))}
