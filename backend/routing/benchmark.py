import pickle
import random
import time

from dijkstra import dijkstra
from a_star import a_star


from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GRAPH_FILE = PROJECT_ROOT / "data" / "osm" / "norwich_graph.pkl"

NUM_TESTS = 100
RANDOM_SEED = 42


def load_graph():
    print("Loading Norwich graph...")

    with open(GRAPH_FILE, "rb") as file:
        graph = pickle.load(file)

    print(f"Nodes: {len(graph.nodes):,}")
    print(f"Edges: {sum(len(edges) for edges in graph.edges.values()):,}")

    return graph


def generate_test_pairs(graph):
    random.seed(RANDOM_SEED)

    nodes = list(graph.nodes.keys())

    pairs = []

    while len(pairs) < NUM_TESTS:
        start = random.choice(nodes)
        destination = random.choice(nodes)

        if start != destination:
            pairs.append((start, destination))

    return pairs


def benchmark(graph, pairs):
    dijkstra_times = []
    a_star_times = []

    dijkstra_distances = []
    a_star_distances = []

    print(f"\nRunning {NUM_TESTS} routing tests...\n")

    for start, destination in pairs:

        start_time = time.perf_counter()

        dijkstra_path, dijkstra_distance = dijkstra(
            graph,
            start,
            destination
        )

        dijkstra_time = time.perf_counter() - start_time

        start_time = time.perf_counter()

        a_star_path, a_star_distance = a_star(
            graph,
            start,
            destination
        )

        a_star_time = time.perf_counter() - start_time

        dijkstra_times.append(dijkstra_time)
        a_star_times.append(a_star_time)

        dijkstra_distances.append(dijkstra_distance)
        a_star_distances.append(a_star_distance)

    return (
        dijkstra_times,
        a_star_times,
        dijkstra_distances,
        a_star_distances
    )


def print_results(
    dijkstra_times,
    a_star_times,
    dijkstra_distances,
    a_star_distances
):
    dijkstra_average = sum(dijkstra_times) / len(dijkstra_times)
    a_star_average = sum(a_star_times) / len(a_star_times)

    print("========== BENCHMARK RESULTS ==========")

    print(
        f"Dijkstra average: "
        f"{dijkstra_average * 1000:.3f} ms"
    )

    print(
        f"A* average:       "
        f"{a_star_average * 1000:.3f} ms"
    )

    speedup = dijkstra_average / a_star_average

    print(
        f"A* speedup:       "
        f"{speedup:.2f}x"
    )

    print(
        f"\nDijkstra routes tested: "
        f"{len(dijkstra_distances)}"
    )

    print(
        f"A* routes tested:       "
        f"{len(a_star_distances)}"
    )

    for dijkstra_distance, a_star_distance in zip(
        dijkstra_distances,
        a_star_distances
    ):
        if dijkstra_distance != a_star_distance:
            print("\nWARNING: Route distances do not match!")
            return

    print("\n✓ All routes produced the same optimal distance.")


def main():
    graph = load_graph()

    pairs = generate_test_pairs(graph)

    (
        dijkstra_times,
        a_star_times,
        dijkstra_distances,
        a_star_distances
    ) = benchmark(graph, pairs)

    print_results(
        dijkstra_times,
        a_star_times,
        dijkstra_distances,
        a_star_distances
    )


if __name__ == "__main__":
    main()