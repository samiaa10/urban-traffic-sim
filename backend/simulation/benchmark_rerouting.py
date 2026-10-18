"""
Benchmark: how expensive is rerouting after a road closure?

Run from the project root:

    python -m backend.simulation.benchmark_rerouting

For each fleet size it closes the busiest road (the one the most
vehicles want to use) and compares two strategies:

    affected only  - recalculate routes just for vehicles that use that road
    recalculate all - recalculate every vehicle's route

It prints a table and saves it to docs/rerouting_benchmark.md.
"""

import datetime
import pickle
import platform
import random
from collections import Counter
from pathlib import Path

from backend.routing.a_star import a_star
from backend.simulation.rerouting import (
    build_node_lookup,
    reroute_affected_vehicles,
    segment_key,
)
from backend.simulation.simulation import Simulation

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GRAPH_FILE = PROJECT_ROOT / "data" / "osm" / "norwich_graph.pkl"
REPORT_FILE = PROJECT_ROOT / "docs" / "rerouting_benchmark.md"

FLEET_SIZES = [100, 500, 1000]
SEED = 42
REPEATS = 3


def make_routes(graph, count, generator):
    """Random journeys between junctions that are really connected."""

    nodes = sorted(graph.nodes)
    routes = []

    while len(routes) < count:

        start, destination = generator.sample(nodes, 2)
        path, _ = a_star(graph, start, destination)

        if len(path) < 10:
            continue

        routes.append([
            {
                "latitude": graph.nodes[node]["latitude"],
                "longitude": graph.nodes[node]["longitude"],
            }
            for node in path
        ])

    return routes


def build_simulation(routes):
    simulation = Simulation(routes[0])

    for route in routes[1:]:
        simulation.add_vehicle(route)

    return simulation


def busiest_segment(routes):
    usage = Counter()

    for route in routes:
        for a, b in zip(route, route[1:]):
            usage[segment_key(a, b)] += 1

    return usage.most_common(1)[0][0]


def main():
    with open(GRAPH_FILE, "rb") as file:
        graph = pickle.load(file)

    lookup = build_node_lookup(graph)
    generator = random.Random(SEED)

    rows = []

    for fleet in FLEET_SIZES:

        routes = make_routes(graph, fleet, generator)
        closed_road = busiest_segment(routes)

        results = {}

        for mode, only_affected in (("affected", True), ("all", False)):

            # Repeat and keep the fastest run, so a one-off slowdown on
            # the computer doesn't distort the result
            runs = []

            for _ in range(REPEATS):
                simulation = build_simulation(routes)
                simulation.block_segment(closed_road)

                runs.append(
                    reroute_affected_vehicles(
                        graph, simulation, lookup, only_affected=only_affected
                    )
                )

            results[mode] = min(runs, key=lambda run: run["seconds"])

        rows.append((fleet, results["affected"], results["all"]))

        print(f"finished {fleet} vehicles")

    lines = [
        "# Rerouting benchmark",
        "",
        f"Run on {datetime.date.today()} with Python {platform.python_version()} "
        f"({platform.platform()}). Random seed {SEED}. "
        f"Each timing is the fastest of {REPEATS} runs.",
        "",
        "The busiest road is closed. \"Affected only\" recalculates routes "
        "just for vehicles that use that road; \"recalculate all\" "
        "recalculates every vehicle.",
        "",
        "| Vehicles | Use the closed road | A* searches (affected only) | "
        "A* searches (all) | Time (affected only) | Time (all) | Speed-up |",
        "|---|---|---|---|---|---|---|",
    ]

    for fleet, smart, everything in rows:

        speedup = everything["seconds"] / smart["seconds"]

        lines.append(
            f"| {fleet} "
            f"| {smart['affected']} ({100 * smart['affected'] / fleet:.0f}%) "
            f"| {smart['recalculations']} "
            f"| {everything['recalculations']} "
            f"| {smart['seconds'] * 1000:.0f} ms "
            f"| {everything['seconds'] * 1000:.0f} ms "
            f"| {speedup:.1f}x |"
        )

    report = "\n".join(lines) + "\n"

    print("\n" + report)

    REPORT_FILE.parent.mkdir(exist_ok=True)
    REPORT_FILE.write_text(report)
    print(f"Saved to {REPORT_FILE}")


if __name__ == "__main__":
    main()
