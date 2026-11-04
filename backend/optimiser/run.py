"""
Run the Emergency Station Optimiser from the command line.

From the project root:

    python -m backend.optimiser.run
    python -m backend.optimiser.run --scenarios 1000 --target 600
    python -m backend.optimiser.run --candidate 52.63,1.29 --candidate 52.62,1.31
    python -m backend.optimiser.run --stations 3
"""

import argparse
import pickle
import time
from pathlib import Path

from backend.optimiser.station_optimiser import (
    dense_candidates,
    explain_station_set,
    optimise_station_locations,
    optimise_station_set,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GRAPH_FILE = PROJECT_ROOT / "data" / "osm" / "norwich_graph.pkl"


def parse_point(text):
    latitude, longitude = text.split(",")
    return float(latitude), float(longitude)


def format_minutes(seconds):
    return f"{seconds / 60:.1f} min" if seconds is not None else "n/a"


def print_station_set(outcome, scenarios, target_seconds):
    """Print the chosen set of stations and how it compares with baselines."""

    print(f"Chosen {outcome['count']} stations (picked in this order):\n")

    for pick in outcome["stations"]:
        print(
            f"  {pick['order']}. {pick['latitude']:.4f}, {pick['longitude']:.4f}"
            f"   average with stations 1-{pick['order']}: "
            f"{format_minutes(pick['average_seconds_after'])}"
        )

    print(f"\n{'':<42}{'Average':>10}{'Worst':>10}{'In target':>11}{'Unreached':>11}")

    rows = [("Chosen set", outcome["result"])] + [
        (baseline["label"], baseline) for baseline in outcome["baselines"]
    ]

    for label, row in rows:
        print(
            f"{label:<42}{format_minutes(row['average_seconds']):>10}"
            f"{format_minutes(row['worst_seconds']):>10}"
            f"{row['within_target_percent']:>10.1f}%"
            f"{row['unreachable']:>11.1f}"
        )

    print()

    for sentence in explain_station_set(outcome, scenarios, target_seconds):
        print(f"- {sentence}")


def main():
    parser = argparse.ArgumentParser(description="Emergency Station Optimiser")
    parser.add_argument("--stations", type=int, default=None,
                        help="choose this many stations that work well "
                             "together (default: rank single stations)")
    parser.add_argument("--scenarios", type=int, default=500)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--target", type=float, default=480,
                        help="target response time in seconds")
    parser.add_argument("--speed", type=float, default=20,
                        help="ambulance speed in metres per second")
    parser.add_argument("--candidate", action="append", type=parse_point,
                        help="candidate station as LAT,LON (repeat for more). "
                             "Default: about 40 locations spread over the map.")
    arguments = parser.parse_args()

    with open(GRAPH_FILE, "rb") as file:
        graph = pickle.load(file)

    candidates = arguments.candidate or dense_candidates(graph)

    started = time.perf_counter()

    if arguments.stations is not None:

        try:
            outcome = optimise_station_set(
                graph,
                candidates,
                arguments.stations,
                scenarios=arguments.scenarios,
                seed=arguments.seed,
                target_seconds=arguments.target,
                speed=arguments.speed,
            )
        except ValueError as error:
            parser.error(str(error))

        print(
            f"\n{outcome['candidates_considered']} candidate stations, "
            f"{arguments.scenarios} random emergencies "
            f"(seed {arguments.seed}), "
            f"target {arguments.target / 60:.1f} min\n"
        )
        print_station_set(outcome, arguments.scenarios, arguments.target)
        print(f"\nFinished in {time.perf_counter() - started:.2f} seconds.")
        return

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
        f"\n{len(results)} candidate stations, "
        f"{arguments.scenarios} random emergencies "
        f"(seed {arguments.seed}), "
        f"target {arguments.target / 60:.1f} min\n"
    )
    print(f"{'Rank':<5}{'Latitude':>10}{'Longitude':>11}"
          f"{'Average':>10}{'Worst':>10}{'In target':>11}{'Unreached':>11}")

    for result in results[:15]:
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

    if len(results) > 15:
        print(f"... and {len(results) - 15} lower-ranked candidates")

    print(f"\nFinished in {elapsed:.2f} seconds.")


if __name__ == "__main__":
    main()
