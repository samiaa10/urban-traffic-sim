"""
Emergency response: live status, and "what if a road ahead is closed?"
----------------------------------------------------------------------

The pieces already existed separately:

    dispatch an ambulance      (Simulation.dispatch_from_nearest_station)
    close a road               (Simulation.block_segment)
    reroute affected vehicles  (rerouting.reroute_affected_vehicles)

This file joins them into one scenario you can watch from start to finish:

    1. an emergency happens and the nearest ambulance is sent
    2. a road on the ambulance's route ahead is closed
    3. the ambulance is rerouted around it (same code as any other vehicle)
    4. it arrives, and the response time is measured in simulated seconds

Nothing here talks to FastAPI, so all of it can be tested on a tiny map.
"""

import logging
import math

from backend.routing.a_star import a_star
from backend.simulation import rerouting


logger = logging.getLogger(__name__)


def nearest_junction(graph, latitude, longitude):
    """The junction closest to a point (straight line, in degrees).

    Uses the grid index (routing/spatial.py), which gives exactly the
    answer of the scan below (nearest_junction_scan), ties included."""

    from backend.routing.spatial import index_for

    found = index_for(graph).nearest(latitude, longitude)
    return found[0] if found else None


def nearest_junction_scan(graph, latitude, longitude):
    """The original scan over every junction (the reference the index is
    tested against)."""

    nearest = None
    nearest_distance = float("inf")

    for node, data in graph.nodes.items():
        distance = math.sqrt(
            (data["latitude"] - latitude) ** 2
            + (data["longitude"] - longitude) ** 2
        )

        if distance < nearest_distance:
            nearest_distance = distance
            nearest = node

    return nearest


def _emergency_factor(simulation, traffic):
    """Speed factor function for planning an emergency vehicle's route:
    the factor a vehicle joining each road would get, with right of way
    (services.EmergencyRules)."""

    rules = simulation.emergency_rules

    def factor(key):
        return rules.emergency_factor(
            simulation.road_speed_factor(key, traffic.get(key, 0) + 1)
        )

    return factor


def _plan(simulation, network, vehicle, target, traffic):
    """Fastest route for an emergency vehicle from where it is to `target`
    (a junction), under current traffic and closures: (path, seconds,
    junctions explored)."""

    here = vehicle.get_current_location()
    start = nearest_junction(network.graph, here["latitude"], here["longitude"])
    return network.fastest_path(
        start, target, vehicle.speed,
        speed_factor=_emergency_factor(simulation, traffic),
        blocked_segments=simulation.get_blocked_segments(),
        directed=simulation.road_model.directed,
    )


def _free_vehicles(simulation, service):
    """Vehicles of a service that can be sent: at their station, or on
    their way back to it."""

    return [
        vehicle for vehicle in simulation.vehicles
        if vehicle.vehicle_type == service and vehicle.available
        and vehicle.home_station is not None
    ]


def _send(simulation, network, emergency, traffic):
    """Send the free vehicle of the emergency's service that would arrive
    first. Returns (vehicle, path, seconds, junctions explored in all the
    searches), with vehicle None if nothing free can reach it."""

    target = nearest_junction(network.graph, emergency.latitude, emergency.longitude)
    best = None
    explored = 0

    for vehicle in _free_vehicles(simulation, emergency.emergency_type):
        path, seconds, searched = _plan(simulation, network, vehicle, target, traffic)
        explored += searched
        if path and (best is None or seconds < best[2]):
            best = (vehicle, path, seconds)

    if best is None:
        return None, None, None, explored

    vehicle, path, seconds = best
    station = vehicle.home_station
    if vehicle in station.available_vehicles:
        station.available_vehicles.remove(vehicle)
    vehicle.available = False
    vehicle.on_emergency = True
    vehicle.going_home = False
    vehicle.needs_route_home = False
    vehicle.set_route(network.points(path), reset_distance=True)
    vehicle.finished = len(path) < 2
    emergency.assigned_vehicle = vehicle
    emergency.dispatched_at = simulation.time

    return vehicle, path, seconds, explored


def _reachable(simulation, network, emergency):
    """Whether any station of the emergency's service can drive to it at
    all, with the roads closed now (traffic does not matter: it only
    slows vehicles). If none can, waiting for a vehicle would be forever."""

    graph = network.graph
    target = nearest_junction(graph, emergency.latitude, emergency.longitude)
    blocked = simulation.get_blocked_segments()
    starts = {
        nearest_junction(graph, station.latitude, station.longitude)
        for station in simulation.stations
        if station.station_type == emergency.emergency_type
    }

    return any(
        network.fastest_path(start, target, 1.0, blocked_segments=blocked)[0]
        for start in starts
    )


