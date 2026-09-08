from graph import Graph
from dijkstra import dijkstra


graph = Graph()

# Create 5 locations
for node in range(1, 6):
    graph.add_node(node, 0, 0)


# Create roads between them
graph.add_edge(1, 2, 5)
graph.add_edge(1, 3, 2)
graph.add_edge(3, 2, 1)
graph.add_edge(2, 4, 3)
graph.add_edge(3, 4, 10)
graph.add_edge(4, 5, 2)


# Find the shortest route
path, distance = dijkstra(
    graph,
    1,
    5
)


print("Path:", path)
print("Distance:", distance)