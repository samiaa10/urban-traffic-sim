"""
Audit tool: how good is the optimiser's pick compared with baselines?

Run from the project root:

    python -m backend.optimiser.evaluate
    python -m backend.optimiser.evaluate --seeds 10 --search 300

This only READS the optimiser (it calls the existing functions and changes
nothing), so it is safe to run at any time. It answers three questions:

  1. Is the optimiser's pick actually better than simple alternatives?
     (the city centre, the middle of the map, a typical random junction)
  2. How far is it from the best location we could find by trying many
     junctions? (a sample, so "best found", not a proof of the optimum)
  3. Would a different random seed give a different answer? (stability)

It also checks the several-station chooser: how close greedy picking, and
greedy picking followed by swaps, get to the best possible set found by
trying EVERY set (only practical for 2 and 3 stations).

All stations are compared on the SAME demand: every junction that all the
compared stations can reach, each counted once. That is exact (no random
sampling) and it never flatters a station that can't reach part of the map.
"""

import argparse
import pickle
import random
import statistics
from collections import Counter
from pathlib import Path

from backend.optimiser.station_optimiser import (
    dense_candidates,
    exhaustive_selection,
    greedy_selection,
    measure_set,
    optimise_station_locations,
    prepare_stations,
    refine_by_swapping,
    response_table,
    sample_emergencies,
    set_score,
    shortest_distances,
    snap_to_road,
    unreachable_penalty,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GRAPH = PROJECT_ROOT / "data" / "osm" / "norwich_graph.pkl"

CITY_CENTRE = (52.6309, 1.2974)
SPEED = 20            # metres per second, same default as the optimiser
TARGET_SECONDS = 480  # 8 minutes, same default as the optimiser


def measure(distances, demand):
    """Mean, 95th percentile and % within target, over the demand junctions."""

    times = sorted(distances[node] / SPEED for node in demand)

    return {
        "mean": statistics.mean(times),
        "p95": times[int(0.95 * (len(times) - 1))],
        "within": 100 * sum(1 for t in times if t <= TARGET_SECONDS) / len(times),
    }


def row(label, stats, note=""):
    return (
        f"{label:<34}{stats['mean'] / 60:>7.2f} min"
        f"{stats['p95'] / 60:>9.2f} min{stats['within']:>9.1f}%  {note}"
    ).rstrip()


def main():
    parser = argparse.ArgumentParser(description="Evaluate the station optimiser")
    parser.add_argument("--graph", type=Path, default=DEFAULT_GRAPH)
    parser.add_argument("--scenarios", type=int, default=500,
                        help="random emergencies per optimiser run")
    parser.add_argument("--seeds", type=int, default=10,
                        help="how many seeds to test the optimiser's stability with")
    parser.add_argument("--search", type=int, default=200,
                        help="random junctions to try as stations for the baselines")
    arguments = parser.parse_args()

    with open(arguments.graph, "rb") as file:
        graph = pickle.load(file)

    # --- The optimiser's own answer (default seed), on its dense grid -------
    candidates = dense_candidates(graph)
    ranked = optimise_station_locations(
        graph, candidates, scenarios=arguments.scenarios, seed=1
    )
    picked_node = ranked[0]["node"]

    stations = {}      # label -> junction
    for result in ranked:
        stations[f"grid candidate (optimiser rank {result['rank']})"] = result["node"]

    candidate_count = len(ranked)

    latitudes = [data["latitude"] for data in graph.nodes.values()]
    longitudes = [data["longitude"] for data in graph.nodes.values()]
    stations["baseline: city centre"] = snap_to_road(graph, *CITY_CENTRE)
    stations["baseline: middle of the map"] = snap_to_road(
        graph, statistics.mean(latitudes), statistics.mean(longitudes)
    )

    maps = {label: shortest_distances(graph, node) for label, node in stations.items()}

    # --- Shared demand: junctions every compared station can reach ----------
    demand = set(graph.nodes)
    for distances in maps.values():
        demand &= distances.keys()

    print(f"Graph: {len(graph.nodes):,} junctions. Demand: the {len(demand):,} "
          f"junctions that all {len(maps)} compared stations can reach.\n")
    print(f"{'Station':<34}{'Mean':>11}{'95th pct':>13}{'In 8 min':>9}")

    # Print the baselines and the top 3 candidates (there can be ~40)
    shown = [
        (label, distances) for label, distances in maps.items()
        if not label.startswith("grid candidate")
        or int(label.split("rank ")[1].rstrip(")")) <= 3
    ]

    for label, distances in sorted(shown, key=lambda item: measure(item[1], demand)["mean"]):
        note = "<- optimiser's pick" if label.endswith("rank 1)") else ""
        print(row(label, measure(distances, demand), note))

    print(f"(showing the top 3 of {candidate_count} grid candidates)")

    # --- Random junctions: a "typical" baseline and a best-found reference --
    generator = random.Random(1)
    pool = sorted(graph.nodes)
    means, skipped = [], 0

    for node in generator.sample(pool, min(arguments.search, len(pool))):
        distances = shortest_distances(graph, node)

        if not demand <= distances.keys():
            skipped += 1      # can't reach all of the demand: not comparable
            continue

        means.append(measure(distances, demand)["mean"])

    picked = measure(maps[next(l for l, n in stations.items() if n == picked_node)], demand)["mean"]

    print(f"\nRandom junctions tried as stations: {len(means)} "
          f"({skipped} skipped: could not reach all demand)")
    print(f"  typical random junction, mean response: {statistics.mean(means) / 60:.2f} min")
    print(f"  best random junction found:             {min(means) / 60:.2f} min")
    print(f"  optimiser's pick vs typical:            "
          f"{100 * (picked - statistics.mean(means)) / statistics.mean(means):+.1f}%")
    print(f"  optimiser's pick vs best found:         "
          f"{100 * (picked - min(means)) / min(means):+.1f}%  "
          f"(positive: a better junction exists than the grid candidates)")

    # --- Stability across seeds ---------------------------------------------
    exact_best = min(
        (n for l, n in stations.items() if l.startswith("grid candidate")),
        key=lambda n: measure(shortest_distances(graph, n), demand)["mean"],
    )

    tops = [
        optimise_station_locations(
            graph, candidates, scenarios=arguments.scenarios, seed=seed
        )[0]["node"]
        for seed in range(1, arguments.seeds + 1)
    ]
    counts = Counter(tops)

    print(f"\nStability over {arguments.seeds} seeds ({arguments.scenarios} random emergencies each):")
    print(f"  different stations picked as rank 1: {len(counts)}")
    print(f"  most common pick won {counts.most_common(1)[0][1]} of {arguments.seeds} runs")
    print(f"  the exact-demand best grid candidate won {counts[exact_best]} of {arguments.seeds} runs")

    # --- Several stations: greedy vs swaps vs trying every set --------------
    stations_table = prepare_stations(graph, candidates)
    emergencies = sample_emergencies(stations_table, arguments.scenarios, 1)
    times = response_table(stations_table, emergencies, SPEED)
    penalty = unreachable_penalty(times)

    def average_minutes(members):
        return measure_set(times, members, penalty, TARGET_SECONDS)["ranking_seconds"] / 60

    print(f"\nSeveral stations ({len(stations_table)} candidates, "
          f"{arguments.scenarios} emergencies): mean response, unreached emergencies "
          f"counted as the slowest response")
    print(f"  {'stations':<10}{'greedy':>9}{'+ swaps':>9}{'best possible':>15}  swaps reach the best?")

    for count in (2, 3):
        greedy = greedy_selection(times, count, penalty)
        swapped = refine_by_swapping(times, greedy, penalty)
        best = exhaustive_selection(times, count, penalty)

        print(f"  {count:<10}{average_minutes(greedy):>6.2f} min"
              f"{average_minutes(swapped):>6.2f} min{average_minutes(best):>12.2f} min"
              f"  {'yes' if set_score(times, swapped, penalty) == set_score(times, best, penalty) else 'no'}")


if __name__ == "__main__":
    main()
