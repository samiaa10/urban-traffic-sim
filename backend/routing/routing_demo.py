import time

from graph import Graph
from dijkstra import dijkstra
from a_star import a_star


def create_test_graph():
    graph = Graph()

    # Coordinates are deliberately different
    # so A* has meaningful geographic information.
    graph.add_node(1, 52.6309, 1.2974)
    graph.add_node(2, 52.6315, 1.3000)
    graph.add_node(3, 52.6295, 1.2990)
    graph.add_node(4, 52.6300, 1.3020)
    graph.add_node(5, 52.6285, 1.3040)

    graph.add_edge(1, 2, 500)
    graph.add_edge(1, 3, 200)
    graph.add_edge(3, 2, 100)
    graph.add_edge(2, 4, 300)
    graph.add_edge(3, 4, 1000)
    graph.add_edge(4, 5, 200)

    return graph


graph = create_test_graph()

start = 1
destination = 5


# -------------------------
# Dijkstra
# -------------------------

start_time = time.perf_counter()

dijkstra_path, dijkstra_distance = dijkstra(
    graph,
    start,
    destination
)

dijkstra_time = time.perf_counter() - start_time


# -------------------------
# A*
# -------------------------

start_time = time.perf_counter()

a_star_path, a_star_distance = a_star(
    graph,
    start,
    destination
)

a_star_time = time.perf_counter() - start_time


# -------------------------
# Results
# -------------------------

print("\n--- Routing Comparison ---")

print("Dijkstra:")
print("  Path:", dijkstra_path)
print("  Distance:", dijkstra_distance)
print("  Runtime:", dijkstra_time * 1000, "ms")

print("\nA*:")
print("  Path:", a_star_path)
print("  Distance:", a_star_distance)
print("  Runtime:", a_star_time * 1000, "ms")


# Both algorithms must find the same optimal distance.
assert dijkstra_distance == a_star_distance

# Both algorithms must find a route.
assert dijkstra_path
assert a_star_path

print("\n✓ Routing algorithms agree on the optimal route.")