def _refuse(simulation, emergency, reason):
    emergency.cancel_reason = reason
    simulation.cancel_emergency(emergency)


def _dispatch_by_drive_time(graph, simulation, emergency):
    network = simulation.road_model.network
    vehicle, path, _, explored = _send(simulation, network, emergency, simulation.get_traffic_density())
    outcome = {"emergency": emergency, "vehicle": vehicle, "vehicle_index": None}

    if vehicle is None:
        if not _has_service(simulation, emergency.emergency_type):
            _refuse(simulation, emergency, "no_vehicle")
            return {**outcome, "status": "no_vehicle"}

        # No station of the service can reach it: refused, as the original
        # rules do, rather than queued for ever
        if not _reachable(simulation, network, emergency):
            _refuse(simulation, emergency, "no_route")
            return {**outcome, "status": "no_route"}

        # Some vehicle could reach it, but none of those is free: it waits
        if simulation.emergency_rules.queue:
            return {**outcome, "status": "queued"}

        _refuse(simulation, emergency, "no_vehicle")
        return {**outcome, "status": "no_vehicle"}

    return {
        **outcome,
        "status": "dispatched",
        "vehicle_index": simulation.vehicles.index(vehicle),
        "path": path,
        "route": vehicle.route,
        "distance": network.path_length(path),
        "nodes_explored": explored,
    }


def _has_service(simulation, service):
    """Whether the run has any vehicle of this service at all (otherwise
    queueing would wait forever)."""

    return any(
        vehicle.vehicle_type == service and vehicle.home_station is not None
        for vehicle in simulation.vehicles
    )


def after_step(simulation):
    """The realistic emergency rules' work after each simulated second
    (services.py): send vehicles that finished a call home, park the ones
    that got there, and answer queued emergencies, oldest first. A queued
    emergency no station can reach any more (a road closed) is refused
    with cancel_reason "no_route". Returns the emergencies dispatched from
    the queue."""

    rules = simulation.emergency_rules
    if not (rules.queue or rules.drive_back):
        return []

    network = simulation.road_model.network
    traffic = None

    for vehicle in simulation.vehicles:
        if not vehicle.going_home:
            continue

        station = vehicle.home_station

        if vehicle.needs_route_home:
            vehicle.needs_route_home = False
            if traffic is None:
                traffic = simulation.get_traffic_density()
            home = nearest_junction(network.graph, station.latitude, station.longitude)
            path, _, _ = _plan(simulation, network, vehicle, home, traffic)
            if len(path) >= 2:
                vehicle.set_route(network.points(path), reset_distance=True)
                vehicle.finished = False
                continue

        if vehicle.finished:
            vehicle.going_home = False
            station.return_vehicle(vehicle)

    dispatched = []

    if rules.queue:
        for emergency in simulation.queued_emergencies():
            if traffic is None:
                traffic = simulation.get_traffic_density()
            vehicle = _send(simulation, network, emergency, traffic)[0]
            if vehicle is not None:
                dispatched.append(emergency)
                continue

            # Re-check whether any station can still reach it, but only
            # when roads have closed or reopened since the last check
            changes = len(simulation.events)
            if getattr(emergency, "reach_checked_at", None) != changes:
                emergency.reach_checked_at = changes
                if not _reachable(simulation, network, emergency):
                    _refuse(simulation, emergency, "no_route")

    return dispatched


