"""Vehicles slow down on congested roads, and that changes journey times.

Everything here uses tiny hand-made routes, so it runs without the
Norwich data file.
"""

import math

import pytest

from backend.simulation import dispatch
from backend.simulation.simulation import (
    SPEED_FACTORS,
    Simulation,
    congestion_level,
    speed_factor_for_count,
)
from backend.simulation.vehicle import Vehicle
from backend.tests.helpers import coords, make_diamond_graph


def diamond_route(path=("A", "B", "D")):
    graph = make_diamond_graph()
    return coords(graph, list(path))


def run_until_finished(simulation, limit=2000):
    """Step until every vehicle has finished (or fail if it never happens)."""

    for _ in range(limit):
        if all(vehicle.finished for vehicle in simulation.vehicles):
            return

        simulation.step()

    raise AssertionError("simulation did not finish")


def crowded_simulation(extra_cars):
    """One vehicle plus `extra_cars` more, all driving the same route."""

    route = diamond_route()
    simulation = Simulation(route)

    for _ in range(extra_cars):
        simulation.add_vehicle(route)

    return simulation


# ------------------------------------------------------------
# The speed rules
# ------------------------------------------------------------

def test_levels_match_vehicle_counts():
    assert congestion_level(0) == "clear"
    assert congestion_level(1) == "light"
    assert congestion_level(2) == "moderate"
    assert congestion_level(3) == "heavy"
    assert congestion_level(50) == "heavy"


def test_busier_roads_never_let_vehicles_go_faster():
    factors = [speed_factor_for_count(count) for count in range(0, 10)]

    assert factors == sorted(factors, reverse=True)


def test_every_speed_factor_is_positive_and_at_most_one():
    # A factor of 0 would freeze vehicles for ever; above 1 would speed them up.
    for factor in SPEED_FACTORS.values():
        assert 0 < factor <= 1


def test_a_lone_vehicle_is_not_slowed_down():
    assert speed_factor_for_count(1) == 1.0


# ------------------------------------------------------------
# Vehicle.move with a speed factor
# ------------------------------------------------------------

def test_without_a_speed_factor_the_vehicle_drives_at_full_speed():
    vehicle = Vehicle(diamond_route(), speed=10)

    vehicle.move(1)

    assert vehicle.distance_travelled == pytest.approx(10)


def test_half_speed_factor_covers_half_the_distance():
    vehicle = Vehicle(diamond_route(), speed=10)

    vehicle.move(1, speed_factor=lambda a, b: 0.5)

    assert vehicle.distance_travelled == pytest.approx(5)
    assert vehicle.speed_factor == 0.5


def test_speed_changes_part_way_through_a_second():
    """Fast road then slow road inside one second of driving."""

    route = diamond_route()
    speed = 400        # fast enough to cross the first road within a second

    vehicle = Vehicle(route, speed=speed)

    first_road = vehicle.calculate_distance(route[0], route[1])

    # Free flow on the first road, half speed on the second
    def factor(point_a, point_b):
        return 1.0 if point_a == route[0] else 0.5

    vehicle.move(1, speed_factor=factor)

    time_on_first = first_road / speed
    expected_on_second = speed * 0.5 * (1 - time_on_first)

    assert vehicle.position == 1
    assert vehicle.distance_on_segment == pytest.approx(expected_on_second)
    assert vehicle.distance_travelled == pytest.approx(
        first_road + expected_on_second
    )


def test_slow_vehicle_still_arrives():
    route = diamond_route()
    vehicle = Vehicle(route, speed=50)

    for _ in range(1000):
        vehicle.move(1, speed_factor=lambda a, b: 0.3)

        if vehicle.finished:
            break

    assert vehicle.finished
    assert vehicle.position == len(route) - 1


def test_slow_vehicle_takes_longer_than_free_flowing_one():
    route = diamond_route()

    def seconds_to_finish(factor):
        vehicle = Vehicle(route, speed=13.9)
        seconds = 0

        while not vehicle.finished:
            vehicle.move(1, speed_factor=lambda a, b: factor)
            seconds += 1

        return seconds

    assert seconds_to_finish(0.3) > 2 * seconds_to_finish(1.0)


# ------------------------------------------------------------
# Regression: a vehicle with speed 0 (or worse) used to be accepted.
# It never moved, and /emergency/status crashed with ZeroDivisionError.
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "bad_speed", [0, -5, float("nan"), float("inf")]
)
def test_vehicle_rejects_speeds_that_cannot_work(bad_speed):
    with pytest.raises(ValueError):
        Vehicle(diamond_route(), speed=bad_speed)


