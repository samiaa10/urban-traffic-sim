"""
Experiments: disruption and resilience on Norwich
-------------------------------------------------

Run from the project root:

    python -m backend.simulation.benchmark_resilience
    python -m backend.simulation.benchmark_resilience --save

--save writes docs/resilience_benchmark.md. Five seeds per scenario, fixed,
so the results repeat exactly (timings apart); figures are the mean with a
95% confidence interval (t distribution, 4 degrees of freedom).

Every scenario: the osm traffic model, 1,500 trips leaving over 30 minutes,
dynamic rerouting, 20 ambulance calls (one every 90 s from 1 minute) and
accessibility measured before, during and after (scenarios/resilience.py).
The disruptions start at 5 minutes:

  Normal              nothing happens
  Single closure      Prince of Wales Road's bridge (Foundry Bridge) out
                      of use for 20 minutes
  Multiple            four central bridges out for 20 minutes, plus two
                      severe accidents on the busiest roads and a closure
  Flood               every road within 350 m of a point on the Wensum by
                      Riverside closed for 20 minutes, then 10 minutes of
                      recovery (an illustrative area chosen by hand, not a
                      flood map)
  Flood + demand      the flood plus 60 extra trips a minute into the city
                      centre for 15 minutes

Then, under the flood: emergency stations (one central station; three
chosen by the existing optimiser for normal roads; three chosen by it with
the flooded roads closed), and emergency vehicle priority at the traffic
signals (adaptive signals with and without priority).
"""

import argparse
import datetime
import math
import pickle
import platform
import statistics
import time
from pathlib import Path

from backend.scenarios.runner import World, run_scenario
from backend.simulation.disruptions import segments_in_area

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GRAPH_FILE = PROJECT_ROOT / "data" / "osm" / "norwich_graph.pkl"
REPORT = PROJECT_ROOT / "docs" / "resilience_benchmark.md"
SEEDS = (1, 2, 3, 4, 5)
T_95 = 2.776   # t distribution, 4 degrees of freedom

CENTRE = {"latitude": 52.6286, "longitude": 1.2927}
FLOOD_AREA = {"latitude": 52.6305, "longitude": 1.3045, "radius_metres": 350}
CENTRAL_BRIDGES = ["Prince of Wales Road", "Whitefriars", "Fye Bridge Street", "Duke Street"]


def interval(values):
    values = [v for v in values if v is not None]
    if not values:
        return None, None
    mean = statistics.fmean(values)
    if len(values) < 2:
        return mean, None
    return mean, T_95 * statistics.stdev(values) / math.sqrt(len(values))


def fmt(pair, digits=0, unit=""):
    mean, half = pair
    if mean is None:
        return "n/a"
    text = f"{mean:,.{digits}f}{unit}"
    return text if half is None else f"{text} ± {half:,.{digits}f}"


def base_spec(seed, stations=None):
    return {
        "name": "resilience", "seed": seed, "traffic_model": "osm", "resilience": True,
        "demand": {"vehicles": 1500, "spread_seconds": 1800},
        "stations": stations or [{**CENTRE, "ambulances": 3}],
        "emergencies": {"count": 20, "first_at": 60, "interval_seconds": 90},
        "max_seconds": 5400,
    }


def flood():
    return {"area": FLOOD_AREA, "severity": "closed", "start_at": 300,
            "duration_seconds": 1200, "recovery_seconds": 600}


def figures(result, normal=None):
    summary = result["summary"]
    resilience = result["resilience"]
    during = resilience["accessibility"]["during"]
    emergency_during = resilience["emergency_access"]["during"].get("ambulance", {})
    trips = result["trips"]
    finished = [j for j, s in zip(trips["journey_seconds"], trips["status"]) if s == "finished"]
    increase = None
    if normal is not None and normal["trips"]["origin"] == trips["origin"][:len(normal["trips"]["origin"])]:
        # The same trips (same seed): each one's extra time against normal
        pairs = [
            (a, b) for a, b, sa, sb in zip(
                trips["journey_seconds"], normal["trips"]["journey_seconds"],
                trips["status"], normal["trips"]["status"],
            ) if sa == sb == "finished"
        ]
        increase = statistics.fmean(a - b for a, b in pairs) if pairs else None
    responses = [r for r in result["emergencies"]["detail"]["responses"] if r is not None]
    traffic_during = resilience["traffic"].get("during", {})
    return {
        "completed": 100 * summary["trips_finished"] / summary["trips_requested"],
        "unroutable": summary["trips_unroutable"],
        "mean_journey": statistics.fmean(finished) if finished else None,
        "worst_journey": max(finished, default=None),
        "increase": increase,
        "reachable": during["reachable_percent"],
        "access_increase": during["mean_increase_seconds"],
        "ambulance_access": emergency_during.get("within_target_percent"),
        "response": statistics.fmean(responses) if responses else None,
        "worst_response": max(responses, default=None),
        "reached": result["emergencies"]["arrived"],
        "throughput": traffic_during.get("arrivals_per_minute"),
        "speed": traffic_during.get("mean_speed_factor"),
        "recovery": resilience["recovery_seconds"],
        "areas": during["areas"],
        "wall": result["execution"]["wall_seconds"],
    }