def dispatch_emergency(graph, simulation, latitude, longitude, emergency_type="ambulance"):
    """Raise an emergency and send a vehicle to it.

    With the realistic rules (osm traffic model, services.py) the free
    vehicle of the service that would arrive first is sent along the
    fastest route, and with none free the emergency is queued ("queued").
    Otherwise, as originally:

    The nearest station (straight line) with a vehicle free sends it along
    an A* route that accounts for current traffic and closed roads. Used by
    /emergency/create and by the scenario runner, so both dispatch the
    same way.

    Returns a dict with "status": "dispatched", "no_vehicle" (nothing
    free: the emergency is cancelled) or "no_route" (the vehicle cannot
    reach it: the emergency is cancelled and the vehicle freed), plus
    "emergency", "vehicle", "vehicle_index", and when dispatched "path",
    "route", "distance" (metres) and "nodes_explored".
    """

    emergency = simulation.create_emergency(latitude, longitude, emergency_type)

    if simulation.emergency_rules.dispatch_by_drive_time:
        return _dispatch_by_drive_time(graph, simulation, emergency)

    vehicle = simulation.dispatch_from_nearest_station(emergency)
    if vehicle is not None:
        emergency.dispatched_at = simulation.time
    else:
        emergency.cancel_reason = "no_vehicle"

    outcome = {"emergency": emergency, "vehicle": vehicle, "vehicle_index": None}

    if vehicle is None:
        simulation.cancel_emergency(emergency)
        return {**outcome, "status": "no_vehicle"}

    target = nearest_junction(graph, latitude, longitude)
    here = vehicle.get_current_location()

    logger.debug(
        "Dispatching vehicle from %s to emergency at (%s, %s); "
        "route before dispatch: %s",
        here, latitude, longitude, vehicle.route,
    )

    start = nearest_junction(graph, here["latitude"], here["longitude"])

    # Plan around current traffic and closed roads
    path, distance, nodes_explored = a_star(
        graph,
        start,
        target,
        return_stats=True,
        traffic=simulation.get_segment_traffic(),
        blocked_segments=simulation.get_blocked_segments(),
    )

    vehicle_index = simulation.vehicles.index(vehicle)

    if not path:
        emergency.cancel_reason = "no_route"
        simulation.cancel_emergency(emergency)
        return {**outcome, "status": "no_route", "vehicle_index": vehicle_index}

    route = [
        {"latitude": graph.nodes[n]["latitude"], "longitude": graph.nodes[n]["longitude"]}
        for n in path
    ]

    # Give the emergency vehicle its new route
    vehicle.set_route(route, reset_distance=True)

    return {
        **outcome,
        "status": "dispatched",
        "vehicle_index": vehicle_index,
        "path": path,
        "route": route,
        "distance": distance,
        "nodes_explored": nodes_explored,
    }


def remaining_on_current_road(vehicle):
    """Metres left on the road the vehicle is on now (same measure it drives)."""

    route = vehicle.route

    return max(
        0.0,
        vehicle.segment_distance(
            route[vehicle.position], route[vehicle.position + 1]
        ) - vehicle.distance_on_segment,
    )


def remaining_distance(vehicle):
    """Metres the vehicle still has to drive along its current route."""

    route = vehicle.route

    if vehicle.finished or vehicle.position >= len(route) - 1:
        return 0.0

    # The rest of the road it is on...
    total = remaining_on_current_road(vehicle)

    # ...then every full road after that
    for index in range(vehicle.position + 1, len(route) - 1):
        total += vehicle.segment_distance(route[index], route[index + 1])

    return total


def remaining_seconds(simulation, vehicle):
    """Seconds the vehicle still needs, given the traffic on its route NOW.

    Same idea as remaining_distance, but each road is driven at the speed
    that road allows right now (Simulation.road_speed_factor: traffic and
    any accident, exactly as the simulation moves vehicles), so a congested
    route has a longer ETA.
    """

    route = vehicle.route

    if vehicle.finished or vehicle.position >= len(route) - 1:
        return 0.0

    traffic = simulation.get_traffic_density()
    limit_factor = simulation.limit_factor_for(vehicle)
    rules = simulation.emergency_rules
    right_of_way = bool(rules.right_of_way) and simulation.on_call(vehicle)

    def seconds_for(metres, point_a, point_b):
        road = simulation.traffic_key(point_a, point_b)
        factor = simulation.road_speed_factor(road, traffic.get(road, 0))
        if right_of_way:
            factor = rules.emergency_factor(factor)
        free_speed = vehicle.speed
        if limit_factor is not None:
            free_speed *= limit_factor(point_a, point_b)
        return metres / (free_speed * factor)

    # The rest of the road it is on...
    total = seconds_for(
        remaining_on_current_road(vehicle),
        route[vehicle.position],
        route[vehicle.position + 1],
    )

    # ...then every full road after that
    for index in range(vehicle.position + 1, len(route) - 1):
        total += seconds_for(
            vehicle.segment_distance(route[index], route[index + 1]),
            route[index],
            route[index + 1],
        )

    return total


def vehicle_is_stuck(simulation, vehicle):
    """True if the vehicle is waiting because the road ahead is closed."""

    if vehicle.finished or vehicle.position >= len(vehicle.route) - 1:
        return False

    return simulation.is_road_closed(
        vehicle.route[vehicle.position],
        vehicle.route[vehicle.position + 1]
    )