def test_emergency_vehicle_with_zero_speed_is_rejected():
    route = diamond_route()
    simulation = Simulation(route)
    station = simulation.add_station(
        route[0]["latitude"], route[0]["longitude"]
    )

    with pytest.raises(ValueError):
        simulation.add_emergency_vehicle(
            [route[0], route[0]], station, speed=0
        )

    # Nothing half-created was left behind
    assert station.available_vehicles == []
    assert len(simulation.vehicles) == 1


# ------------------------------------------------------------
# Simulation: congestion changes behaviour, not just the picture
# ------------------------------------------------------------

def test_vehicles_in_a_crowd_travel_less_far_than_a_lone_vehicle():
    alone = crowded_simulation(extra_cars=0)
    crowd = crowded_simulation(extra_cars=3)       # 4 on the road = heavy

    for _ in range(10):
        alone.step()
        crowd.step()

    assert (
        crowd.vehicles[0].distance_travelled
        < alone.vehicles[0].distance_travelled
    )


def test_congestion_makes_journeys_take_longer():
    alone = crowded_simulation(extra_cars=0)
    crowd = crowded_simulation(extra_cars=3)

    run_until_finished(alone)
    run_until_finished(crowd)

    free_flow = alone.vehicles[0].journey_seconds

    for vehicle in crowd.vehicles:
        assert vehicle.journey_seconds > free_flow

    # Heavy traffic is 30% speed, so the crowd should be far slower
    assert crowd.vehicles[0].journey_seconds > 2 * free_flow


def test_more_traffic_means_a_longer_journey():
    times = []

    for extra_cars in (0, 1, 3):
        simulation = crowded_simulation(extra_cars)
        run_until_finished(simulation)
        times.append(simulation.vehicles[0].journey_seconds)

    assert times[0] < times[1] < times[2]


def test_everyone_still_arrives_in_heavy_traffic():
    simulation = crowded_simulation(extra_cars=5)

    run_until_finished(simulation)

    assert all(vehicle.finished for vehicle in simulation.vehicles)


def test_journey_distance_is_the_same_only_the_time_changes():
    alone = crowded_simulation(extra_cars=0)
    crowd = crowded_simulation(extra_cars=3)

    run_until_finished(alone)
    run_until_finished(crowd)

    assert crowd.vehicles[0].distance_travelled == pytest.approx(
        alone.vehicles[0].distance_travelled
    )


def test_vehicles_on_different_roads_do_not_slow_each_other():
    route_one = diamond_route(("A", "B"))
    route_two = diamond_route(("C", "D"))

    simulation = Simulation(route_one)
    simulation.add_vehicle(route_two)

    simulation.step()

    for vehicle in simulation.vehicles:
        assert vehicle.speed_factor == 1.0
        assert vehicle.distance_travelled == pytest.approx(vehicle.speed)


def test_vehicle_speeds_up_again_when_the_road_clears():
    route = diamond_route()
    simulation = Simulation(route)
    simulation.add_vehicle(route[:2])            # leaves the road early

    simulation.step()
    assert simulation.vehicles[0].speed_factor < 1.0     # shared road

    for _ in range(300):
        simulation.step()

        if simulation.vehicles[1].finished:
            break

    assert simulation.vehicles[1].finished

    simulation.step()

    assert simulation.vehicles[0].speed_factor == 1.0    # road to itself again


def test_order_of_vehicles_does_not_change_the_result():
    forwards = crowded_simulation(extra_cars=3)

    backwards = crowded_simulation(extra_cars=3)
    backwards.vehicles.reverse()

    for _ in range(15):
        forwards.step()
        backwards.step()

    first = sorted(v.distance_travelled for v in forwards.vehicles)
    second = sorted(v.distance_travelled for v in backwards.vehicles)

    assert first == pytest.approx(second)


# ------------------------------------------------------------
# Journey time also includes waiting at a closed road
# ------------------------------------------------------------

def test_waiting_at_a_closed_road_counts_as_journey_time():
    route = diamond_route()
    simulation = Simulation(route)

    # Close the first road before the vehicle starts
    simulation.block_segment((
        (route[0]["latitude"], route[0]["longitude"]),
        (route[1]["latitude"], route[1]["longitude"]),
    ))

    for _ in range(10):
        simulation.step()

    vehicle = simulation.vehicles[0]

    assert vehicle.distance_travelled == 0
    assert vehicle.journey_seconds == 10


def test_finished_vehicle_stops_counting_journey_time():
    route = diamond_route(("A", "B"))
    simulation = Simulation(route)

    run_until_finished(simulation)

    seconds = simulation.vehicles[0].journey_seconds

    for _ in range(5):
        simulation.step()

    assert simulation.vehicles[0].journey_seconds == seconds


