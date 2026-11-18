"""
Experiments: traffic signals (signals.py)
-----------------------------------------

Run from the project root:

    python -m backend.simulation.benchmark_signals
    python -m backend.simulation.benchmark_signals --save

--save writes docs/signals_benchmark.md. Fixed seeds throughout, so the
results repeat exactly (timings apart).

  1. One crossroads (200 m arms, one lane, 30 mph), five minutes of
     arrivals then five to clear, balanced and unbalanced demand, in all
     three traffic models: no signals, fixed time, adaptive.
  2. Norwich, the 121 junctions OpenStreetMap shows with traffic lights
     (108 get signals; the rest have only one road in):
     1,000 trips and 15 ambulance calls from one central station, the osm
     and ca models, no signals / fixed time / adaptive / adaptive with
     emergency priority, three seeds each.
"""

import argparse
import datetime
import pickle
import platform
import random
import statistics
import time
from pathlib import Path

from backend.routing.travel_time import RoadNetwork
from backend.scenarios.runner import World, run_scenario
from backend.simulation.road_model import CAR_TOP_SPEED, make_road_model
from backend.simulation.signals import ADAPTIVE, FIXED, make_signals
from backend.simulation.simulation import Simulation

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GRAPH_FILE = PROJECT_ROOT / "data" / "osm" / "norwich_graph.pkl"
REPORT = PROJECT_ROOT / "docs" / "signals_benchmark.md"
SEEDS = (1, 2, 3)

CONFIGS = (
    ("No signals", None),
    ("Fixed time", {"controller": "fixed"}),
    ("Adaptive", {"controller": "adaptive"}),
    ("Adaptive + priority", {"controller": "adaptive", "emergency_priority": True}),
)


def mean(values):
    values = [v for v in values if v is not None]
    return statistics.fmean(values) if values else None


def fmt(value, digits=0):
    return "n/a" if value is None else f"{value:,.{digits}f}"


# ----------------------------------------------------------------------
# 1. One crossroads
# ----------------------------------------------------------------------

DEMANDS = {
    # vehicles per second arriving from each arm (N, E, S, W)
    "balanced": (0.08, 0.08, 0.08, 0.08),
    "unbalanced": (0.20, 0.03, 0.20, 0.03),
}


def crossroads_run(model, controller, demand, seed):
    from backend.tests.test_signals import crossroads

    network = RoadNetwork(crossroads())
    simulation = Simulation(
        [network.point(1), network.point(1)], road_model=make_road_model(model, network, seed=seed)
    )
    simulation.vehicles[0].finished = True
    simulation.use_road_lengths(network.length_between_points)
    simulation.signals = make_signals(network, [0], controller) if controller else None

    generator = random.Random(seed)
    arms = (1, 2, 3, 4)
    cars = []
    started = time.perf_counter()
    for second in range(600):
        if second < 300:
            for arm, rate in zip(arms, DEMANDS[demand]):
                if generator.random() < rate:
                    exit_ = generator.choice([a for a in arms if a != arm])
                    cars.append(simulation.add_vehicle(network.points([arm, 0, exit_]), speed=CAR_TOP_SPEED))
        simulation.step()
    wall = time.perf_counter() - started

    done = [c.journey_seconds for c in cars if c.finished]
    signals = simulation.signals
    return {
        "cars": len(cars),
        "finished": len(done),
        "mean_journey": mean(done),
        "worst_journey": max(done, default=None),
        "mean_wait": mean([c.signal_wait_seconds for c in cars]),
        "max_queue": signals.junctions[0].max_queue if signals else 0,
        "wall": wall,
    }


def part1_crossroads():
    rows = []
    for demand in DEMANDS:
        for model in ("simple", "osm", "ca"):
            for label, controller in (("No signals", None), ("Fixed time", FIXED), ("Adaptive", ADAPTIVE)):
                runs = [crossroads_run(model, controller, demand, seed) for seed in SEEDS]
                rows.append({
                    "demand": demand, "model": model, "config": label,
                    **{key: mean(r[key] for r in runs) for key in runs[0]},
                })
    return rows


# ----------------------------------------------------------------------
# 2. Norwich
# ----------------------------------------------------------------------

def norwich_spec(model, seed, signals):
    spec = {
        "name": "signals", "seed": seed,
        "demand": {"vehicles": 1000, "spread_seconds": 300},
        "traffic_model": model,
        "stations": [{"latitude": 52.6286, "longitude": 1.2927, "ambulances": 3}],
        "emergencies": {"count": 15, "first_at": 60, "interval_seconds": 40},
    }
    if signals:
        spec["signals"] = signals
    return spec


def figures(result):
    journeys = [j for j, s in zip(result["trips"]["journey_seconds"], result["trips"]["status"]) if s == "finished"]
    series = result["timeseries"]
    weighted = [(r["mean_speed_factor"], r["active_vehicles"]) for r in series
                if r["mean_speed_factor"] is not None and r["active_vehicles"]]
    total = sum(w for _, w in weighted)
    signals = result["signals"] or {}
    emergencies = result["emergencies"]
    return {
        "completed": result["summary"]["completion_percent"],
        "mean_journey": mean(journeys),
        "worst_journey": max(journeys, default=None),
        "mean_delay": result["summary"]["mean_delay_seconds"],
        "mean_wait": signals.get("mean_wait_seconds"),
        "mean_queue": signals.get("mean_queue"),
        "max_queue": signals.get("max_queue"),
        "throughput": signals.get("crossings_per_junction_hour"),
        "speed": sum(f * w for f, w in weighted) / total if total else None,
        "response": emergencies["mean_response_seconds"],
        "worst_response": emergencies["worst_response_seconds"],
        "priority": signals.get("priority_requests"),
        "wall": result["execution"]["wall_seconds"],
    }


