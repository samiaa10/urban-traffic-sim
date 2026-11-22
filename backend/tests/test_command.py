"""The emergency response command centre (simulation/command.py): units
sent by road travel time, time at the scene, queues, road effects,
timelines, metrics, rerouting and recorded tracks."""

import pytest

from backend.routing.travel_time import RoadNetwork
from backend.simulation import dispatch
from backend.simulation.command import (
    ON_SCENE_SECONDS,
    CommandCentre,
    nearest_road,
    segment_of,
)
from backend.simulation.rerouting import build_node_lookup
from backend.simulation.road_model import make_road_model
from backend.simulation.services import SERVICES
from backend.simulation.simulation import Simulation
from backend.simulation.vehicle import straight_line_metres
from backend.tests.helpers import make_grid_graph


def world(size=8, stations=(("ambulance", 0, 1),)):
    """A grid with the realistic emergency rules and a command centre.
    stations: (service, junction, units)."""

    graph = make_grid_graph(size)
    network = RoadNetwork(graph)
    point = network.point(0)
    simulation = Simulation([point, point], road_model=make_road_model("osm", network))
    simulation.vehicles[0].finished = True
    simulation.use_road_lengths(network.length_between_points)
    command = CommandCentre(simulation, network)
    for service, node, units in stations:
        latitude, longitude = network.position[node]
        command.add_station(latitude, longitude, service, f"{service} {node}", "test", units)
    return graph, network, simulation, command


def where(network, node):
    return network.position[node]


def run(simulation, command, seconds):
    for _ in range(seconds):
        simulation.step()
        dispatch.after_step(simulation)
        command.update()
        command.record()


def run_until(simulation, command, done, limit=6000):
    for _ in range(limit):
        if done():
            return
        run(simulation, command, 1)
    raise AssertionError("did not happen in time")


# ------------------------------------------------------------
# Time at the scene
# ------------------------------------------------------------

def test_a_unit_stays_at_the_scene_then_drives_back():
    graph, network, simulation, command = world()
    incident, outcomes = command.report("medical", *where(network, 27), graph)
    unit = incident.units[0]
    vehicle = unit.assigned_vehicle
    assert outcomes[0]["status"] == "dispatched"

    run_until(simulation, command, lambda: unit.resolved)
    arrived = simulation.time
    assert unit.on_scene and not vehicle.available
    assert command.unit_status(simulation.vehicles.index(vehicle), vehicle)[0] == "on_scene"

    # Busy for exactly the on-scene time, then on its way home and free
    run(simulation, command, ON_SCENE_SECONDS["medical"] - 1)
    assert unit.on_scene
    run(simulation, command, 1)
    assert unit.cleared_at == arrived + ON_SCENE_SECONDS["medical"]
    assert vehicle.available and vehicle.going_home
    assert incident.resolved_at == unit.cleared_at


def test_without_on_scene_time_a_unit_is_freed_on_arrival_as_before():
    graph, network, simulation, _ = world()
    emergency = dispatch.dispatch_emergency(graph, simulation, *where(network, 27))["emergency"]
    for _ in range(3000):
        simulation.step()
        dispatch.after_step(simulation)
        if emergency.resolved:
            break
    assert emergency.cleared_at == emergency.resolved_at
    assert emergency.assigned_vehicle.available


# ------------------------------------------------------------
# Dispatch
# ------------------------------------------------------------

def test_each_service_in_the_plan_gets_the_unit_that_arrives_first_by_road():
    graph, network, simulation, command = world(stations=(
        ("ambulance", 0, 1), ("ambulance", 63, 1), ("fire", 7, 1),
    ))
    incident, outcomes = command.report("fire", *where(network, 54), graph)
    assert [o["status"] for o in outcomes] == ["dispatched", "dispatched"]
    services = [u.emergency_type for u in incident.units]
    assert services == ["fire", "ambulance"]

    # The ambulance sent is the one with the shorter planned drive
    target = dispatch.nearest_junction(graph, *where(network, 54))
    sent = incident.units[1].assigned_vehicle
    drive = {}
    for _, vehicle in command.units():
        if vehicle.vehicle_type == "ambulance":
            start = dispatch.nearest_junction(graph, vehicle.home_station.latitude, vehicle.home_station.longitude)
            drive[vehicle] = network.fastest_path(start, target, vehicle.speed)[1]
    assert drive[sent] == min(drive.values())


def test_a_service_with_no_units_is_refused_and_shown_on_the_timeline():
    graph, network, simulation, command = world()
    incident, outcomes = command.report("fire", *where(network, 20), graph)
    assert [o["status"] for o in outcomes] == ["no_vehicle", "dispatched"]
    assert incident.refused == [("fire", "no_vehicle")]
    labels = [e["label"] for e in command.timeline(incident)]
    assert "No fire engine to send" in labels


