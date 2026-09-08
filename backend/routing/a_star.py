import heapq
from math import radians, sin, cos, sqrt, atan2


EARTH_RADIUS_M = 6_371_000


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


def a_star(graph, start, destination):
    distances = {
        node: float("inf")
        for node in graph.nodes
    }

    previous = {
        node: None
        for node in graph.nodes
    }

    distances[start] = 0

    priority_queue = [
        (heuristic(graph, start, destination), start)
    ]

    while priority_queue:
        _, current_node = heapq.heappop(priority_queue)

        if current_node == destination:
            break

        for neighbour, weight in graph.get_neighbours(current_node):

            new_distance = (
                distances[current_node] + weight
            )

            if new_distance < distances[neighbour]:
                distances[neighbour] = new_distance
                previous[neighbour] = current_node

                estimated_total_cost = (
                    new_distance
                    + heuristic(
                        graph,
                        neighbour,
                        destination
                    )
                )

                heapq.heappush(
                    priority_queue,
                    (estimated_total_cost, neighbour)
                )

    if distances[destination] == float("inf"):
        return [], float("inf")

    path = []

    current = destination

    while current is not None:
        path.append(current)
        current = previous[current]

    path.reverse()

    return path, distances[destination]