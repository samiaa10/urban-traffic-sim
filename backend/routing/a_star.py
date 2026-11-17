import heapq
from math import radians, sin, cos, sqrt, atan2


EARTH_RADIUS_M = 6_371_000

# Congestion-aware routing treats each vehicle on a road as if the road
# were this many metres longer, so a busy road looks "further away".
CONGESTION_PENALTY_METRES = 50


def heuristic(graph, node, destination):
    node_data = graph.nodes[node]
    destination_data = graph.nodes[destination]

    lat1 = radians(node_data["latitude"])
    lon1 = radians(node_data["longitude"])

    lat2 = radians(destination_data["latitude"])
    lon2 = radians(destination_data["longitude"])

    lat_difference = lat2 - lat1
    lon_difference = lon2 - lon1

    a = (
        sin(lat_difference / 2) ** 2
        + cos(lat1)
        * cos(lat2)
        * sin(lon_difference / 2) ** 2
    )

    c = 2 * atan2(sqrt(a), sqrt(1 - a))

    return EARTH_RADIUS_M * c


def get_segment(graph, node_a, node_b):
    segment = (
        (
            graph.nodes[node_a]["latitude"],
            graph.nodes[node_a]["longitude"]
        ),
        (
            graph.nodes[node_b]["latitude"],
            graph.nodes[node_b]["longitude"]
        )
    )

    return tuple(sorted(segment))


def a_star(
    graph,
    start,
    destination,
    return_stats=False,
    traffic=None,
    blocked_segments=None
):

    if blocked_segments is None:
        blocked_segments = set()

    infinity = float("inf")

    # Filled in as junctions are reached (a junction not in the table is
    # infinitely far), instead of a table over every junction per call
    distances = {start: 0}
    previous = {start: None}

    # Segment keys are only needed to check closures and count traffic
    needs_segments = bool(blocked_segments) or traffic is not None

    # heuristic() for this destination, with the destination's part worked
    # out once (the same arithmetic in the same order, so the same values)
    nodes = graph.nodes
    lat2 = radians(nodes[destination]["latitude"])
    lon2 = radians(nodes[destination]["longitude"])
    cos_lat2 = cos(lat2)

    def estimate(node):
        node_data = nodes[node]
        lat1 = radians(node_data["latitude"])
        lon1 = radians(node_data["longitude"])
        a = (
            sin((lat2 - lat1) / 2) ** 2
            + cos(lat1)
            * cos_lat2
            * sin((lon2 - lon1) / 2) ** 2
        )
        return EARTH_RADIUS_M * (2 * atan2(sqrt(a), sqrt(1 - a)))

    priority_queue = [
        (estimate(start), 0, start)
    ]

    nodes_explored = 0

    while priority_queue:

        _, current_distance, current_node = heapq.heappop(
            priority_queue
        )

        if current_distance > distances.get(current_node, infinity):
            continue

        nodes_explored += 1

        if current_node == destination:
            break

        for neighbour, weight in graph.get_neighbours(current_node):

            # --------------------------------
            # Check whether road is blocked
            # --------------------------------

            if needs_segments:
                segment = get_segment(
                    graph,
                    current_node,
                    neighbour
                )

                if segment in blocked_segments:
                    continue

            # --------------------------------
            # Congestion cost
            # --------------------------------

            congestion_cost = 0

            if traffic is not None:

                vehicle_count = traffic.get(
                    segment,
                    0
                )

                congestion_cost = (
                    vehicle_count * CONGESTION_PENALTY_METRES
                )

            # --------------------------------
            # Total routing cost
            # --------------------------------

            new_distance = (
                current_distance
                + weight
                + congestion_cost
            )

            if new_distance < distances.get(neighbour, infinity):

                distances[neighbour] = new_distance
                previous[neighbour] = current_node

                estimated_total_cost = (
                    new_distance
                    + estimate(neighbour)
                )

                heapq.heappush(
                    priority_queue,
                    (
                        estimated_total_cost,
                        new_distance,
                        neighbour
                    )
                )

    # --------------------------------
    # No route exists
    # --------------------------------

    if distances.get(destination, infinity) == infinity:

        if return_stats:
            return [], float("inf"), nodes_explored

        return [], float("inf")

    # --------------------------------
    # Reconstruct route
    # --------------------------------

    path = []
    current = destination

    while current is not None:

        path.append(current)
        current = previous[current]

    path.reverse()

    if return_stats:
        return path, distances[destination], nodes_explored

    return path, distances[destination]