# dijkstra algo that disocvers lowest cost route 

import heapq


def dijkstra(graph, start, destination):
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

    while priority_queue:
        current_distance, current_node = heapq.heappop(
            priority_queue
        )

        if current_node == destination:
            break

        if current_distance > distances[current_node]:
            continue

        for neighbour, weight in graph.get_neighbours(current_node):

            new_distance = current_distance + weight

            if new_distance < distances[neighbour]:
                distances[neighbour] = new_distance
                previous[neighbour] = current_node

                heapq.heappush(
                    priority_queue,
                    (new_distance, neighbour)
                )

    path = []

    current = destination

    while current is not None:
        path.append(current)
        current = previous[current]

    path.reverse()

    return path, distances[destination]