def emergency_status(simulation, emergency):
    """Describe one emergency in a form the frontend can show.

    state is one of:
        "waiting"   no vehicle has been sent yet
        "en_route"  the vehicle is driving to the emergency
        "blocked"   the vehicle is stopped by a closed road
        "arrived"   the vehicle got there (response_time_seconds is final)
    """

    vehicle = emergency.assigned_vehicle

    if emergency.resolved:
        state = "arrived"
    elif vehicle is None:
        state = "waiting"
    elif vehicle_is_stuck(simulation, vehicle):
        state = "blocked"
    else:
        state = "en_route"

    if emergency.resolved:
        elapsed = emergency.response_time
        remaining = 0.0
        eta = 0.0
    else:
        elapsed = simulation.time - emergency.created_at
        remaining = remaining_distance(vehicle) if vehicle else 0.0
        eta = remaining_seconds(simulation, vehicle) if vehicle else 0.0

    vehicle_id = None
    if vehicle is not None and vehicle in simulation.vehicles:
        vehicle_id = simulation.vehicles.index(vehicle)

    return {
        "state": state,
        "latitude": emergency.latitude,
        "longitude": emergency.longitude,
        "vehicle_id": vehicle_id,
        "elapsed_seconds": elapsed,
        "response_time_seconds": emergency.response_time,
        "remaining_metres": remaining,
        "eta_seconds": eta,
        "reroutes": vehicle.reroutes if vehicle else 0,
        "closed_roads": len(simulation.get_blocked_segments()),
        "service": emergency.emergency_type,
        # Seconds it waited for a free vehicle (queue), None while waiting
        "waited_seconds": (
            emergency.dispatched_at - emergency.created_at
            if emergency.dispatched_at is not None else None
        ),
    }


def latest_active_emergency(simulation):
    """The most recent emergency whose vehicle is still driving to it."""

    for emergency in reversed(simulation.get_emergencies()):

        vehicle = emergency.assigned_vehicle

        if emergency.active and vehicle is not None and not vehicle.finished:
            return emergency

    return None


def candidate_segments(vehicle, node_by_point, already_blocked):
    """Roads ahead of the vehicle that could be closed, best first.

    We skip the road it is on right now (closing that would trap it) and
    the very last road (closing that would cut the destination off), and
    try the roads in the middle of the remaining journey first.
    """

    route = vehicle.route
    first = vehicle.position + 1
    last = len(route) - 3

    middle = (first + last) / 2

    indices = []

    for index in range(first, last + 1):

        point_a = rerouting.point_key(route[index])
        point_b = rerouting.point_key(route[index + 1])

        # Only roads that are real junction-to-junction segments
        if point_a not in node_by_point or point_b not in node_by_point:
            continue

        if rerouting.segment_key(route[index], route[index + 1]) in already_blocked:
            continue

        indices.append(index)

    indices.sort(key=lambda index: abs(index - middle))

    return [
        rerouting.segment_key(route[index], route[index + 1])
        for index in indices
    ]


def block_road_ahead(graph, simulation, node_by_point, route_finder=None):
    """Close a road on the ambulance's route and reroute it around it.

    Returns a dict whose "outcome" is:
        "rerouted"      road closed, ambulance has a new route (with the
                        extra distance and time the detour costs)
        "no_emergency"  there is no ambulance on its way
        "too_close"     not enough road left ahead to close one
        "no_detour"     every road we could close would cut the ambulance
                        off, so nothing was closed
        "off_network"   the route isn't on the road graph
    """

    emergency = latest_active_emergency(simulation)

    if emergency is None:
        return {"outcome": "no_emergency"}

    vehicle = emergency.assigned_vehicle

    start_node, _ = rerouting.find_restart_point(vehicle, node_by_point)
    destination_node = node_by_point.get(
        rerouting.point_key(vehicle.route[-1])
    )

    if start_node is None or destination_node is None:
        return {"outcome": "off_network"}

    already_blocked = set(simulation.get_blocked_segments())

    candidates = candidate_segments(vehicle, node_by_point, already_blocked)

    if not candidates:
        return {"outcome": "too_close"}

    distance_before = remaining_distance(vehicle)

    for segment in candidates:

        # Would the ambulance still have a way to the emergency?
        # (A trial search: nothing is changed unless there is one.)
        path, _ = a_star(
            graph,
            start_node,
            destination_node,
            blocked_segments=already_blocked | {segment}
        )

        if not path:
            continue

        simulation.block_segment(segment)

        # Existing rerouting code: finds every vehicle whose route uses
        # the closed road (the ambulance included) and recalculates it.
        stats = rerouting.reroute_affected_vehicles(
            graph,
            simulation,
            node_by_point,
            route_finder=route_finder
        )

        distance_after = remaining_distance(vehicle)
        extra_metres = distance_after - distance_before

        return {
            "outcome": "rerouted",
            "blocked_segment": segment,
            "vehicle_id": simulation.vehicles.index(vehicle),
            "distance_before_metres": distance_before,
            "distance_after_metres": distance_after,
            "extra_metres": extra_metres,
            "extra_seconds": extra_metres / vehicle.speed,
            "rerouting": stats,
            "route": vehicle.route,
        }

    return {"outcome": "no_detour"}
