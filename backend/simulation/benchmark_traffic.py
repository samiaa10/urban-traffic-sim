"""
Benchmark: dynamic traffic on the real Norwich map
---------------------------------------------------

Run from the project root:

    python -m backend.simulation.benchmark_traffic
    python -m backend.simulation.benchmark_traffic --save

Four parts:

  1. Route search on Norwich: the old shortest-distance A*, the travel-time
     A* with a straight-line estimate, and with the landmark estimate.
  2. Scaling: for each fleet size, how long spawning takes, how long one
     simulated second takes with movement only and with dynamic rerouting,
     how many route searches rerouting does, and the extra memory used.
  3. An accident on a busy road: average journey time of the vehicles
     whose route used that road, with and without dynamic rerouting.
  4. Rerouting policies: journey times, route changes and search cost for
     no rerouting, the default policy, the default with traffic smoothing,
     and a more eager policy.

Timings depend on the machine; the report records which one.
"""

import argparse
import datetime
import pickle
import platform
import random
import statistics
import time
import tracemalloc
from collections import Counter
from pathlib import Path

from backend.routing.a_star import a_star
from backend.routing.travel_time import RoadNetwork
from backend.simulation.simulation import Simulation
from backend.simulation.road_model import make_road_model
from backend.simulation.traffic_manager import ReroutePolicy, TrafficManager

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GRAPH_FILE = PROJECT_ROOT / "data" / "osm" / "norwich_graph.pkl"
REPORT_FILE = PROJECT_ROOT / "docs" / "traffic_benchmark.md"

SEED = 42


# Traffic model of every simulation (--traffic-model), and for the osm
# model the shortest road length used for capacity (--min-road-length)
TRAFFIC_MODEL = "simple"
MIN_ROAD_LENGTH = None


def new_manager(network, policy):
    # The Simulation needs a first vehicle: a parked one, out of the way
    parked = network.point(min(network.main_component()))
    if TRAFFIC_MODEL == "osm" and MIN_ROAD_LENGTH is not None:
        from backend.simulation.road_model import OsmRoads

        road_model = OsmRoads(network, min_road_length=MIN_ROAD_LENGTH)
    else:
        road_model = make_road_model(TRAFFIC_MODEL, network)

    simulation = Simulation([parked, parked], road_model=road_model)
    return simulation, TrafficManager(network, simulation, policy)


# ------------------------------------------------------------
# 1. Route search
# ------------------------------------------------------------

def search_comparison(graph, network, searches=300):
    generator = random.Random(SEED)
    nodes = sorted(network.main_component())
    pairs = [tuple(generator.sample(nodes, 2)) for _ in range(searches)]

    # Congestion so travel-time searches have real work to do: a random
    # quarter of roads at 60% or 30% speed
    factors = {
        segment: generator.choice([1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.6, 0.3])
        for segment in set(network.segment.values())
    }

    rows = []

    def measure(label, run):
        times, explored = [], []
        for start, destination in pairs:
            started = time.perf_counter()
            explored.append(run(start, destination))
            times.append((time.perf_counter() - started) * 1000)
        rows.append((label, statistics.mean(times), statistics.median(times), statistics.mean(explored)))

    measure(
        "A* shortest distance (a_star.py, no traffic)",
        lambda s, d: a_star(graph, s, d, return_stats=True)[2],
    )
    measure(
        "A* fastest time, straight-line estimate (default)",
        lambda s, d: network.fastest_path(s, d, 13.9, factors.get)[2],
    )
    measure(
        "A* fastest time, landmark estimate",
        lambda s, d: network.fastest_path(s, d, 13.9, factors.get, heuristic="landmarks")[2],
    )

    straight, landmark = [], []
    for start, destination in pairs[:100]:
        _, seconds, _ = network.fastest_path(start, destination, 13.9)
        true_metres = seconds * 13.9
        straight.append(network.straight_line_metres(start, destination) / true_metres)
        landmark.append(network.lower_bound_metres(start, destination) / true_metres)

    return rows, statistics.mean(straight), statistics.mean(landmark)


# ------------------------------------------------------------
# 2. Scaling with fleet size
# ------------------------------------------------------------

MEMORY_STEPS = 60


