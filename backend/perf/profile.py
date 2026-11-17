"""Profile the main workloads on the Norwich map with cProfile.

    python -m backend.perf.profile            print the hotspots
    python -m backend.perf.profile --save     also write docs/profiling.md

Workloads (osm traffic model unless said otherwise):

1. Dynamic traffic: 1,000 cars, 120 simulated seconds with rerouting.
2. Route searches: 500 fastest-route searches under that traffic.
3. The station optimiser: the default ranking (38 candidates, 500
   emergencies) and a 3-station set.
4. One scenario: 500 vehicles, to the end.

For each, the functions with the most time spent inside them (tottime)
and their share of the workload's total.
"""

import argparse
import cProfile
import datetime
import io
import pickle
import platform
import pstats
import random
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GRAPH_FILE = PROJECT_ROOT / "data" / "osm" / "norwich_graph.pkl"
REPORT_FILE = PROJECT_ROOT / "docs" / "profiling.md"
TOP = 8


def profile(label, work):
    profiler = cProfile.Profile()
    started = time.perf_counter()
    profiler.enable()
    work()
    profiler.disable()
    wall = time.perf_counter() - started

    stats = pstats.Stats(profiler, stream=io.StringIO())
    total = sum(entry[2] for entry in stats.stats.values())
    rows = sorted(stats.stats.items(), key=lambda item: -item[1][2])[:TOP]

    lines = [
        f"### {label}",
        "",
        f"{wall:.2f} s wall-clock with profiling on (profiling slows Python code, "
        "so compare shares, not seconds).",
        "",
        "| Function | Calls | Time inside | Share |",
        "|---|---|---|---|",
    ]
    for (filename, line, name), (_, calls, tottime, _, _) in rows:
        where = Path(filename).name if filename != "~" else "built-in"
        lines.append(
            f"| `{where}:{line} {name}` | {calls:,} | {tottime:.2f} s | {100 * tottime / total:.0f}% |"
        )
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args()

    from backend.optimiser.station_optimiser import optimise_station_locations, optimise_station_set
    from backend.routing.travel_time import RoadNetwork
    from backend.scenarios.runner import World, run_scenario
    from backend.scenarios.spec import ScenarioSpec
    from backend.simulation.road_model import CAR_TOP_SPEED, make_road_model
    from backend.simulation.simulation import Simulation
    from backend.simulation.traffic_manager import TrafficManager

    with open(GRAPH_FILE, "rb") as file:
        graph = pickle.load(file)

    network = RoadNetwork(graph)
    network.use_landmarks(8)
    sections = []

    parked = network.point(min(network.main_component()))
    simulation = Simulation([parked, parked], road_model=make_road_model("osm", network))
    manager = TrafficManager(network, simulation)
    manager.spawn_vehicles(1000, random.Random(42))

    def traffic():
        for _ in range(120):
            manager.step()

    sections.append(profile("1. Dynamic traffic (1,000 cars, 120 simulated s, osm model)", traffic))

    nodes = sorted(network.main_component())
    generator = random.Random(7)
    pairs = [tuple(generator.sample(nodes, 2)) for _ in range(500)]
    snapshot = manager.planning_traffic()

    def searches():
        for start, destination in pairs:
            manager.plan(start, destination, CAR_TOP_SPEED, snapshot)

    sections.append(profile("2. Route searches (500 under that traffic)", searches))

    from backend.optimiser.station_optimiser import dense_candidates

    def optimiser():
        candidates = dense_candidates(graph)
        optimise_station_locations(graph, candidates, scenarios=500)
        optimise_station_set(graph, candidates, 3, scenarios=500)

    sections.append(profile("3. Station optimiser (ranking + 3-station set)", optimiser))

    world = World(graph)
    spec = ScenarioSpec(name="profile", seed=3, demand={"vehicles": 500}, traffic_model="osm")

    sections.append(profile("4. One scenario (500 vehicles, to the end)", lambda: run_scenario(world, spec)))

    header = [
        "# Profiling",
        "",
        f"Run on {datetime.date.today()} with Python {platform.python_version()} "
        f"({platform.platform()}). Reproduce with `python -m backend.perf.profile --save`.",
        "",
    ]
    text = "\n\n".join(sections)
    print(text)

    if args.save:
        doc = REPORT_FILE.read_text() if REPORT_FILE.exists() else ""
        marker = "<!-- profile:end -->"
        notes = doc.split(marker, 1)[1] if marker in doc else ""
        REPORT_FILE.write_text("\n".join(header) + text + "\n\n" + marker + notes)
        print(f"\nSaved to {REPORT_FILE}")


if __name__ == "__main__":
    main()
