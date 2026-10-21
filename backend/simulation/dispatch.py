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

from backend.routing.a_star import a_star
from backend.simulation import rerouting


def remaining_distance(vehicle):
    """Metres the vehicle still has to drive along its current route."""

    route = vehicle.route

    if vehicle.finished or vehicle.position >= len(route) - 1:
        return 0.0

    # From where it is now to the next point on the route...
    total = vehicle.calculate_distance(
        vehicle.get_current_location(),
        route[vehicle.position + 1]
    )

    # ...then every full segment after that
    for index in range(vehicle.position + 1, len(route) - 1):
        total += vehicle.calculate_distance(route[index], route[index + 1])

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
        eta = remaining / vehicle.speed if vehicle else 0.0

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


def block_road_ahead(graph, simulation, node_by_point):
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
            node_by_point
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
