"""Testing the optimiser's best answers in full traffic simulations.

The station optimiser (station_optimiser.py) is fast because it estimates
response time as road distance / speed: no traffic, every vehicle always
free. That is a good screen but not the whole truth. This module takes the
optimiser's top candidates (single sites or sets of stations) and runs
each one as full scenarios (scenarios/runner.py, osm traffic model): real
trips at several demand levels and seeds, emergencies dispatched to the
vehicle that would arrive first, queues when every vehicle is busy,
vehicles driving back, and traffic giving way.

Fair comparison: for a given demand level and seed every candidate gets
exactly the same trips and the same emergencies (same seed, same streams;
only the stations differ). A matching "no emergencies" run per demand
level and seed measures what the emergency response itself costs the
rest of the traffic (network impact).

For each candidate and demand level the report gives, pooled over seeds:
the simulated mean, 90th percentile and worst response, the share within
the target, the network delay and how much the emergency response added
to it, each with a 95% confidence interval over the seeds (Student's t,
so few seeds give honest, wide intervals); and the optimiser's estimate
next to the simulated value. Candidates are ranked by simulated mean
response (averaged over demand levels), and Kendall's tau says how well
the optimiser's own order agreed with the simulation's.
"""

import math
import statistics

from backend.optimiser.station_optimiser import (
    dense_candidates,
    optimise_station_locations,
    optimise_station_set,
)
from backend.scenarios.spec import ScenarioSpec


# Two-sided 95% Student's t critical values by degrees of freedom
T_95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447,
        7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228, 15: 2.131, 20: 2.086, 30: 2.042}


def t_critical(df):
    if df < 1:
        return math.inf
    known = [d for d in T_95 if d <= df]
    return T_95[max(known)] if df <= 30 else 1.96


def mean_interval(values):
    """(mean, half-width of the 95% confidence interval) of a sample;
    the half-width is None with fewer than 2 values."""

    values = [v for v in values if v is not None]
    if not values:
        return None, None
    mean = statistics.mean(values)
    if len(values) < 2:
        return mean, None
    return mean, t_critical(len(values) - 1) * statistics.stdev(values) / math.sqrt(len(values))


def percentile(values, fraction):
    values = sorted(values)
    if not values:
        return None
    index = min(len(values) - 1, max(0, math.ceil(fraction * len(values)) - 1))
    return values[index]


def kendall_tau(order_a, order_b):
    """Kendall's tau between two rankings of the same items (1 = the same
    order, -1 = reversed)."""

    position_a = {item: i for i, item in enumerate(order_a)}
    position_b = {item: i for i, item in enumerate(order_b)}
    items = list(order_a)
    concordant = discordant = 0
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a = position_a[items[i]] - position_a[items[j]]
            b = position_b[items[i]] - position_b[items[j]]
            if a * b > 0:
                concordant += 1
            elif a * b < 0:
                discordant += 1
    pairs = len(items) * (len(items) - 1) / 2
    return (concordant - discordant) / pairs if pairs else None


# ------------------------------------------------------------
# Building the scenarios
# ------------------------------------------------------------

def _estimate(measured):
    return {
        "mean_response_seconds": measured.get("average_seconds"),
        "worst_response_seconds": measured.get("worst_seconds"),
        "within_target_percent": measured.get("within_target_percent"),
    }


def _station(item):
    return {"latitude": item["latitude"], "longitude": item["longitude"], "node": item["node"]}


