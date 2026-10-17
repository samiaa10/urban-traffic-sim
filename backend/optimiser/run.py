"""
Run the Emergency Station Optimiser from the command line.

From the project root:

    python -m backend.optimiser.run
    python -m backend.optimiser.run --scenarios 1000 --target 600
    python -m backend.optimiser.run --candidate 52.63,1.29 --candidate 52.62,1.31
"""

import argparse
import pickle
import time
from pathlib import Path

from backend.optimiser.station_optimiser import (
    grid_candidates,
    optimise_station_locations,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GRAPH_FILE = PROJECT_ROOT / "data" / "osm" / "norwich_graph.pkl"


def parse_point(text):
    latitude, longitude = text.split(",")
    return float(latitude), float(longitude)


def main():
    parser = argparse.ArgumentParser(description="Emergency Station Optimiser")
    parser.add_argument("--scenarios", type=int, default=500)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--target", type=float, default=480,
                        help="target response time in seconds")
    parser.add_argument("--speed", type=float, default=20,
                        help="ambulance speed in metres per second")
    parser.add_argument("--candidate", action="append", type=parse_point,
                        help="candidate station as LAT,LON (repeat for more). "
                             "Default: a 3x3 grid over the map.")
    arguments = parser.parse_args()

    with open(GRAPH_FILE, "rb") as file:
        graph = pickle.load(file)

    candidates = arguments.candidate or grid_candidates(graph)

    started = time.perf_counter()

    results = optimise_station_locations(
        graph,
        candidates,
        scenarios=arguments.scenarios,
        seed=arguments.seed,
        target_seconds=arguments.target,
        speed=arguments.speed,
    )

    elapsed = time.perf_counter() - started

    print(
        f"\n{len(candidates)} candidate stations, "
        f"{arguments.scenarios} random emergencies "
        f"(seed {arguments.seed}), "
        f"target {arguments.target / 60:.1f} min\n"
    )
    print(f"{'Rank':<5}{'Latitude':>10}{'Longitude':>11}"
          f"{'Average':>10}{'Worst':>10}{'In target':>11}{'Unreached':>11}")

    for result in results:
        average = (
            f"{result['average_seconds'] / 60:.1f} min"
            if result["average_seconds"] is not None else "n/a"
        )
        worst = (
            f"{result['worst_seconds'] / 60:.1f} min"
            if result["worst_seconds"] is not None else "n/a"
        )
        print(
            f"{result['rank']:<5}{result['latitude']:>10.4f}"
            f"{result['longitude']:>11.4f}{average:>10}{worst:>10}"
            f"{result['within_target_percent']:>10.0f}%"
            f"{result['unreachable']:>11}"
        )

    print(f"\nFinished in {elapsed:.2f} seconds.")


if __name__ == "__main__":
    main()