def run_set(world, label, make, normals=None):
    runs = []
    for seed in SEEDS:
        result = run_scenario(world, make(seed))
        runs.append(figures(result, normals[seed] if normals else None))
        if normals is not None and label == "Normal":
            pass
        print(f"   {label} seed {seed}: {runs[-1]['wall']:.1f} s", flush=True)
    return {"label": label, "runs": runs}


def busiest_roads(world, count=3):
    result = run_scenario(world, {**base_spec(1), "resilience": False})
    roads = []
    for road in result["roads"]:
        pair = (road["start_node"], road["end_node"])
        if None not in pair and pair not in roads and pair[::-1] not in roads:
            roads.append(pair)
        if len(roads) == count:
            break
    return roads


def station_sets(world):
    """Three stations from the optimiser: for normal roads, and with the
    flooded roads closed. Each gets one ambulance."""
    from backend.optimiser.station_optimiser import dense_candidates, optimise_station_set

    candidates = dense_candidates(world.graph)
    flooded = set(segments_in_area(world.network, **FLOOD_AREA))
    sets = {}
    for label, blocked in (("normal", None), ("flood", flooded)):
        outcome = optimise_station_set(world.graph, candidates, 3, seed=1, blocked_segments=blocked)
        sets[label] = [
            {"latitude": s["latitude"], "longitude": s["longitude"], "ambulances": 1}
            for s in outcome["stations"]
        ]
    return sets


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--save", action="store_true")
    arguments = parser.parse_args()
    started = time.perf_counter()
    machine = f"{platform.machine()} / Python {platform.python_version()}"

    with open(GRAPH_FILE, "rb") as file:
        world = World(pickle.load(file))

    print("normal", flush=True)
    normal_results = {seed: run_scenario(world, base_spec(seed)) for seed in SEEDS}
    rows = [{"label": "Normal", "runs": [figures(normal_results[s], normal_results[s]) for s in SEEDS]}]

    busiest = busiest_roads(world)
    scenarios = [
        ("Single closure", lambda s: {**base_spec(s), "failures": [
            {"names": ["Prince of Wales Road"], "start_at": 300, "end_at": 1500}]}),
        ("Multiple disruptions", lambda s: {**base_spec(s),
            "failures": [{"names": CENTRAL_BRIDGES, "start_at": 300, "end_at": 1500}],
            "accidents": [{"start": a, "destination": b, "severity": "severe", "start_at": 300,
                           "duration_seconds": 1200} for a, b in busiest[:2]],
            "closures": [{"start": busiest[2][0], "destination": busiest[2][1], "start_at": 300, "end_at": 1500}]}),
        ("Flood", lambda s: {**base_spec(s), "floods": [flood()]}),
        ("Flood + extra demand", lambda s: {**base_spec(s), "floods": [flood()], "surges": [
            {**CENTRE, "radius_metres": 1000, "direction": "inbound", "vehicles_per_minute": 60,
             "start_at": 300, "duration_seconds": 900}]}),
    ]
    for label, make in scenarios:
        rows.append(run_set(world, label, make, normal_results))

    print("stations", flush=True)
    sets = station_sets(world)
    station_rows = [rows[3] | {"label": "One central station (3 ambulances)"}]
    for label, key in (("3 stations chosen for normal roads", "normal"),
                       ("3 stations chosen for the flooded roads", "flood")):
        station_rows.append(run_set(world, label, lambda s, key=key: {
            **base_spec(s, sets[key]), "floods": [flood()]}))

    print("priority", flush=True)
    priority_rows = []
    for label, priority in (("Adaptive signals", False), ("Adaptive signals + emergency priority", True)):
        priority_rows.append(run_set(world, label, lambda s, p=priority: {
            **base_spec(s), "floods": [flood()],
            "signals": {"controller": "adaptive", "emergency_priority": p}}))

    total = time.perf_counter() - started
    print(f"done in {total / 60:.1f} min", flush=True)
    if arguments.save:
        write_report(machine, rows, station_rows, priority_rows, sets, busiest, world, total)
        print(f"wrote {REPORT.relative_to(PROJECT_ROOT)}")


def column(rows, key):
    return {row["label"]: interval([r[key] for r in row["runs"]]) for row in rows}