def test_new_trip_resets_the_journey_clock():
    route = diamond_route()
    vehicle = Vehicle(route)
    vehicle.journey_seconds = 99

    vehicle.set_route(route, reset_distance=True)

    assert vehicle.journey_seconds == 0


# ------------------------------------------------------------
# Emergency vehicles: response time and ETA feel the traffic
# ------------------------------------------------------------

def ambulance_response_time(extra_cars):
    """Send an ambulance A -> D with `extra_cars` other vehicles on its road."""

    route = diamond_route()

    # Vehicle 0 is a car on a different road (C -> D), so the empty-road
    # case really is empty.
    simulation = Simulation(diamond_route(("C", "D")))

    for _ in range(extra_cars):
        simulation.add_vehicle(route)

    station = simulation.add_station(
        route[0]["latitude"], route[0]["longitude"]
    )
    ambulance = simulation.add_emergency_vehicle(
        [route[0], route[0]], station, speed=20
    )

    emergency = simulation.create_emergency(
        route[-1]["latitude"], route[-1]["longitude"]
    )
    simulation.dispatch_from_nearest_station(emergency)
    ambulance.set_route(route, reset_distance=True)

    for _ in range(2000):
        simulation.step()

        if emergency.resolved:
            return emergency.response_time

    raise AssertionError("ambulance never arrived")


def test_ambulance_response_time_is_longer_in_traffic():
    empty_road = ambulance_response_time(extra_cars=0)
    busy_road = ambulance_response_time(extra_cars=3)

    assert busy_road > empty_road


def test_empty_road_response_time_is_just_distance_over_speed():
    route = diamond_route()
    length = sum(
        Vehicle(route).calculate_distance(a, b)
        for a, b in zip(route, route[1:])
    )

    response = ambulance_response_time(extra_cars=0)

    # Whole seconds: the ambulance arrives during the step that finishes it
    assert response == math.ceil(length / 20)


def test_eta_is_distance_over_speed_on_an_empty_road():
    route = diamond_route()
    simulation = Simulation(diamond_route(("C", "D")))
    station = simulation.add_station(
        route[0]["latitude"], route[0]["longitude"]
    )
    ambulance = simulation.add_emergency_vehicle(
        [route[0], route[0]], station, speed=20
    )
    emergency = simulation.create_emergency(
        route[-1]["latitude"], route[-1]["longitude"]
    )
    simulation.dispatch_from_nearest_station(emergency)
    ambulance.set_route(route, reset_distance=True)

    expected = dispatch.remaining_distance(ambulance) / 20

    assert dispatch.remaining_seconds(simulation, ambulance) == (
        pytest.approx(expected)
    )


def test_eta_grows_when_the_route_is_congested():
    route = diamond_route()

    def eta(extra_cars):
        simulation = Simulation(diamond_route(("C", "D")))

        for _ in range(extra_cars):
            simulation.add_vehicle(route)

        station = simulation.add_station(
            route[0]["latitude"], route[0]["longitude"]
        )
        ambulance = simulation.add_emergency_vehicle(
            [route[0], route[0]], station, speed=20
        )
        emergency = simulation.create_emergency(
            route[-1]["latitude"], route[-1]["longitude"]
        )
        simulation.dispatch_from_nearest_station(emergency)
        ambulance.set_route(route, reset_distance=True)

        return dispatch.emergency_status(simulation, emergency)["eta_seconds"]

    assert eta(extra_cars=3) > eta(extra_cars=0)


def test_finished_vehicle_has_no_time_remaining():
    route = diamond_route(("A", "B"))
    simulation = Simulation(route)
    run_until_finished(simulation)

    assert dispatch.remaining_seconds(
        simulation, simulation.vehicles[0]
    ) == 0.0


# ------------------------------------------------------------
# Regression: validate.py used to put a car on the SAME route as the
# ambulance. Once vehicles slow each other down, that made the ambulance
# 3-29% slower than the optimiser predicted. The check is meant to time
# an ambulance with the road to itself.
# ------------------------------------------------------------

def test_validation_times_an_ambulance_that_has_the_road_to_itself():
    from backend.optimiser.validate import SPEED, simulated_response_time

    graph = make_diamond_graph()
    path = ["A", "B", "D"]
    route = coords(graph, path)

    length = sum(
        Vehicle(route).calculate_distance(a, b)
        for a, b in zip(route, route[1:])
    )

    assert simulated_response_time(graph, path) == math.ceil(length / SPEED)
