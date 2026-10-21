"""
Automatic rerouting when a road is closed
-----------------------------------------

The chain of events:

    road closed -> which vehicles are affected? -> recalculate only
    those routes -> affected vehicles drive a new way

The key idea is the middle step. A closure only matters to vehicles whose
remaining route uses the closed road, so we check that first (cheap) and
only run the expensive A* search for those vehicles.
"""

import time

from backend.routing.a_star import a_star


def point_key(point):
    return (point["latitude"], point["longitude"])


def segment_key(point_a, point_b):
    """Same format the simulation uses for a road segment."""
    return tuple(sorted((point_key(point_a), point_key(point_b))))


def build_node_lookup(graph):
    """Map a (latitude, longitude) pair back to its junction id."""
    return {
        (data["latitude"], data["longitude"]): node
        for node, data in graph.nodes.items()
    }


def route_uses_closed_road(vehicle, blocked_segments):
    """Does the part of the route still to be driven use a closed road?"""

    route = vehicle.route

    for index in range(vehicle.position, len(route) - 1):
        if segment_key(route[index], route[index + 1]) in blocked_segments:
            return True

    return False


def find_restart_point(vehicle, node_by_point):
    """Where can the new route start?

    A vehicle in the middle of a road must finish that road first (it
    can't teleport), so the new route starts at the next junction it
    will reach. If it is already at a junction, it starts there.

    Returns (junction id, list of extra points to put in front of the
    new route). Returns (None, []) if the route isn't on the road graph.
    """

    route = vehicle.route
    position = vehicle.position

    if (
        vehicle.distance_on_segment == 0
        and point_key(route[position]) in node_by_point
    ):
        return node_by_point[point_key(route[position])], []

    for index in range(position + 1, len(route)):

        key = point_key(route[index])

        if key in node_by_point:
            return node_by_point[key], [vehicle.get_current_location()]

    return None, []


def reroute_vehicle(
    graph,
    vehicle,
    node_by_point,
    traffic=None,
    blocked_segments=None
):
    """Give one vehicle a fresh route to its destination.

    Returns a dict whose "status" is:
        "rerouted"  new route set (also has route, distance, nodes_explored)
        "stranded"  no way to the destination; the route is left alone
        "skipped"   the vehicle's route isn't on the road graph
    """

    start_node, prefix = find_restart_point(vehicle, node_by_point)
    destination_node = node_by_point.get(point_key(vehicle.route[-1]))

    if start_node is None or destination_node is None:
        return {"status": "skipped"}

    path, distance, nodes_explored = a_star(
        graph,
        start_node,
        destination_node,
        return_stats=True,
        traffic=traffic,
        blocked_segments=blocked_segments
    )

    if not path:
        return {"status": "stranded"}

    new_route = prefix + [
        {
            "latitude": graph.nodes[node]["latitude"],
            "longitude": graph.nodes[node]["longitude"]
        }
        for node in path
    ]

    vehicle.set_route(new_route)
    vehicle.reroutes += 1

    return {
        "status": "rerouted",
        "route": new_route,
        "distance": distance,
        "nodes_explored": nodes_explored
    }


def reroute_affected_vehicles(
    graph,
    simulation,
    node_by_point,
    only_affected=True
):
    """Reroute vehicles after a closure.

    only_affected=True  recalculate just the vehicles whose route uses a
                        closed road (the normal, fast way)
    only_affected=False recalculate every vehicle (only used by the
                        benchmark, to show how much work is saved)
    """

    started = time.perf_counter()

    blocked = simulation.get_blocked_segments()

    # Traffic is measured once and shared by every recalculation
    traffic = simulation.get_traffic_density()

    stats = {
        "vehicles_checked": 0,
        "affected": 0,
        "recalculations": 0,
        "rerouted": 0,
        "stranded": 0,
        "skipped": 0,
    }

    for vehicle in simulation.vehicles:

        if vehicle.finished or vehicle.position >= len(vehicle.route) - 1:
            continue

        stats["vehicles_checked"] += 1

        affected = route_uses_closed_road(vehicle, blocked)

        if affected:
            stats["affected"] += 1

        if not affected and only_affected:
            continue

        stats["recalculations"] += 1

        result = reroute_vehicle(
            graph,
            vehicle,
            node_by_point,
            traffic=traffic,
            blocked_segments=blocked
        )

        stats[result["status"]] += 1

    stats["seconds"] = time.perf_counter() - started

    return stats