def candidates_to_test(graph, settings, candidates=None):
    """The candidates to simulate: [{label, stations: [{latitude,
    longitude, node}], estimate: {mean, worst, within target}}].

    ranking: the optimiser's top `top` single sites.
    station_set: the optimised set, and for comparison the top `stations`
    sites ranked one by one and the best single site (each with the
    optimiser's own estimate for it).
    """

    points = candidates or dense_candidates(graph)
    common = dict(
        scenarios=settings["optimiser_scenarios"], seed=settings["optimiser_seed"],
        target_seconds=settings["target_seconds"], speed=settings["speed"],
        distribution=settings["distribution"],
    )
    ranking = optimise_station_locations(graph, points, **common)

    if settings["kind"] == "ranking":
        return [
            {"label": f"Site #{rank}", "stations": [_station(result)], "estimate": _estimate(result)}
            for rank, result in enumerate(ranking[: settings["top"]], start=1)
        ]

    count = settings["stations"]
    outcome = optimise_station_set(graph, points, count, **common)
    baselines = {b["name"]: b for b in outcome["baselines"]}

    return [
        {"label": f"Optimised {count} stations",
         "stations": [_station(pick) for pick in outcome["stations"]],
         "estimate": _estimate(outcome["result"])},
        {"label": f"Top {count} ranked one by one",
         "stations": [_station(result) for result in ranking[:count]],
         "estimate": _estimate(baselines["top_individual"])},
        {"label": "Best single station",
         "stations": [_station(ranking[0])],
         "estimate": _estimate(baselines["best_single"])},
    ][: settings["top"]]


def build_specs(settings, candidates):
    """The scenarios: for every demand level and seed, one run per
    candidate and one with no emergencies. Returns (specs, plan), where
    plan says which scenario position is which."""

    specs, plan = [], []

    for vehicles in settings["demand"]:
        for seed in settings["seeds"]:
            common = {
                "seed": seed,
                "demand": {"vehicles": vehicles, "spread_seconds": settings["spread_seconds"]},
                "traffic_model": "osm",
                "max_seconds": settings["max_seconds"],
            }
            plan.append({"candidate": None, "vehicles": vehicles, "seed": seed})
            specs.append(ScenarioSpec(name=f"No emergencies, {vehicles} vehicles, seed {seed}", **common))

            for index, candidate in enumerate(candidates):
                plan.append({"candidate": index, "vehicles": vehicles, "seed": seed})
                specs.append(ScenarioSpec(
                    name=f"{candidate['label']}, {vehicles} vehicles, seed {seed}",
                    stations=[
                        {"latitude": s["latitude"], "longitude": s["longitude"],
                         "service": settings["service"], "ambulances": settings["vehicles_per_station"],
                         "speed": settings["speed"]}
                        for s in candidate["stations"]
                    ],
                    emergencies={
                        "count": settings["emergencies"], "first_at": settings["first_emergency_at"],
                        "interval_seconds": settings["emergency_interval_seconds"],
                        "target_seconds": settings["target_seconds"],
                        "service": settings["service"], "distribution": settings["distribution"],
                    },
                    **common,
                ))

    return specs, plan


# ------------------------------------------------------------
# The report
# ------------------------------------------------------------

def _figure(mean_and_interval):
    mean, half = mean_and_interval
    return {"mean": mean, "ci95": half}


