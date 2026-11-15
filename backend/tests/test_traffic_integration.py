"""Everything at once: a fleet, a rush-hour surge, an accident that clears
by itself, and a closure that reopens, with dynamic rerouting on. Checks
the rules that must hold every simulated second, not just the end state."""

import random

import pytest

from backend.routing.travel_time import RoadNetwork
from backend.simulation.demand import Zone
from backend.simulation.rerouting import segment_key
from backend.simulation.simulation import Simulation
from backend.simulation.traffic_manager import ReroutePolicy, TrafficManager
from backend.tests.helpers import make_grid_graph


ACCIDENT_STARTS, ACCIDENT_LASTS = 30, 120
CLOSURE_STARTS, CLOSURE_ENDS = 60, 150
SURGE_LASTS = 120
STEPS = 1500


def current_road(vehicle):
    if vehicle.finished or vehicle.position >= len(vehicle.route) - 1:
        return None
    return segment_key(vehicle.route[vehicle.position], vehicle.route[vehicle.position + 1])


def busiest_road(network, vehicles):
    usage = {}
    for vehicle in vehicles:
        for a, b in zip(vehicle.route[1:], vehicle.route[2:]):
            road = segment_key(a, b)
            usage[road] = usage.get(road, 0) + 1
    return max(usage, key=usage.get)


@pytest.fixture(scope="module")
def run():
    """Run the combined scenario once and record what happened."""

    network = RoadNetwork(make_grid_graph(size=8))
    parked = network.point(0)
    simulation = Simulation([parked, parked])
    policy = ReroutePolicy()
    manager = TrafficManager(network, simulation, policy)

    fleet = manager.spawn_vehicles(120, random.Random(11))["vehicles"]
    manager.add_surge(Zone(52.627, 1.2905, 400), "inbound", 60, SURGE_LASTS)

    accident_road = busiest_road(network, fleet)
    closed_road = None      # chosen when it closes: the busiest road then

    record = {
        "entered_closed_road": [],
        "accident_speed_violations": [],
        "seconds_on_accident_road": 0,
        "flagged_on_reopen": None,
        "accident_cleared_at": None,
        "max_urgent_events": 0,
    }

    previous_road = {}

    for _ in range(STEPS):
        now = simulation.time

        if now == ACCIDENT_STARTS:
            simulation.add_accident(accident_road, "severe", duration=ACCIDENT_LASTS)
            manager.process_events()
        if now == CLOSURE_STARTS:
            usage = {}
            for vehicle in simulation.vehicles[1:]:
                if vehicle.finished:
                    continue
                for a, b in zip(vehicle.route[vehicle.position + 1:], vehicle.route[vehicle.position + 2:]):
                    road = segment_key(a, b)
                    if road != accident_road:
                        usage[road] = usage.get(road, 0) + 1
            closed_road = max(usage, key=usage.get)
            simulation.block_segment(closed_road)
            manager.process_events()
        if now == CLOSURE_ENDS:
            simulation.unblock_segment(closed_road)
            record["flagged_on_reopen"] = manager.process_events()

        manager.step()

        for index, vehicle in enumerate(simulation.vehicles[1:], start=1):
            road = current_road(vehicle)

            # Never drive ONTO a closed road (being on it when it closes is fine)
            if (
                road is not None
                and road != previous_road.get(index)
                and road in simulation.blocked_segments
            ):
                record["entered_closed_road"].append((simulation.time, index))

            if road == accident_road and road in simulation.accidents:
                record["seconds_on_accident_road"] += 1

            # Anyone on the accident road is slowed at least as much as the accident says
            if (
                road == accident_road
                and road in simulation.accidents
                and vehicle.speed_factor > 0.2 + 1e-9
            ):
                record["accident_speed_violations"].append((simulation.time, index))

            previous_road[index] = road

        if record["accident_cleared_at"] is None and accident_road not in simulation.accidents and simulation.time > ACCIDENT_STARTS:
            record["accident_cleared_at"] = simulation.time

    record.update(
        simulation=simulation,
        manager=manager,
        fleet=fleet,
        accident_road=accident_road,
        policy=policy,
    )
    return record


def test_no_vehicle_ever_drives_onto_a_closed_road(run):
    assert run["entered_closed_road"] == []


def test_vehicles_on_the_accident_road_are_slowed_by_it(run):
    # Some vehicles really were there (the check below is not vacuous)
    assert run["seconds_on_accident_road"] > 0
    assert run["accident_speed_violations"] == []


def test_the_accident_clears_exactly_when_its_time_is_up(run):
    assert run["accident_cleared_at"] == ACCIDENT_STARTS + ACCIDENT_LASTS

    types = [e["type"] for e in run["simulation"].events]
    assert types.count("accident") == 1 and types.count("accident_cleared") == 1
    assert types.count("road_closed") == 1 and types.count("road_reopened") == 1


def test_reopening_the_road_makes_vehicles_reconsider(run):
    assert run["flagged_on_reopen"] > 0


def test_the_surge_added_its_vehicles(run):
    surge = run["manager"].surges[0]

    assert surge.spawned + surge.failed == SURGE_LASTS     # 60 a minute for 2 minutes
    assert surge.spawned > 0
    assert len(run["simulation"].vehicles) == 1 + 120 + surge.spawned


def test_vehicles_did_reroute_but_within_the_damping_limits(run):
    vehicles = run["simulation"].vehicles[1:]
    reroutes = [v.reroutes for v in vehicles]

    assert sum(reroutes) > 0
    # Routine switches: at most one per cooldown over a whole trip; urgent
    # switches: at most one per network event (4 events in this scenario)
    longest_trip = max(v.journey_seconds for v in vehicles)
    limit = 1 + longest_trip // run["policy"].cooldown_seconds + 4
    assert max(reroutes) <= limit


def test_everyone_arrives_in_the_end(run):
    assert all(v.finished for v in run["simulation"].vehicles[1:])


def test_journeys_are_never_faster_than_free_flow(run):
    network = run["manager"].network

    for vehicle in run["fleet"]:
        lower_bound = network.lower_bound_metres(vehicle.origin, vehicle.destination) / vehicle.speed
        assert vehicle.journey_seconds >= lower_bound - 1
