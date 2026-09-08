# Dijkstra algorithm that discovers the lowest-cost route

import heapq


def dijkstra(graph, start, destination, return_stats=False):
    distances = {
        node: float("inf")
        for node in graph.nodes
    }

    previous = {
        node: None
        for node in graph.nodes
    }

    distances[start] = 0

    priority_queue = [(0, start)]

    nodes_explored = 0

    while priority_queue:
        current_distance, current_node = heapq.heappop(
            priority_queue
        )

        if current_distance > distances[current_node]:
            continue

        nodes_explored += 1

        if current_node == destination:
            break

        for neighbour, weight in graph.get_neighbours(current_node):

            new_distance = current_distance + weight

            if new_distance < distances[neighbour]:
                distances[neighbour] = new_distance
                previous[neighbour] = current_node

                heapq.heappush(
                    priority_queue,
                    (new_distance, neighbour)
                )

    if distances[destination] == float("inf"):
        if return_stats:
            return [], float("inf"), nodes_explored

        return [], float("inf")

    path = []
    current = destination

    while current is not None:
        path.append(current)
        current = previous[current]

    path.reverse()

    if return_stats:
        return path, distances[destination], nodes_explored

    return path, distances[destination]