def write_report(machine, rows, station_rows, priority_rows, sets, busiest, world, total):
    names = [world.graph.road_name(a, b) or f"{a}-{b}" for a, b in busiest]
    lines = [
        "# Disruption and resilience: experiments",
        "",
        f"Generated by `python -m backend.simulation.benchmark_resilience --save` on {machine}"
        f" ({datetime.date.today().isoformat()}), {total / 60:.0f} minutes in all. Seeds 1 to 5;"
        " each figure is the mean ± a 95% confidence interval over the five. See"
        " [RESILIENCE.md](RESILIENCE.md) for the model and what the measures mean.",
        "",
        "Every scenario: osm traffic model, 1,500 trips leaving over 30 minutes, dynamic rerouting,"
        " 20 ambulance calls from 1 minute (one every 90 s), one central station with 3 ambulances"
        " unless stated. Disruptions start at 5 minutes and last 20 (the flood then recovers over 10)."
        f" Busiest roads (from the normal run, seed 1): {', '.join(names)}.",
        "",
        "## Traffic",
        "",
        "| Scenario | Trips completed | Trips with no route | Mean journey (s) | Extra time per trip vs normal (s)"
        " | Worst journey (s) | Arrivals a minute, during | Mean speed during (of free flow) | Recovery after (s) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        r = row["runs"]
        lines.append(
            f"| {row['label']} | {fmt(interval([x['completed'] for x in r]), 1, '%')}"
            f" | {fmt(interval([x['unroutable'] for x in r]))} | {fmt(interval([x['mean_journey'] for x in r]))}"
            f" | {fmt(interval([x['increase'] for x in r]), 1)} | {fmt(interval([x['worst_journey'] for x in r]))}"
            f" | {fmt(interval([x['throughput'] for x in r]), 1)}"
            f" | {fmt(interval([None if x['speed'] is None else 100 * x['speed'] for x in r]), 1, '%')}"
            f" | {fmt(interval([x['recovery'] for x in r]))} |"
        )
    lines += [
        "",
        "## Accessibility and emergency response",
        "",
        "Accessibility: from every junction trips start at to 60 sampled destinations, at the speed"
        " limits around closed roads (not traffic), at the moment of greatest disruption. Ambulance"
        " access: the share of the 60 destinations the nearest station reaches within 7 minutes then.",
        "",
        "| Scenario | Junction pairs still connected | Mean extra drive time (s) | Ambulance access within 7 min"
        " | Ambulance response, mean (s) | Worst response (s) | Calls reached (of 20) |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        r = row["runs"]
        lines.append(
            f"| {row['label']} | {fmt(interval([x['reachable'] for x in r]), 1, '%')}"
            f" | {fmt(interval([x['access_increase'] for x in r]), 1)}"
            f" | {fmt(interval([x['ambulance_access'] for x in r]), 1, '%')}"
            f" | {fmt(interval([x['response'] for x in r]))} | {fmt(interval([x['worst_response'] for x in r]))}"
            f" | {fmt(interval([x['reached'] for x in r]), 1)} |"
        )
    lines += ["", "### Most affected areas (flood, seed 1)", "",
              "About 500 m squares; junction pairs cut off and the mean extra drive time from the area's junctions.", "",
              "| Area (south-west to north-east corner) | Junctions | Pairs cut off | Mean extra drive time (s) |",
              "|---|---|---|---|"]
    for area in rows[3]["runs"][0]["areas"]:
        b = area["bounds"]
        lines.append(
            f"| {b[0]:.4f}, {b[1]:.4f} to {b[2]:.4f}, {b[3]:.4f} | {area['junctions']} | {area['cut_off_pairs']:,}"
            f" | {fmt((area['mean_increase_seconds'], None), 1)} |"
        )
    lines += [
        "",
        "## Emergency stations under the flood",
        "",
        "The same flood; three ambulances in every case. The three-station sets come from the existing"
        " station optimiser (`optimise_station_set`, 200 sampled emergencies, seed 1): once for normal roads,"
        " once with the flooded roads closed.",
        "",
        "| Stations | Ambulance access within 7 min, during | Ambulance response, mean (s) | Worst response (s) | Calls reached |",
        "|---|---|---|---|---|",
    ]
    for row in station_rows:
        r = row["runs"]
        lines.append(
            f"| {row['label']} | {fmt(interval([x['ambulance_access'] for x in r]), 1, '%')}"
            f" | {fmt(interval([x['response'] for x in r]))} | {fmt(interval([x['worst_response'] for x in r]))}"
            f" | {fmt(interval([x['reached'] for x in r]), 1)} |"
        )
    for key, label in (("normal", "normal roads"), ("flood", "the flooded roads")):
        lines.append("")
        lines.append(f"Stations chosen for {label}: " + "; ".join(
            f"{s['latitude']:.4f}, {s['longitude']:.4f}" for s in sets[key]))
    lines += [
        "",
        "## Emergency priority at traffic signals, under the flood",
        "",
        "Adaptive signals at the junctions OpenStreetMap shows with lights, with and without priority"
        " for ambulances on a call (signals.py).",
        "",
        "| Signals | Ambulance response, mean (s) | Worst response (s) | Mean journey (s) |",
        "|---|---|---|---|",
    ]
    for row in priority_rows:
        r = row["runs"]
        lines.append(
            f"| {row['label']} | {fmt(interval([x['response'] for x in r]))}"
            f" | {fmt(interval([x['worst_response'] for x in r]))} | {fmt(interval([x['mean_journey'] for x in r]))} |"
        )
    lines += ["", "Runtime per run (s): " + "; ".join(
        f"{row['label']} {fmt(interval([x['wall'] for x in row['runs']]), 1)}"
        for row in rows + station_rows[1:] + priority_rows)]
    REPORT.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