def test_simultaneous_incidents_queue_for_a_busy_unit_and_get_it_next():
    graph, network, simulation, command = world()
    first, _ = command.report("medical", *where(network, 9), graph)
    second, outcomes = command.report("medical", *where(network, 50), graph)
    assert outcomes[0]["status"] == "queued"
    assert command.incident_status(second) == "queued"
    assert command.metrics()["queued_units"] == 1

    run_until(simulation, command, lambda: second.units[0].resolved)
    unit = second.units[0]
    # It got the same ambulance once that left the first scene
    assert unit.assigned_vehicle is first.units[0].assigned_vehicle
    assert unit.dispatched_at >= first.units[0].cleared_at
    dispatched = next(e for e in command.timeline(second) if e["event"] == "dispatched")
    assert "after waiting" in dispatched["label"]
    # Response time counts the wait
    assert unit.response_time == unit.resolved_at - unit.created_at


# ------------------------------------------------------------
# Road effects and resolving
# ------------------------------------------------------------

def test_a_road_closure_closes_the_nearest_road_until_the_operator_reopens_it():
    graph, network, simulation, command = world()
    lat, lon = where(network, 27)
    incident, outcomes = command.report("road_closure", lat + 0.0002, lon, graph)
    assert outcomes == []
    assert incident.segment in simulation.get_blocked_segments()
    a, b = nearest_road(graph, lat + 0.0002, lon)
    assert 27 in (a, b) and segment_of(graph, a, b) == incident.segment

    run(simulation, command, 120)
    assert incident.resolved_at is None
    command.resolve(incident)
    assert incident.segment not in simulation.get_blocked_segments()
    assert incident.resolved_at == simulation.time
    assert command.incident_status(incident) == "resolved"


def test_a_road_accident_slows_the_road_and_resolves_once_the_road_is_clear():
    graph, network, simulation, command = world(stations=(("ambulance", 0, 1), ("police", 63, 1)))
    incident, _ = command.report("road_accident", *where(network, 36), graph)
    assert incident.segment in simulation.accidents
    clears = simulation.accidents[incident.segment].clears_at
    units = incident.units
    run_until(simulation, command, lambda: all(u.cleared_at is not None for u in units))
    # Units have left, but the accident is still on the road
    assert simulation.time < clears and incident.resolved_at is None
    run_until(simulation, command, lambda: incident.resolved_at is not None)
    assert incident.road_cleared_at == clears == incident.resolved_at


def test_resolving_stands_units_down_early_but_not_while_one_is_on_its_way():
    graph, network, simulation, command = world()
    incident, _ = command.report("medical", *where(network, 45), graph)
    with pytest.raises(ValueError):
        command.resolve(incident)
    run_until(simulation, command, lambda: incident.units[0].resolved)
    run(simulation, command, 30)
    command.resolve(incident)
    unit = incident.units[0]
    assert unit.cleared_at == simulation.time < unit.resolved_at + ON_SCENE_SECONDS["medical"]
    assert incident.resolved_by == "operator"
    assert unit.assigned_vehicle.going_home
    with pytest.raises(ValueError):
        command.resolve(incident)


def test_flood_units_stage_at_a_dry_junction_they_can_reach():
    graph, network, simulation, command = world(stations=(("fire", 0, 1),))
    centre = where(network, 36)
    # (the grid's junctions are 200 m apart: a bigger flood than Norwich's)
    incident, outcomes = command.report("flood", *centre, graph, flood_radius=450)
    assert incident.flood is not None and incident.flood.closes_roads
    wet = {p for segment in incident.flood.segments for p in segment}
    assert incident.target not in wet
    assert outcomes[0]["status"] == "dispatched"
    assert straight_line_metres(centre, incident.target) <= 450 + 250
    run_until(simulation, command, lambda: incident.units[0].resolved)


def test_unknown_kinds_and_places_off_the_map_are_refused():
    graph, network, simulation, command = world()
    with pytest.raises(ValueError):
        command.report("alien_landing", *where(network, 3), graph)
    with pytest.raises(ValueError):
        command.report("medical", float("nan"), 1.29, graph)
    assert command.incidents == []


# ------------------------------------------------------------
# Rerouting
# ------------------------------------------------------------