def scaling_row(network, size, steps):
    """Timings and memory for one fleet size.

    Timing runs have memory tracing OFF: tracemalloc slows every allocation
    (route searches allocate a lot), which inflated an earlier version of
    these figures several times over. Memory is measured in a separate,
    shorter run with tracing on.
    """

    row = {"vehicles": size}

    for label, policy in (
        ("movement", ReroutePolicy(enabled=False)),
        ("dynamic", ReroutePolicy()),
    ):
        simulation, manager = new_manager(network, policy)

        outcome = manager.spawn_vehicles(size, random.Random(SEED))
        spawn_searches = manager.stats["searches"]

        step_ms = []
        for _ in range(steps):
            started = time.perf_counter()
            manager.step()
            step_ms.append((time.perf_counter() - started) * 1000)

        row[label] = {
            "spawn_seconds": outcome["seconds"],
            "spawned": outcome["spawned"],
            "ms_per_step": statistics.mean(step_ms),
            "worst_step_ms": max(step_ms),
            "searches_per_step": (manager.stats["searches"] - spawn_searches) / steps,
            "reroutes": manager.stats["reroutes"],
            "still_driving": sum(1 for v in simulation.vehicles[1:] if not v.finished),
        }

        print(f"  {size} vehicles, {label}: {row[label]['ms_per_step']:.1f} ms per step", flush=True)

    # Memory: Python allocations made by spawning the fleet and running
    # MEMORY_STEPS seconds of dynamic traffic (the road network itself is
    # shared and already built, so it is not counted)
    tracemalloc.start()
    baseline = tracemalloc.get_traced_memory()[0]
    simulation, manager = new_manager(network, ReroutePolicy())
    manager.spawn_vehicles(size, random.Random(SEED))
    for _ in range(MEMORY_STEPS):
        manager.step()
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    row["memory_mb"] = (current - baseline) / 1e6
    row["peak_memory_mb"] = (peak - baseline) / 1e6
    del simulation, manager

    return row


# ------------------------------------------------------------
# 3. An accident on a busy road
# ------------------------------------------------------------

def accident_scenario(network, vehicles, steps):
    """Severe accident on the road most of the fleet is about to use."""

    results = {}

    for label, policy in (
        ("without dynamic rerouting", ReroutePolicy(enabled=False)),
        ("with dynamic rerouting", ReroutePolicy()),
    ):
        simulation, manager = new_manager(network, policy)
        fleet = manager.spawn_vehicles(vehicles, random.Random(SEED))["vehicles"]

        # The busiest road on the fleet's planned routes (after the first),
        # among roads at least 200 m long: on a very short road even a
        # severe accident only costs a few seconds, below the threshold
        # for changing route
        usage = Counter()
        for vehicle in fleet:
            for a, b in zip(vehicle.route[1:], vehicle.route[2:]):
                start = network.node_at[(a["latitude"], a["longitude"])]
                end = network.node_at[(b["latitude"], b["longitude"])]
                if network.length[(start, end)] >= 200:
                    usage[network.segment[(start, end)]] += 1

        road, users = usage.most_common(1)[0]
        affected = [
            vehicle for vehicle in fleet
            if any(
                tuple(sorted(((a["latitude"], a["longitude"]), (b["latitude"], b["longitude"])))) == road
                for a, b in zip(vehicle.route, vehicle.route[1:])
            )
        ]

        simulation.add_accident(road, "severe", duration=steps)
        manager.process_events()

        for _ in range(steps):
            manager.step()

        def mean_journey(vehicles):
            done = [v.journey_seconds for v in vehicles if v.finished]
            return statistics.mean(done) if done else None

        results[label] = {
            "affected": len(affected),
            "finished": sum(1 for v in affected if v.finished),
            "mean_journey": mean_journey(affected),
            "fleet_mean_journey": mean_journey(fleet),
            "reroutes": manager.stats["reroutes"],
        }

    return results


# ------------------------------------------------------------
# 4. Rerouting policies compared
# ------------------------------------------------------------

POLICIES = [
    ("No dynamic rerouting", dict(enabled=False)),
    ("Default: check every 10 s, 60 s cooldown, save >= 20 s and 10%", dict()),
    ("Default + traffic averaged over 30 s", dict(smoothing_seconds=30)),
    ("Eager: 30 s cooldown, save >= 10 s and 5%",
     dict(cooldown_seconds=30, min_saving_seconds=10, min_saving_fraction=0.05)),
]


