"""Checks of the osm traffic model on the real Norwich map (the acceptance
checks for keeping OpenStreetMap road attributes; docs/road_network.md).

    python -m backend.simulation.check_road_model

1. A* with speed limits (straight-line and landmark estimates) finds
   exactly the fastest route: the same cost as a plain Dijkstra on 1,000
   random pairs, with random per-direction traffic.
2. Planned and driven times agree: 200 random trips, one car each, driven
   second by second through the simulation.
3. What the extra work costs: the same 1,000 searches in the simple
   model, and in the osm model with plain Dijkstra, the straight-line
   estimate alone, and the travel-time landmark estimate (the default).
"""

import heapq
import math
import pickle
import random
import resource
import statistics
import sys
import time
from pathlib import Path

from backend.routing.travel_time import RoadNetwork
from backend.simulation import dispatch
from backend.simulation.road_model import CAR_TOP_SPEED, make_road_model
from backend.simulation.simulation import Simulation

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GRAPH_FILE = PROJECT_ROOT / "data" / "osm" / "norwich_graph.pkl"
SEED = 42


def dijkstra_seconds(network, start, destination, speed, factor):
    """Plain Dijkstra on the osm costs, written independently of
    fastest_path."""

    outgoing = {}
    for (a, b), length in network.length.items():
        outgoing.setdefault(a, []).append((b, length))

    best = {start: 0.0}
    queue = [(0.0, start)]
    while queue:
        seconds, node = heapq.heappop(queue)
        if node == destination:
            return seconds
        if seconds > best[node]:
            continue
        for neighbour, length in outgoing.get(node, ()):
            road_speed = min(speed, network.limit[(node, neighbour)])
            key = (network.position[node], network.position[neighbour])
            cost = seconds + length / (road_speed * factor(key))
            if cost < best.get(neighbour, math.inf):
                best[neighbour] = cost
                heapq.heappush(queue, (cost, neighbour))
    return math.inf


def main():
    with open(GRAPH_FILE, "rb") as file:
        graph = pickle.load(file)

    network = RoadNetwork(graph)
    network.use_landmarks(8)
    generator = random.Random(SEED)
    nodes = sorted(network.main_component())

    factors = {
        (network.position[a], network.position[b]): generator.choice([1.0, 1.0, 1.0, 0.8, 0.5, 0.2])
        for a, b in network.length
    }
    factor = lambda key: factors.get(key, 1.0)
    pairs = [tuple(generator.sample(nodes, 2)) for _ in range(1000)]

    # 1. Exactness
    mismatches = 0
    for start, destination in pairs:
        expected = dijkstra_seconds(network, start, destination, CAR_TOP_SPEED, factor)
        for heuristic in ("straight_line", "landmarks"):
            _, seconds, _ = network.fastest_path(
                start, destination, CAR_TOP_SPEED, factor,
                heuristic=heuristic, directed=True, limits=True,
            )
            if not math.isclose(seconds, expected, rel_tol=1e-9, abs_tol=1e-9):
                mismatches += 1
    print(f"1. A* with limits vs plain Dijkstra: {len(pairs)} pairs x 2 estimates, "
          f"{mismatches} mismatches")

    # 2. Planned vs driven
    model = make_road_model("osm", network)
    gaps, estimate_errors = [], []
    for start, destination in pairs[:200]:
        path, planned, _ = network.fastest_path(start, destination, CAR_TOP_SPEED, limits=True)
        simulation = Simulation(network.points(path), road_model=model)
        simulation.use_road_lengths(network.length_between_points)
        car = simulation.vehicles[0]
        car.speed = CAR_TOP_SPEED
        estimate_errors.append(abs(dispatch.remaining_seconds(simulation, car) - planned))
        while not car.finished:
            simulation.step()
        gaps.append(car.journey_seconds - planned)
    print(f"2. Planned vs driven, 200 trips: driven - planned from {min(gaps):.3f} s "
          f"to {max(gaps):.3f} s (whole-second clock: 0 <= gap < 1 expected); "
          f"largest ETA difference {max(estimate_errors):.2e} s")

    # 3. Search cost
    simple_factor = {}
    for (a, b), segment in network.segment.items():
        simple_factor[segment] = factors[(network.position[a], network.position[b])]

    def search_times(directed, limits):
        lookup = factor if directed else (lambda segment: simple_factor.get(segment, 1.0))
        times = []
        for start, destination in pairs:
            began = time.perf_counter()
            network.fastest_path(
                start, destination, CAR_TOP_SPEED if limits else 13.9, lookup,
                directed=directed, limits=limits,
            )
            times.append(time.perf_counter() - began)
        return times

    def plain_dijkstra_times():
        times = []
        for start, destination in pairs:
            began = time.perf_counter()
            dijkstra_seconds(network, start, destination, CAR_TOP_SPEED, factor)
            times.append(time.perf_counter() - began)
        return times

    def without_time_landmarks(run):
        saved = network._seconds_from_landmark
        network._seconds_from_landmark = []
        try:
            return run()
        finally:
            network._seconds_from_landmark = saved

    search_times(False, False)   # warm up
    rows = [
        ("simple model: A*, straight-line estimate", search_times(False, False)),
        ("osm model: plain Dijkstra (no estimate)", plain_dijkstra_times()),
        ("osm model: A*, straight-line estimate only", without_time_landmarks(lambda: search_times(True, True))),
        ("osm model: A*, travel-time landmarks (default)", search_times(True, True)),
    ]
    baseline = statistics.mean(rows[0][1])
    print(f"3. Search time, {len(pairs)} congested searches (same pairs and traffic):")
    for label, times in rows:
        print(f"   {label:48s} mean {statistics.mean(times) * 1000:5.2f} ms, "
              f"median {statistics.median(times) * 1000:5.2f} ms ({statistics.mean(times) / baseline:.2f}x)")

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(f"   Peak memory of this check: {peak / (1024 * 1024) if sys.platform == 'darwin' else peak / 1024:.0f} MB")


if __name__ == "__main__":
    main()