def test_a_unit_is_rerouted_round_a_road_closed_ahead_and_still_arrives():
    graph, network, simulation, command = world(size=8)
    incident, _ = command.report("medical", *where(network, 63), graph)
    unit = incident.units[0]
    run(simulation, command, 5)
    result = dispatch.block_road_ahead(graph, simulation, build_node_lookup(graph))
    assert result["outcome"] == "rerouted"
    vehicle = unit.assigned_vehicle
    assert vehicle.reroutes >= 1
    run_until(simulation, command, lambda: unit.resolved)
    arrived = next(e for e in command.timeline(incident) if e["event"] == "arrived")
    assert "route change" in arrived["label"]


# ------------------------------------------------------------
# Timeline, metrics and tracks
# ------------------------------------------------------------

def test_the_timeline_is_in_order_and_uses_the_simulations_times():
    graph, network, simulation, command = world(stations=(("ambulance", 0, 1), ("fire", 63, 1)))
    run(simulation, command, 10)
    incident, _ = command.report("fire", *where(network, 30), graph)
    run_until(simulation, command, lambda: incident.resolved_at is not None)
    events = command.timeline(incident)
    assert [e["time"] for e in events] == sorted(e["time"] for e in events)
    assert events[0] == {"time": 10, "event": "reported", "label": "Fire reported"}
    assert events[-1]["event"] == "resolved"
    for unit in incident.units:
        arrival = next(e for e in events if e["event"] == "arrived" and e["service"] == unit.emergency_type)
        assert arrival["response_seconds"] == unit.resolved_at - 10
        assert arrival["within_target"] == (arrival["response_seconds"] <= SERVICES[unit.emergency_type].target_seconds)


def test_metrics_come_from_the_units_that_arrived():
    graph, network, simulation, command = world(stations=(("ambulance", 0, 2),))
    a, _ = command.report("medical", *where(network, 9), graph)
    b, _ = command.report("medical", *where(network, 63), graph)
    metrics = command.metrics()
    assert metrics["responses"] == 0 and metrics["mean_response_seconds"] is None
    assert metrics["units_total"] == 2 and metrics["units_available"] == 0 and metrics["units_en_route"] == 2
    run_until(simulation, command, lambda: a.units[0].resolved and b.units[0].resolved)
    metrics = command.metrics()
    times = [a.units[0].response_time, b.units[0].response_time]
    assert metrics["responses"] == 2
    assert metrics["mean_response_seconds"] == pytest.approx(sum(times) / 2)
    assert metrics["worst_response_seconds"] == max(times)
    assert metrics["within_target_percent"] == 100 * sum(t <= 420 for t in times) / 2
    assert metrics["units_on_scene"] == 2 and metrics["incidents_open"] == 2
    assert metrics["by_service"]["ambulance"]["responses"] == 2


def test_tracks_follow_the_unit_from_its_station_once_a_second():
    graph, network, simulation, command = world()
    incident, _ = command.report("medical", *where(network, 63), graph)
    vehicle = incident.units[0].assigned_vehicle
    index = simulation.vehicles.index(vehicle)
    run_until(simulation, command, lambda: incident.units[0].resolved)
    run(simulation, command, 100)
    track = command.tracks[index]
    times = [sample[0] for sample in track]
    assert times == sorted(set(times))
    # Starts at the station, reaches the scene
    station = (vehicle.home_station.latitude, vehicle.home_station.longitude)
    assert straight_line_metres(station, tuple(track[0][1:])) < 30
    scene = where(network, 63)
    assert straight_line_metres(scene, tuple(track[-1][1:])) < 30
    # Standing at the scene is two samples (when it stopped and now), not one a second
    still = [s for s in track if straight_line_metres(scene, tuple(s[1:])) < 1]
    assert len(still) == 2 and still[-1][0] == simulation.time
    # Every moving sample is a real position the simulation had: no jumps
    # longer than the unit could drive in the time between them
    for (t1, *p1), (t2, *p2) in zip(track, track[1:]):
        assert straight_line_metres(tuple(p1), tuple(p2)) <= vehicle.speed * (t2 - t1) + 1


def test_state_describes_every_unit_and_incident():
    graph, network, simulation, command = world(stations=(("ambulance", 0, 1), ("police", 63, 2)))
    command.report("crime", *where(network, 30), graph)
    state = command.state()
    assert len(state["units"]) == 3 and len(state["stations"]) == 2
    states = sorted(u["state"] for u in state["units"])
    assert states == ["available", "available", "en_route"]
    moving = next(u for u in state["units"] if u["state"] == "en_route")
    assert moving["service"] == "police" and moving["incident_id"] == 1
    assert moving["eta_seconds"] > 0 and len(moving["route"]) >= 2
    assert state["incidents"][0]["status"] == "en_route"
