import pickle
import random
import time
from pathlib import Path

from backend.routing.dijkstra import dijkstra
from backend.routing.a_star import a_star

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GRAPH_FILE = PROJECT_ROOT / "data" / "osm" / "norwich_graph.pkl"


def load_graph():
    print("Loading Norwich graph...")

    with open(GRAPH_FILE, "rb") as file:
        graph = pickle.load(file)

    print(f"Nodes: {len(graph.nodes):,}")
    print(f"Edges: {sum(len(edges) for edges in graph.edges.values()):,}")

    return graph


def benchmark_algorithm(algorithm, graph, start, destination):
    start_time = time.perf_counter()

    path, distance, nodes_explored = algorithm(
        graph,
        start,
        destination,
        return_stats=True
    )

    end_time = time.perf_counter()

    runtime_ms = (end_time - start_time) * 1000

    return path, distance, nodes_explored, runtime_ms


def main():
    graph = load_graph()

    nodes = list(graph.nodes)

    tests = []

    while len(tests) < 100:
        start = random.choice(nodes)
        destination = random.choice(nodes)

        if start != destination:
            tests.append((start, destination))

    print("\nRunning 100 routing tests...")

    dijkstra_times = []
    a_star_times = []

    dijkstra_nodes = []
    a_star_nodes = []

    for start, destination in tests:

        dijkstra_path, dijkstra_distance, dijkstra_explored, dijkstra_runtime = (
            benchmark_algorithm(
                dijkstra,
                graph,
                start,
                destination
            )
        )

        a_star_path, a_star_distance, a_star_explored, a_star_runtime = (
            benchmark_algorithm(
                a_star,
                graph,
                start,
                destination
            )
        )

        if dijkstra_distance != a_star_distance:
            print("ERROR: Algorithms produced different distances!")
            return

        dijkstra_times.append(dijkstra_runtime)
        a_star_times.append(a_star_runtime)

        dijkstra_nodes.append(dijkstra_explored)
        a_star_nodes.append(a_star_explored)

    average_dijkstra_time = sum(dijkstra_times) / len(dijkstra_times)
    average_a_star_time = sum(a_star_times) / len(a_star_times)

    average_dijkstra_nodes = sum(dijkstra_nodes) / len(dijkstra_nodes)
    average_a_star_nodes = sum(a_star_nodes) / len(a_star_nodes)

    speedup = average_dijkstra_time / average_a_star_time

    node_reduction = (
        1 - (average_a_star_nodes / average_dijkstra_nodes)
    ) * 100

    print("\n========== BENCHMARK RESULTS ==========")

    print(f"Dijkstra average runtime: {average_dijkstra_time:.3f} ms")
    print(f"A* average runtime:       {average_a_star_time:.3f} ms")
    print(f"A* speedup:               {speedup:.2f}x")

    print()

    print(
        f"Dijkstra average nodes explored: "
        f"{average_dijkstra_nodes:,.1f}"
    )

    print(
        f"A* average nodes explored:       "
        f"{average_a_star_nodes:,.1f}"
    )

    print(
        f"A* node exploration reduction:   "
        f"{node_reduction:.1f}%"
    )

    print()

    print("Dijkstra routes tested:", len(dijkstra_times))
    print("A* routes tested:", len(a_star_times))

    print("\n✓ All routes produced the same optimal distance.")


if __name__ == "__main__":
    main()