def summarise(settings, candidates, plan, results):
    """The evaluation report. results[i] is the result dict of scenario
    position i (None if it has not finished or failed)."""

    target = settings["target_seconds"]
    done = sum(1 for r in results if r is not None)

    baseline_delay = {
        (p["vehicles"], p["seed"]): results[i]["summary"]["total_delay_vehicle_hours"]
        for i, p in enumerate(plan) if p["candidate"] is None and results[i] is not None
    }

    rows = []
    for index, candidate in enumerate(candidates):
        by_demand = []
        for vehicles in settings["demand"]:
            runs = [
                (p["seed"], results[i]) for i, p in enumerate(plan)
                if p["candidate"] == index and p["vehicles"] == vehicles and results[i] is not None
            ]
            pooled, per_seed_mean, per_seed_within, delays, impacts = [], [], [], [], []
            unreached = 0
            for seed, result in runs:
                emergencies = result["emergencies"]
                detail = emergencies.get("detail") or {}
                responses = [r for r in detail.get("responses", []) if r is not None]
                pooled += responses
                unreached += emergencies["requested"] - len(responses)
                per_seed_mean.append(emergencies["mean_response_seconds"])
                per_seed_within.append(emergencies["within_target_percent"])
                delay = result["summary"]["total_delay_vehicle_hours"]
                delays.append(delay)
                if (vehicles, seed) in baseline_delay:
                    impacts.append(delay - baseline_delay[(vehicles, seed)])

            requested = settings["emergencies"] * len(runs)
            by_demand.append({
                "vehicles": vehicles,
                "runs": len(runs),
                "mean_response_seconds": _figure(mean_interval(per_seed_mean)),
                "pooled_mean_response_seconds": statistics.mean(pooled) if pooled else None,
                "p90_response_seconds": percentile(pooled, 0.9),
                "worst_response_seconds": max(pooled) if pooled else None,
                "within_target_percent": _figure(mean_interval(per_seed_within)),
                "pooled_within_target_percent": (
                    100 * sum(1 for r in pooled if r <= target) / requested if requested else None
                ),
                "unreached": unreached,
                "network_delay_vehicle_hours": _figure(mean_interval(delays)),
                "added_delay_vehicle_hours": _figure(mean_interval(impacts)),
            })

        simulated = [d["pooled_mean_response_seconds"] for d in by_demand]
        simulated_mean = statistics.mean(simulated) if simulated and None not in simulated else None
        estimate = candidate["estimate"]["mean_response_seconds"]
        rows.append({
            "label": candidate["label"],
            "stations": candidate["stations"],
            "estimate": candidate["estimate"],
            "by_demand": by_demand,
            "simulated_mean_response_seconds": simulated_mean,
            "estimate_gap_seconds": (
                simulated_mean - estimate if simulated_mean is not None and estimate is not None else None
            ),
            "estimate_gap_percent": (
                100 * (simulated_mean - estimate) / estimate
                if simulated_mean is not None and estimate else None
            ),
        })

    complete = done == len(results) and all(r["simulated_mean_response_seconds"] is not None for r in rows)
    estimated_order = sorted(range(len(rows)), key=lambda i: (rows[i]["estimate"]["mean_response_seconds"] or math.inf, i))
    simulated_order = sorted(range(len(rows)), key=lambda i: (rows[i]["simulated_mean_response_seconds"] or math.inf, i))
    for rank, i in enumerate(estimated_order, start=1):
        rows[i]["estimated_rank"] = rank
    for rank, i in enumerate(simulated_order, start=1):
        rows[i]["simulated_rank"] = rank

    tau = kendall_tau(estimated_order, simulated_order) if complete and len(rows) > 1 else None

    return {
        "scenarios_done": done,
        "scenarios_total": len(results),
        "complete": complete,
        "candidates": [rows[i] for i in simulated_order] if complete else rows,
        "rank_agreement_kendall_tau": tau,
        "explanation": explain(rows, simulated_order, tau, settings) if complete else [],
    }


def explain(rows, order, tau, settings):
    best = rows[order[0]]
    lines = [
        f"{best['label']} answers fastest in traffic: a mean response of "
        f"{best['simulated_mean_response_seconds'] / 60:.1f} min over "
        f"{len(settings['demand'])} demand level(s) and {len(settings['seeds'])} seed(s), "
        f"against {best['estimate']['mean_response_seconds'] / 60:.1f} min estimated without traffic."
    ]
    gaps = [r["estimate_gap_percent"] for r in rows if r["estimate_gap_percent"] is not None]
    if gaps:
        lines.append(
            f"Traffic, queues and dispatch made responses {min(gaps):+.0f}% to {max(gaps):+.0f}% "
            "slower than the optimiser's free-flow estimate."
        )
    if tau is not None:
        agreement = "the same order" if tau == 1 else (
            "much the same order" if tau >= 0.6 else "a different order")
        lines.append(
            f"The simulation ranked the candidates in {agreement} as the optimiser "
            f"(Kendall's tau {tau:.2f})."
        )
    if len(order) > 1:
        second = rows[order[1]]
        top_demand = best["by_demand"][-1]
        other = second["by_demand"][-1]
        a, b = top_demand["mean_response_seconds"], other["mean_response_seconds"]
        if a["ci95"] is not None and b["ci95"] is not None:
            overlap = (a["mean"] + a["ci95"]) >= (b["mean"] - b["ci95"])
            lines.append(
                f"At {top_demand['vehicles']} vehicles the 95% intervals of the top two "
                + ("overlap, so more seeds would be needed to be sure of the order." if overlap
                   else "do not overlap.")
            )
    return lines