def part2_norwich(world):
    rows = []
    for model in ("osm", "ca"):
        for label, signals in CONFIGS:
            runs = []
            for seed in SEEDS:
                runs.append(figures(run_scenario(world, norwich_spec(model, seed, signals))))
                print(f"   {model} {label} seed {seed}: {runs[-1]['wall']:.1f} s", flush=True)
            rows.append({"model": model, "config": label, "runs": runs,
                         **{key: mean(r[key] for r in runs) for key in runs[0]}})
    return rows


def repeatable(world):
    spec = norwich_spec("osm", 4, {"controller": "adaptive", "emergency_priority": True})
    return run_scenario(world, spec)["trips"]["journey_seconds"] == run_scenario(world, spec)["trips"]["journey_seconds"]


# ----------------------------------------------------------------------
# Report
# ----------------------------------------------------------------------

def write_report(machine, crossroads_rows, norwich_rows, same, total):
    lines = [
        "# Traffic signals: experiments",
        "",
        f"Generated by `python -m backend.simulation.benchmark_signals --save` on {machine}"
        f" ({datetime.date.today().isoformat()}), {total / 60:.0f} minutes in all. Seeds 1, 2 and 3;"
        " figures are their mean. See [TRAFFIC_SIGNALS.md](TRAFFIC_SIGNALS.md) for the model.",
        "",
        "## 1. One crossroads",
        "",
        "200 m arms, one lane each way, 30 mph. Cars arrive for 5 minutes (balanced: 0.08 a second"
        " from each arm, 1,150 an hour in all; unbalanced: 0.2 a second north and south, 0.03 east"
        " and west, 1,660 an hour), each to a random other arm; then 5 minutes to clear. Every right"
        " turn conflicts with the oncoming traffic, so each arm gets its own phase.",
        "",
        "| Demand | Model | Control | Through in 10 min | Mean journey (s) | Worst journey (s)"
        " | Mean wait at signals (s) | Longest queue |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in crossroads_rows:
        lines.append(
            f"| {r['demand']} | {r['model']} | {r['config']} | {r['finished']:.0f} of {r['cars']:.0f}"
            f" | {fmt(r['mean_journey'])} | {fmt(r['worst_journey'])} | {fmt(r['mean_wait'], 1)}"
            f" | {fmt(r['max_queue'])} |"
        )
    lines += [
        "",
        "Journey times count finished cars only, so a row that did not get every car through looks"
        " better than it was.",
        "",
        "## 2. Norwich",
        "",
        "The 121 junctions OpenStreetMap shows with traffic lights (108 of them get signals: the"
        " other 13 have only one road in, because of one-way streets); 1,000 trips leaving over 5"
        " minutes; 15 ambulance calls at random junctions, one every 40 s from 1 minute, from one"
        " central station with 3 ambulances; dynamic rerouting on. The same seed gives the same"
        " trips and emergencies in every row.",
        "",
        "| Model | Control | Mean journey (s) | Worst journey (s) | Mean wait at signals (s)"
        " | Mean / longest queue | Crossings per junction-hour | Mean speed (of free flow)"
        " | Ambulance response, mean / worst (s) | Priority requests | Runtime (s) |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    base = {}
    for r in norwich_rows:
        if r["config"] == "No signals":
            base[r["model"]] = r["wall"]
        overhead = f" (+{100 * (r['wall'] / base[r['model']] - 1):.0f}%)" if r["config"] != "No signals" else ""
        lines.append(
            f"| {r['model']} | {r['config']} | {fmt(r['mean_journey'])} | {fmt(r['worst_journey'])}"
            f" | {fmt(r['mean_wait'], 1)} | {fmt(r['mean_queue'], 1)} / {fmt(r['max_queue'])}"
            f" | {fmt(r['throughput'])} | {100 * r['speed']:.0f}%"
            f" | {fmt(r['response'])} / {fmt(r['worst_response'])} | {fmt(r['priority'])}"
            f" | {r['wall']:.1f}{overhead} |"
        )
    lines += [
        "",
        "All trips completed in every Norwich run." if all(
            r["completed"] == 100 for r in norwich_rows
        ) else "Not every trip completed in every run: see the completion figures in the code's output.",
        "",
        "Ambulance response per seed (mean seconds), to show how much it varies:",
        "",
        "| Model | Control | Seed 1 | Seed 2 | Seed 3 |",
        "|---|---|---|---|---|",
    ]
    for r in norwich_rows:
        lines.append(f"| {r['model']} | {r['config']} | " + " | ".join(fmt(x['response']) for x in r["runs"]) + " |")
    lines += [
        "",
        f"Repeatable: the same scenario with adaptive signals and priority, run twice, gave"
        f" {'identical' if same else 'DIFFERENT'} journey times.",
    ]
    REPORT.write_text("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--save", action="store_true")
    arguments = parser.parse_args()
    started = time.perf_counter()
    machine = f"{platform.machine()} / Python {platform.python_version()}"

    print("1. crossroads", flush=True)
    crossroads_rows = part1_crossroads()
    with open(GRAPH_FILE, "rb") as file:
        world = World(pickle.load(file))
    print("2. Norwich", flush=True)
    norwich_rows = part2_norwich(world)
    same = repeatable(world)
    total = time.perf_counter() - started
    print(f"done in {total / 60:.1f} min", flush=True)
    if arguments.save:
        write_report(machine, crossroads_rows, norwich_rows, same, total)
        print(f"wrote {REPORT.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
