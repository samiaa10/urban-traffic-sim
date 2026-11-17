"""Run a traffic-aware evaluation of the optimiser on Norwich from the
command line (no API or database), the same as POST /optimiser/evaluations.

    python -m backend.optimiser.run_evaluation                 top 5 sites
    python -m backend.optimiser.run_evaluation --kind station_set --stations 3
    python -m backend.optimiser.run_evaluation --save          also write docs/optimiser_evaluation.md

Scenarios run in --workers processes (spawn, each with its own map).
"""

import argparse
import datetime
import json
import multiprocessing
import pickle
import platform
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GRAPH_FILE = PROJECT_ROOT / "data" / "osm" / "norwich_graph.pkl"
REPORT_FILE = PROJECT_ROOT / "docs" / "optimiser_evaluation.md"

_world = None


def _init(graph_file):
    global _world
    from backend.scenarios.runner import World

    with open(graph_file, "rb") as file:
        _world = World(pickle.load(file))


def _run(spec_dict):
    from backend.scenarios.runner import run_scenario

    result = run_scenario(_world, spec_dict)
    return {"summary": result["summary"], "emergencies": result["emergencies"],
            "wall_seconds": result["execution"]["wall_seconds"]}


def minutes(seconds):
    return "-" if seconds is None else f"{seconds / 60:.2f} min"


def interval(figure, scale=60, unit="min", digits=2):
    if figure["mean"] is None:
        return "-"
    text = f"{figure['mean'] / scale:.{digits}f}"
    if figure["ci95"] is not None:
        text += f" ± {figure['ci95'] / scale:.{digits}f}"
    return f"{text} {unit}"


def markdown(settings, report, wall, scenario_seconds, workers):
    lines = [
        "# The optimiser's answers tested in traffic",
        "",
        f"Run on {datetime.date.today()} with Python {platform.python_version()} "
        f"({platform.platform()}), {workers} worker processes. Reproduce with "
        f"`python -m backend.optimiser.run_evaluation --kind {settings['kind']} "
        + (f"--stations {settings['stations']} " if settings["kind"] == "station_set" else "")
        + f"--top {settings['top']} --save`.",
        "",
        f"The optimiser (single-site ranking or station set, {settings['optimiser_scenarios']} "
        f"emergencies, {settings['speed']:.0f} m/s, {settings['target_seconds'] / 60:.0f} min target, "
        f"{settings['distribution']} emergency locations) picked the candidates below; each was "
        f"then simulated with the osm traffic model at {', '.join(map(str, settings['demand']))} "
        f"background vehicles and seeds {', '.join(map(str, settings['seeds']))}: "
        f"{settings['emergencies']} {settings['service']} emergencies per run, one every "
        f"{settings['emergency_interval_seconds']:.0f} s from {settings['first_emergency_at']:.0f} s, "
        f"{settings['vehicles_per_station']} vehicles per station, realistic dispatch "
        "(fastest vehicle, queue, drive back, right of way). Every candidate faces the same trips "
        "and emergencies for a given demand level and seed. "
        f"{report['scenarios_total']} scenarios in {wall / 60:.1f} min "
        f"({scenario_seconds:.0f} s of simulation in total).",
        "",
        "| Rank in traffic | Candidate | Optimiser's own estimate | Estimate for the simulated emergencies | Simulated mean | Gap (same emergencies) | Rank by optimiser |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in report["candidates"]:
        lines.append(
            f"| {row['simulated_rank']} | {row['label']} | {minutes(row['estimate']['mean_response_seconds'])} | "
            f"{minutes(row['paired_estimate_seconds'])} | "
            f"{minutes(row['simulated_mean_response_seconds'])} | "
            f"{row['estimate_gap_percent']:+.0f}% | {row['estimated_rank']} |"
        )
    lines += ["", "Per demand level (mean ± 95% interval over the seeds; p90 and worst pooled over all runs):", "",
              "| Candidate | Vehicles | Mean response | Of which waiting for a vehicle | p90 | Worst | Within target | Unreached | Network delay | Added by the response |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for row in report["candidates"]:
        for d in row["by_demand"]:
            lines.append(
                f"| {row['label']} | {d['vehicles']} | {interval(d['mean_response_seconds'])} | "
                f"{minutes(d.get('mean_wait_seconds'))} | "
                f"{minutes(d['p90_response_seconds'])} | {minutes(d['worst_response_seconds'])} | "
                f"{interval(d['within_target_percent'], 1, '%', 0)} | {d['unreached']} | "
                f"{interval(d['network_delay_vehicle_hours'], 1, 'veh-h', 1)} | "
                f"{interval(d['added_delay_vehicle_hours'], 1, 'veh-h', 2)} |"
            )
    tau = report["rank_agreement_kendall_tau"]
    lines += ["", f"Kendall's tau between the optimiser's order and the simulated order: "
              + ("-" if tau is None else f"{tau:.2f}") + ".", "", "What it says:", ""]
    lines += [f"- {line}" for line in report["explanation"]]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--kind", choices=["ranking", "station_set"], default="ranking")
    parser.add_argument("--stations", type=int, default=3)
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--service", default="ambulance")
    parser.add_argument("--distribution", default="uniform")
    parser.add_argument("--demand", type=int, nargs="+", default=[500, 1500])
    parser.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3])
    parser.add_argument("--emergencies", type=int, default=12)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args()

    from backend.api.routes.optimiser import service_settings
    from backend.optimiser.evaluation import build_specs, candidates_to_test, summarise

    with open(GRAPH_FILE, "rb") as file:
        graph = pickle.load(file)

    target_seconds, speed = service_settings(args.service, None, None, args.distribution)
    settings = {
        "kind": args.kind, "stations": args.stations, "top": args.top,
        "service": args.service, "distribution": args.distribution,
        "target_seconds": target_seconds, "speed": speed,
        "optimiser_scenarios": 300, "optimiser_seed": 1,
        "demand": args.demand, "seeds": args.seeds, "emergencies": args.emergencies,
        "first_emergency_at": 120, "emergency_interval_seconds": 45,
        "vehicles_per_station": 2, "spread_seconds": 300, "max_seconds": 2400,
    }

    started = time.perf_counter()
    candidates = candidates_to_test(graph, settings)
    specs, plan = build_specs(settings, candidates)
    print(f"{len(candidates)} candidates, {len(specs)} scenarios", flush=True)

    with ProcessPoolExecutor(
        max_workers=args.workers, mp_context=multiprocessing.get_context("spawn"),
        initializer=_init, initargs=(str(GRAPH_FILE),),
    ) as pool:
        results = list(pool.map(_run, [spec.model_dump(mode="json") for spec in specs]))

    wall = time.perf_counter() - started
    report = summarise(settings, candidates, plan, results, graph)
    text = markdown(settings, report, wall, sum(r["wall_seconds"] for r in results), args.workers)
    print(text)

    if args.save:
        REPORT_FILE.write_text(text + "\n")
        (PROJECT_ROOT / "docs" / "optimiser_evaluation.json").write_text(
            json.dumps({"settings": settings, "report": report}, indent=1, default=float)
        )
        print(f"\nSaved to {REPORT_FILE}")


if __name__ == "__main__":
    main()
