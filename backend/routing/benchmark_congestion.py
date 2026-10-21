"""
Benchmark: shortest-path routing vs congestion-aware routing
------------------------------------------------------------

Run from the project root:

    python -m backend.routing.benchmark_congestion
    python -m backend.routing.benchmark_congestion --vehicles 3000 --save

What it does:

    1. Fill the map with traffic: a fleet of vehicles all heading for the
       city centre (like rush hour), driven by the real Simulation for a
       few seconds. The Simulation's own traffic density is the congestion.
    2. Take random journeys and route each one TWICE with the same A*
       search: once ignoring traffic (the plain shortest path) and once
       congestion-aware (every vehicle on a road adds a penalty).
    3. Measure both routes on the same traffic snapshot: distance, free-flow
       travel time, how many vehicles they pass, and how much search work
       each approach needed.

Only the cost function differs between the two routes, so any difference
in the results is down to congestion-awareness and nothing else.
"""

import argparse
import datetime
import functools
import platform
import random
from pathlib import Path

from backend.routing.a_star import (
    CONGESTION_PENALTY_METRES,
    a_star,
    get_segment,
)
from backend.routing.benchmark import benchmark_algorithm, load_graph
from backend.simulation.benchmark_rerouting import build_simulation
from backend.simulation.vehicle import Vehicle

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPORT_FILE = PROJECT_ROOT / "docs" / "congestion_benchmark.md"

CITY_CENTRE = (52.6309, 1.2974)
CENTRE_TARGETS = 30      # vehicles head for one of the 30 junctions nearest the centre
MIN_ROUTE_NODES = 10     # ignore very short trips when building the traffic

HEAVY_VEHICLES = 3       # same threshold the live map uses for "heavy" congestion
SPEED = Vehicle([]).speed   # metres per second: the simulation's default car speed

DEFAULT_VEHICLES = 2000
DEFAULT_JOURNEYS = 100
DEFAULT_WARMUP = 60
DEFAULT_SEED = 42


# ------------------------------------------------------------
# 1. Traffic
# ------------------------------------------------------------

def nearest_nodes(graph, point, count):
    """The `count` junctions closest to a (latitude, longitude) point."""

    latitude, longitude = point

    return sorted(
        graph.nodes,
        key=lambda node: (
            (graph.nodes[node]["latitude"] - latitude) ** 2
            + (graph.nodes[node]["longitude"] - longitude) ** 2
        )
    )[:count]


def build_rush_hour_traffic(graph, vehicles, warmup_seconds, generator):
    """Run the simulation with many vehicles heading for the centre.

    Returns the traffic density: {road segment: vehicles on it right now}.
    This is exactly what the live congestion map and the router use.
    """

    targets = nearest_nodes(graph, CITY_CENTRE, CENTRE_TARGETS)
    nodes = sorted(graph.nodes)

    routes = []
    attempts = 0

    while len(routes) < vehicles:

        attempts += 1

        if attempts > vehicles * 50:
            raise RuntimeError("Could not find enough routes to the centre")

        start = generator.choice(nodes)
        destination = generator.choice(targets)

        if start == destination:
            continue

        path, _ = a_star(graph, start, destination)

        if len(path) < MIN_ROUTE_NODES:
            continue

        routes.append([
            {
                "latitude": graph.nodes[node]["latitude"],
                "longitude": graph.nodes[node]["longitude"],
            }
            for node in path
        ])

    simulation = build_simulation(routes)

    for _ in range(warmup_seconds):
        simulation.step()

    return simulation.get_traffic_density()


def describe_traffic(traffic):
    """A few numbers so you can see how congested the scenario really was."""

    return {
        # Vehicles that have not yet finished their trip at the snapshot
        "vehicles_on_road": sum(traffic.values()),
        "roads": len(traffic),
        "busiest": max(traffic.values(), default=0),
        "heavy": sum(1 for count in traffic.values() if count >= HEAVY_VEHICLES),
    }


# ------------------------------------------------------------
# 2. Measuring one route
# ------------------------------------------------------------

def cheapest_road_length(graph, node_a, node_b):
    """Length in metres of the road from node_a to node_b."""

    return min(
        weight
        for neighbour, weight in graph.get_neighbours(node_a)
        if neighbour == node_b
    )


def describe_route(graph, path, traffic):
    """Measure a route against a traffic snapshot."""

    distance = 0.0
    vehicles = 0
    heavy_roads = 0

    for node_a, node_b in zip(path, path[1:]):

        distance += cheapest_road_length(graph, node_a, node_b)

        count = traffic.get(get_segment(graph, node_a, node_b), 0)

        vehicles += count

        if count >= HEAVY_VEHICLES:
            heavy_roads += 1

    return {
        "distance": distance,
        # Free-flow: the simulation does not slow vehicles down in traffic
        "seconds": distance / SPEED,
        "vehicles": vehicles,
        "heavy_roads": heavy_roads,
        # The cost the congestion-aware router tries to minimise
        "cost": distance + CONGESTION_PENALTY_METRES * vehicles,
    }