def policy_comparison(network, vehicles, steps, seeds):
    rows = []

    for label, settings in POLICIES:
        runs = []

        for seed in seeds:
            simulation, manager = new_manager(network, ReroutePolicy(**settings))
            fleet = manager.spawn_vehicles(vehicles, random.Random(seed))["vehicles"]
            spawn_searches = manager.stats["searches"]

            started = time.perf_counter()
            for _ in range(steps):
                manager.step()
            elapsed = time.perf_counter() - started

            done = [v.journey_seconds for v in fleet if v.finished]
            runs.append({
                "journey": statistics.mean(done) if done else None,
                "finished": len(done),
                "reroutes_per_vehicle": sum(v.reroutes for v in fleet) / len(fleet),
                "max_reroutes": max(v.reroutes for v in fleet),
                "searches": manager.stats["searches"] - spawn_searches,
                "skipped": manager.stats["skipped_no_possible_saving"],
                "ms_per_step": 1000 * elapsed / steps,
            })

        print(f"  {label}: done", flush=True)
        rows.append((label, runs))

    return rows


# ------------------------------------------------------------
# Report
# ------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Dynamic traffic benchmark")
    parser.add_argument("--sizes", type=int, nargs="+", default=[100, 500, 1000, 2000])
    parser.add_argument("--steps", type=int, default=300, help="simulated seconds per run")
    parser.add_argument("--accident-vehicles", type=int, default=1000)
    parser.add_argument("--accident-steps", type=int, default=1200)
    parser.add_argument("--policy-vehicles", type=int, default=600)
    parser.add_argument("--policy-steps", type=int, default=1200)
    parser.add_argument("--policy-seeds", type=int, nargs="+", default=[1, 2])
    parser.add_argument("--save", action="store_true", help="write docs/traffic_benchmark.md")
    parser.add_argument("--traffic-model", choices=["simple", "osm"], default="simple")
    parser.add_argument("--min-road-length", type=float, default=None)
    arguments = parser.parse_args()

    global TRAFFIC_MODEL, MIN_ROAD_LENGTH
    TRAFFIC_MODEL = arguments.traffic_model
    MIN_ROAD_LENGTH = arguments.min_road_length

    with open(GRAPH_FILE, "rb") as file:
        graph = pickle.load(file)

    started = time.perf_counter()
    network = RoadNetwork(graph)
    network_ms = (time.perf_counter() - started) * 1000

    started = time.perf_counter()
    network.use_landmarks(8)
    landmark_ms = (time.perf_counter() - started) * 1000

    print("Route search comparison...", flush=True)
    search_rows, straight_ratio, landmark_ratio = search_comparison(graph, network)

    print("Scaling...", flush=True)
    scaling = [scaling_row(network, size, arguments.steps) for size in arguments.sizes]

    print("Accident scenario...", flush=True)
    accident = accident_scenario(network, arguments.accident_vehicles, arguments.accident_steps)

    print("Rerouting policies...", flush=True)
    policies = policy_comparison(
        network, arguments.policy_vehicles, arguments.policy_steps, arguments.policy_seeds
    )

    lines = [
        "# Dynamic traffic benchmark",
        "",
        f"Run on {datetime.date.today()} with Python {platform.python_version()} "
        f"({platform.platform()}). Seed {SEED}. Reproduce with "
        f"`python -m backend.simulation.benchmark_traffic --sizes {' '.join(map(str, arguments.sizes))} "
        f"--steps {arguments.steps} --accident-vehicles {arguments.accident_vehicles} "
        f"--accident-steps {arguments.accident_steps} --traffic-model {arguments.traffic_model} --save`. "
        "Timings depend on the machine.",
        "",
        f"Norwich map: {len(graph.nodes):,} junctions, {len(network.length):,} directed roads, "
        f"{len(network.main_component()):,} junctions in the main connected area (trips are made there). "
        f"Building the road network took {network_ms:.0f} ms; 8 landmarks took {landmark_ms:.0f} ms.",
        "",
        "## 1. Route search (300 random trips, a random quarter of roads congested)",
        "",
        "| Search | Mean | Median | Junctions explored |",
        "|---|---|---|---|",
    ]

    for label, mean, median, explored in search_rows:
        lines.append(f"| {label} | {mean:.2f} ms | {median:.2f} ms | {explored:,.0f} |")

    lines += [
        "",
        f"Lower bound as a share of the true free-flow road distance (100 trips): "
        f"straight line {100 * straight_ratio:.1f}%, landmarks {100 * landmark_ratio:.1f}%. "
        "The tighter landmark bound is what lets the traffic manager skip route searches "
        "that could not save enough time.",
        "",
        f"## 2. Scaling with fleet size ({arguments.steps} simulated seconds after spawning)",
        "",
        "All vehicles set off together, so these seconds are the busiest of the run. "
        "Movement only: rerouting off (vehicles still drive in congestion and accidents). "
        "Dynamic: the default rerouting policy. Per simulated second: mean wall-clock time "
        "of one `TrafficManager.step()` (worst second in brackets), measured with memory "
        f"tracing off. Memory: Python allocations (tracemalloc) for spawning the fleet and "
        f"{MEMORY_STEPS} s of dynamic traffic, measured in a separate run; the road network is "
        "shared and not included.",
        "",
        "| Vehicles | Spawn (route every vehicle) | Movement only, per simulated s "
        "| Dynamic, per simulated s | Route searches per simulated s | Reroutes "
        "| Still driving at end | Memory held (peak) |",
        "|---|---|---|---|---|---|---|---|",
    ]

    for row in scaling:
        movement, dynamic = row["movement"], row["dynamic"]
        lines.append(
            f"| {row['vehicles']:,} | {dynamic['spawn_seconds']:.2f} s "
            f"| {movement['ms_per_step']:.1f} ms ({movement['worst_step_ms']:.0f}) "
            f"| {dynamic['ms_per_step']:.1f} ms ({dynamic['worst_step_ms']:.0f}) "
            f"| {dynamic['searches_per_step']:.1f} | {dynamic['reroutes']:,} "
            f"| {dynamic['still_driving']:,} "
            f"| {row['memory_mb']:.1f} MB ({row['peak_memory_mb']:.1f} MB) |"
        )

    without = accident["without dynamic rerouting"]
    with_ = accident["with dynamic rerouting"]

    def journey(value):
        return "n/a" if value is None else f"{value:.0f} s"

    lines += [
        "",
        f"## 3. Severe accident on the busiest road ({arguments.accident_vehicles:,} vehicles, "
        f"{arguments.accident_steps} simulated seconds)",
        "",
        "The accident starts as the fleet sets off, on the road the most planned routes use, "
        "and lasts the whole run.",
        "",
        "| | Vehicles whose route used the road | Of those, arrived | Their mean journey "
        "| Whole fleet mean journey | Reroutes |",
        "|---|---|---|---|---|---|",
    ]

    for label, result in (("Without dynamic rerouting", without), ("With dynamic rerouting", with_)):
        lines.append(
            f"| {label} | {result['affected']} | {result['finished']} "
            f"| {journey(result['mean_journey'])} | {journey(result['fleet_mean_journey'])} "
            f"| {result['reroutes']:,} |"
        )

    seeds = ", ".join(map(str, arguments.policy_seeds))

    lines += [
        "",
        f"## 4. Rerouting policies ({arguments.policy_vehicles:,} vehicles, "
        f"{arguments.policy_steps} simulated seconds, seeds {seeds})",
        "",
        "Each cell lists one value per seed. Mean journey is over vehicles that arrived. "
        "Searches and skipped searches exclude the routes planned when spawning.",
        "",
        "| Policy | Arrived | Mean journey | Route changes per vehicle | Most for one vehicle "
        "| Route searches | Searches skipped (no possible saving) | Per simulated s |",
        "|---|---|---|---|---|---|---|---|",
    ]

    def each(runs, field, fmt):
        return " / ".join("n/a" if run[field] is None else fmt.format(run[field]) for run in runs)

    for label, runs in policies:
        lines.append(
            f"| {label} | {each(runs, 'finished', '{:,}')} | {each(runs, 'journey', '{:.0f} s')} "
            f"| {each(runs, 'reroutes_per_vehicle', '{:.1f}')} | {each(runs, 'max_reroutes', '{}')} "
            f"| {each(runs, 'searches', '{:,}')} | {each(runs, 'skipped', '{:,}')} "
            f"| {each(runs, 'ms_per_step', '{:.0f} ms')} |"
        )

    report = "\n".join(lines) + "\n"
    print("\n" + report)

    if arguments.save:
        REPORT_FILE.write_text(report)
        print(f"Saved to {REPORT_FILE}")


if __name__ == "__main__":
    main()