# ------------------------------------------------------------
# 3. Comparing the two approaches
# ------------------------------------------------------------

def compare_journey(graph, start, destination, traffic):
    """Route one journey both ways. Returns None if there is no route."""

    shortest_path, _, shortest_explored, shortest_ms = benchmark_algorithm(
        a_star, graph, start, destination
    )

    if not shortest_path:
        return None

    aware_path, _, aware_explored, aware_ms = benchmark_algorithm(
        functools.partial(a_star, traffic=traffic),
        graph, start, destination
    )

    return {
        "changed": shortest_path != aware_path,
        "shortest": describe_route(graph, shortest_path, traffic),
        "aware": describe_route(graph, aware_path, traffic),
        "shortest_explored": shortest_explored,
        "aware_explored": aware_explored,
        "shortest_ms": shortest_ms,
        "aware_ms": aware_ms,
    }


def compare_journeys(graph, journeys, traffic):
    rows = []

    for start, destination in journeys:
        row = compare_journey(graph, start, destination, traffic)

        if row is not None:
            rows.append(row)

    return rows


def make_journeys(graph, count, generator):
    nodes = sorted(graph.nodes)
    journeys = []

    while len(journeys) < count:
        start, destination = generator.sample(nodes, 2)
        journeys.append((start, destination))

    return journeys


def average(values):
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def summarise(rows):
    """Turn the per-journey rows into the averages the report shows."""

    changed = [row for row in rows if row["changed"]]

    summary = {"journeys": len(rows), "changed": len(changed)}

    for name in ("shortest", "aware"):
        for field in ("distance", "seconds", "vehicles", "heavy_roads"):
            summary[f"{name}_{field}"] = average(row[name][field] for row in rows)

        summary[f"{name}_explored"] = average(row[f"{name}_explored"] for row in rows)
        summary[f"{name}_ms"] = average(row[f"{name}_ms"] for row in rows)

    # The same numbers, for only the journeys where the route changed
    summary["changed_extra_metres"] = average(
        row["aware"]["distance"] - row["shortest"]["distance"] for row in changed
    )
    summary["changed_worst_extra_metres"] = max(
        (row["aware"]["distance"] - row["shortest"]["distance"] for row in changed),
        default=0.0
    )
    summary["changed_vehicles_before"] = average(
        row["shortest"]["vehicles"] for row in changed
    )
    summary["changed_vehicles_after"] = average(
        row["aware"]["vehicles"] for row in changed
    )

    # Both approaches are optimal for their own cost, so these must hold:
    #   the shortest route is never longer than the congestion-aware one,
    #   the congestion-aware route never costs more than the shortest one.
    summary["sanity_failures"] = sum(
        1 for row in rows
        if row["aware"]["distance"] < row["shortest"]["distance"] - 1e-6
        or row["aware"]["cost"] > row["shortest"]["cost"] + 1e-6
    )

    return summary


# ------------------------------------------------------------
# 4. The report
# ------------------------------------------------------------

def percent_change(before, after):
    if before == 0:
        return ""

    return f"{100 * (after - before) / before:+.1f}%"


def format_report(summary, traffic_stats, settings, graph_info):
    s = summary

    # How many vehicles were still driving when the snapshot was taken
    active = traffic_stats.get("vehicles_on_road")
    active_text = f"vehicles still driving: {active:,} | " if active is not None else ""

    # Random pairs with no route (one-way streets, disconnected map pieces) are
    # skipped, so say how many were drawn and how many were measured
    drawn = settings.get("journeys", s["journeys"])
    skipped = drawn - s["journeys"]

    if skipped > 0:
        journeys_line = (
            f"Journeys: {s['journeys']} measured, from {drawn} random "
            f"start/destination pairs (seed {settings['seed']}); "
            f"{skipped} had no route and were skipped"
        )
    else:
        journeys_line = (
            f"Journeys: {s['journeys']} random start/destination pairs "
            f"(seed {settings['seed']})"
        )

    def row(label, shortest, aware, decimals=0, show_change=True):
        change = percent_change(shortest, aware) if show_change else ""
        return (
            f"{label:<38}{shortest:>12,.{decimals}f}"
            f"{aware:>18,.{decimals}f}   {change}"
        ).rstrip()

    lines = [
        "NORWICH//SIM: shortest-path vs congestion-aware routing",
        "",
        f"Map:      {graph_info['nodes']:,} junctions, {graph_info['edges']:,} road segments",
        f"Traffic:  {settings['vehicles']:,} vehicles heading for the city centre, "
        f"snapshot after {settings['warmup']} simulated seconds",
        f"          {active_text}roads with traffic: {traffic_stats['roads']:,} | "
        f"busiest road: {traffic_stats['busiest']} vehicles | "
        f"heavy roads ({HEAVY_VEHICLES}+ vehicles): {traffic_stats['heavy']:,}",
        journeys_line,
        f"Routing:  same A* search both times. Congestion-aware adds "
        f"{CONGESTION_PENALTY_METRES} m of cost per vehicle on a road.",
        "",
        f"{'Average per journey':<38}{'Shortest':>12}{'Congestion-aware':>18}",
        row("Route distance (m)", s["shortest_distance"], s["aware_distance"]),
        row("Free-flow travel time (s)", s["shortest_seconds"], s["aware_seconds"]),
        row("Vehicles passed on the route", s["shortest_vehicles"],
            s["aware_vehicles"], decimals=1),
        row(f"Heavy roads on the route ({HEAVY_VEHICLES}+)", s["shortest_heavy_roads"],
            s["aware_heavy_roads"], decimals=2, show_change=False),
        row("Search work (nodes explored)", s["shortest_explored"], s["aware_explored"]),
        row("Search time (ms)", s["shortest_ms"], s["aware_ms"], decimals=2),
        "",
        "Free-flow time = distance / "
        f"{SPEED} m/s. The simulation does not slow vehicles in traffic, so this "
        "contains no congestion delay.",
        "Vehicles on a road are counted per road, with both directions together.",
        "",
    ]

    percentage = 100 * s["changed"] / s["journeys"] if s["journeys"] else 0

    lines.append(
        f"Journeys where congestion-awareness chose a different route: "
        f"{s['changed']} of {s['journeys']} ({percentage:.0f}%)"
    )

    if s["changed"]:

        before = s["changed_vehicles_before"]
        after = s["changed_vehicles_after"]
        avoided = before - after

        lines.append(
            f"  For those {s['changed']} journeys: "
            f"+{s['changed_extra_metres']:,.0f} m on average "
            f"(worst +{s['changed_worst_extra_metres']:,.0f} m), "
            f"about +{s['changed_extra_metres'] / SPEED:,.0f} s free-flow"
        )
        lines.append(
            f"  Vehicles passed: {before:.1f} -> {after:.1f} on average"
        )

        if avoided > 0:
            lines.append(
                f"  Trade-off: about {s['changed_extra_metres'] / avoided:,.0f} m "
                f"of extra driving for each vehicle avoided"
            )

    else:
        lines.append(
            "  The traffic was too light to be worth a detour. "
            "Try a bigger fleet, e.g. --vehicles 4000"
        )

    lines.append("")

    if s["sanity_failures"] == 0:
        lines.append(
            "Sanity check passed: the shortest route was never longer, and the "
            "congestion-aware route never cost more (as both are optimal)."
        )
    else:
        lines.append(
            f"WARNING: sanity check failed on {s['sanity_failures']} journeys."
        )

    return "\n".join(lines)


def save_report(report, settings):
    header = (
        "# Congestion-aware routing benchmark\n\n"
        f"Run on {datetime.date.today()} with Python {platform.python_version()} "
        f"({platform.platform()}). Reproduce with "
        f"`python -m backend.routing.benchmark_congestion "
        f"--vehicles {settings['vehicles']} --journeys {settings['journeys']} "
        f"--warmup {settings['warmup']} --seed {settings['seed']}`.\n\n"
    )

    REPORT_FILE.parent.mkdir(exist_ok=True)
    REPORT_FILE.write_text(header + "```\n" + report + "\n```\n")

    print(f"\nSaved to {REPORT_FILE}")


# ------------------------------------------------------------
# Command line
# ------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Compare shortest-path and congestion-aware routing"
    )
    parser.add_argument("--vehicles", type=int, default=DEFAULT_VEHICLES,
                        help="vehicles used to create traffic")
    parser.add_argument("--journeys", type=int, default=DEFAULT_JOURNEYS,
                        help="random journeys to route both ways")
    parser.add_argument("--warmup", type=int, default=DEFAULT_WARMUP,
                        help="simulated seconds before the traffic snapshot")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED,
                        help="same seed = same traffic and journeys")
    parser.add_argument("--save", action="store_true",
                        help="also save the report to docs/congestion_benchmark.md")
    arguments = parser.parse_args()

    graph = load_graph()

    print(f"\nBuilding traffic with {arguments.vehicles:,} vehicles...")
    traffic = build_rush_hour_traffic(
        graph,
        arguments.vehicles,
        arguments.warmup,
        random.Random(arguments.seed),
    )

    print(f"Routing {arguments.journeys} journeys both ways...")
    journeys = make_journeys(
        graph, arguments.journeys, random.Random(arguments.seed + 1)
    )
    rows = compare_journeys(graph, journeys, traffic)

    settings = {
        "vehicles": arguments.vehicles,
        "journeys": arguments.journeys,
        "warmup": arguments.warmup,
        "seed": arguments.seed,
    }
    graph_info = {
        "nodes": len(graph.nodes),
        "edges": sum(len(edges) for edges in graph.edges.values()),
    }

    report = format_report(
        summarise(rows), describe_traffic(traffic), settings, graph_info
    )

    print("\n" + report)

    if arguments.save:
        save_report(report, settings)


if __name__ == "__main__":
    main()
