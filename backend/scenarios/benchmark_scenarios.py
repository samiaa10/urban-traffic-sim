"""
Benchmark: the scenario engine on the real Norwich map
------------------------------------------------------

Run from the project root (parts 4 and 5 need a migrated database):

    DATABASE_URL=postgresql://... python -m backend.scenarios.benchmark_scenarios --save

Five parts, all full simulations:

  1. One scenario at increasing fleet sizes, each in a fresh process (so
     its peak memory is its own): wall and CPU time, simulated seconds,
     vehicle-seconds simulated per second, peak memory.
  2. What measuring the impact costs: the same 500-vehicle scenario with
     and without the impact accounting, alternating.
  3. Parallel speed-up: the same 16 scenarios with 1, 2, 4, 6 and 8 worker
     processes (as the JobManager runs them).
  4. Saving a result to PostgreSQL: one 500-vehicle result, several times.
  5. A large study through the API's own JobManager and database: random
     road closures in the city centre (Monte Carlo), with the spread of
     outcomes it found.

Timings depend on the machine; the report records which.
"""

import argparse
import datetime
import os
import pickle
import platform
import statistics
import time
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
from pathlib import Path

from backend.scenarios import jobs
from backend.scenarios.runner import World, run_scenario
from backend.scenarios.spec import ScenarioSpec


PROJECT_ROOT = Path(__file__).resolve().parents[2]
GRAPH_FILE = PROJECT_ROOT / "data" / "osm" / "norwich_graph.pkl"
REPORT = PROJECT_ROOT / "docs" / "scenario_benchmark.md"

CENTRE = (52.6286, 1.2927)


def _one_in_fresh_process(spec):
    """Run one scenario in a new process; its result (with peak memory)."""

    with ProcessPoolExecutor(
        max_workers=1, mp_context=get_context("spawn"),
        initializer=jobs._init_worker, initargs=(str(GRAPH_FILE), None),
    ) as pool:
        return pool.submit(jobs._run, spec).result()


def part1_scaling(fleets):
    rows = []
    for fleet in fleets:
        kind, result = _one_in_fresh_process({"name": f"{fleet}", "seed": 1, "demand": {"vehicles": fleet}})
        assert kind == "ok", result
        execution, summary = result["execution"], result["summary"]
        rows.append({
            "vehicles": fleet,
            "wall": execution["wall_seconds"],
            "cpu": execution["cpu_seconds"],
            "simulated": summary["simulated_seconds"],
            "rate": execution["vehicle_seconds_per_second"],
            "rss": execution["peak_rss_mb"],
            "delay": summary["total_delay_vehicle_hours"],
            "increase": summary["mean_increase_seconds"],
        })
        print(f"  {fleet:>5} vehicles: {execution['wall_seconds']:.1f} s, "
              f"{execution['peak_rss_mb']:.0f} MB", flush=True)
    return rows


def part2_overhead(world, repeats):
    spec = {"name": "overhead", "seed": 11, "demand": {"vehicles": 500}}
    run_scenario(world, spec)    # warm the free-flow cache
    times = {True: [], False: []}
    for _ in range(repeats):
        for measure in (False, True):
            started = time.perf_counter()
            run_scenario(world, spec, measure=measure)
            times[measure].append(time.perf_counter() - started)
    plain, measured = statistics.median(times[False]), statistics.median(times[True])
    print(f"  plain {plain:.2f} s, measured {measured:.2f} s", flush=True)
    return {"plain": plain, "measured": measured, "repeats": repeats,
            "overhead": 100 * (measured - plain) / plain}


def part3_parallel(worker_counts, scenarios, vehicles):
    specs = [{"name": f"S{seed}", "seed": seed, "demand": {"vehicles": vehicles}}
             for seed in range(1, scenarios + 1)]
    rows = []
    for workers in worker_counts:
        started = time.perf_counter()
        with ProcessPoolExecutor(
            max_workers=workers, mp_context=get_context("spawn"),
            initializer=jobs._init_worker, initargs=(str(GRAPH_FILE), None),
        ) as pool:
            outcomes = list(pool.map(jobs._run, specs))
        wall = time.perf_counter() - started
        assert all(kind == "ok" for kind, _ in outcomes)
        results = [result for _, result in outcomes]
        rows.append({
            "workers": workers,
            "scenarios": scenarios,
            "wall": wall,
            "per_hour": scenarios * 3600 / wall,
            "rss": max(r["execution"]["peak_rss_mb"] for r in results),
            "mean_scenario": statistics.mean(r["execution"]["wall_seconds"] for r in results),
        })
        print(f"  {workers} worker(s): {wall:.1f} s", flush=True)
    base = rows[0]["wall"]
    for row in rows:
        row["speedup"] = base / row["wall"]
        row["efficiency"] = 100 * row["speedup"] / row["workers"]
    return rows


def part4_saving(world, url, repeats):
    from backend.db.database import Database
    from backend.db.scenario_repository import ScenarioRepository

    database = Database(url)
    spec = ScenarioSpec.model_validate({"name": "save", "seed": 2, "demand": {"vehicles": 500}})
    result = run_scenario(world, spec)
    times = []
    study_ids = []
    for _ in range(repeats):
        with database.transaction() as session:
            repo = ScenarioRepository(session)
            study = repo.create_study("bench", [spec], "total_delay_vehicle_hours", 1, "x" * 64, 1)
            scenario_id = study.scenarios[0].id
            study_ids.append(study.id)
            repo.start_scenario(scenario_id)
        started = time.perf_counter()
        with database.transaction() as session:
            ScenarioRepository(session).save_result(scenario_id, result)
        times.append((time.perf_counter() - started) * 1000)
    with database.transaction() as session:
        repo = ScenarioRepository(session)
        for study_id in study_ids:
            repo.delete_study(study_id)
    database.dispose()
    print(f"  saving a result: median {statistics.median(times):.1f} ms", flush=True)
    return {"median_ms": statistics.median(times), "max_ms": max(times),
            "repeats": repeats, "trips": result["summary"]["trips_requested"],
            "rows": len(result["roads"]) + len(result["areas"]) + len(result["timeseries"]) + 1}


def part5_large_study(url, count, vehicles, closures, workers):
    """A random-closures study run exactly as the API runs one."""

    from backend.api.context import graph_fingerprint
    from backend.db.database import Database
    from backend.db.scenario_repository import (
        ScenarioRepository, result_from_rows, summary_from_rows,
    )
    from backend.scenarios import sweep
    from backend.scenarios.compare import paired
    from backend.simulation.demand import Zone

    with open(GRAPH_FILE, "rb") as file:
        graph = pickle.load(file)
    world = World(graph)
    sha = graph_fingerprint(graph, GRAPH_FILE)

    base = ScenarioSpec.model_validate({
        "name": "Norwich", "seed": 1,
        "demand": {"vehicles": vehicles, "spread_seconds": 300},
    })
    roads = sweep.candidate_roads(world.network, Zone(CENTRE[0], CENTRE[1], 1500))
    specs = sweep.random_closures(base, roads, count, closures, sweep_seed=1)

    database = Database(url)
    manager = jobs.JobManager(database, GRAPH_FILE, sha, workers)

    with database.transaction() as session:
        study = ScenarioRepository(session).create_study(
            "Benchmark: random closures", specs, "total_delay_vehicle_hours",
            workers, sha, 1, baseline_position=0,
        )
        study_id = study.id

    started = time.perf_counter()
    manager.submit_study(study_id)
    manager.wait_until_idle()
    wall = time.perf_counter() - started
    manager.shutdown()

    with database.transaction() as session:
        repo = ScenarioRepository(session)
        study = repo.get_study(study_id)
        scenarios = repo.scenarios(study_id, with_results=True)
        baseline = result_from_rows(repo.get_scenario(scenarios[0].id, full=True))
        extra, cut_off, delays, rss, per_scenario = [], [], [], [], []
        for scenario in scenarios[1:]:
            if scenario.result is None:
                continue
            light = summary_from_rows(scenario)
            delays.append(light["summary"]["total_delay_vehicle_hours"])
            rss.append(light["execution"]["peak_rss_mb"])
            per_scenario.append(light["execution"]["wall_seconds"])
            comparison = paired(baseline, result_from_rows(repo.get_scenario(scenario.id, full=True)))
            extra.append(comparison["extra_vehicle_hours"])
            cut_off.append(comparison["newly_cut_off"])
        completed, failed = study.completed_count, study.failed_count
        repo.delete_study(study_id)
    database.dispose()

    ordered = sorted(extra)
    print(f"  {count + 1} scenarios in {wall:.0f} s", flush=True)
    return {
        "scenarios": count + 1, "vehicles": vehicles, "closures": closures,
        "candidate_roads": len(roads), "workers": workers, "wall": wall,
        "per_hour": (count + 1) * 3600 / wall, "completed": completed, "failed": failed,
        "baseline_delay": baseline["summary"]["total_delay_vehicle_hours"],
        "delay_median": statistics.median(delays), "delay_min": min(delays), "delay_max": max(delays),
        "extra_median": statistics.median(extra), "extra_min": ordered[0], "extra_max": ordered[-1],
        "extra_p90": ordered[int(0.9 * (len(ordered) - 1))],
        "worse": sum(1 for e in extra if e > 0), "better": sum(1 for e in extra if e < 0),
        "cut_off_scenarios": sum(1 for c in cut_off if c), "rss_max": max(rss),
        "mean_scenario": statistics.mean(per_scenario),
    }


def report(scaling, overhead, parallel, saving, large):
    cpu = os.cpu_count()
    lines = [
        "# Scenario engine benchmark",
        "",
        "Full simulations on the real Norwich map (5,058 junctions, 10,980 road segments).",
        "Produced by `python -m backend.scenarios.benchmark_scenarios --save`.",
        "",
        f"- Date: {datetime.date.today().isoformat()}",
        f"- Machine: {platform.platform()}, {cpu} CPUs, Python {platform.python_version()}",
        "",
        "## 1. One scenario, by fleet size",
        "",
        "Each run in a fresh process; trips leave at once and the run ends when the last arrives.",
        "",
        "| Vehicles | Wall time (s) | CPU time (s) | Simulated (s) | Vehicle-seconds per second | Peak memory (MB) | Network delay (vehicle-hours) | Mean journey-time increase (s) |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in scaling:
        lines.append(
            f"| {r['vehicles']:,} | {r['wall']:.1f} | {r['cpu']:.1f} | {r['simulated']:,} | "
            f"{r['rate']:,.0f} | {r['rss']:.0f} | {r['delay']:.1f} | {r['increase']:.0f} |"
        )
    lines += [
        "",
        "## 2. What measuring the impact costs",
        "",
        f"The same 500-vehicle scenario with and without the impact accounting, alternating, "
        f"{overhead['repeats']} times each (median): {overhead['plain']:.2f} s without, "
        f"{overhead['measured']:.2f} s with, **{overhead['overhead']:+.1f}%**.",
        "",
        "## 3. Parallel speed-up",
        "",
        f"The same {parallel[0]['scenarios']} scenarios of 500 vehicles (seeds 1 to "
        f"{parallel[0]['scenarios']}) with a pool of worker processes.",
        "",
        "| Workers | Wall time (s) | Speed-up | Efficiency | Scenarios per hour | Mean time per scenario (s) | Peak memory per worker (MB) |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in parallel:
        lines.append(
            f"| {r['workers']} | {r['wall']:.1f} | {r['speedup']:.2f}x | {r['efficiency']:.0f}% | "
            f"{r['per_hour']:,.0f} | {r['mean_scenario']:.1f} | {r['rss']:.0f} |"
        )
    if saving:
        lines += [
            "",
            "## 4. Saving a result",
            "",
            f"Storing one 500-vehicle result ({saving['trips']} trips in the per-trip arrays, "
            f"{saving['rows']} rows in all) in a local PostgreSQL, {saving['repeats']} times: "
            f"median {saving['median_ms']:.1f} ms, at most {saving['max_ms']:.1f} ms.",
        ]
    if large:
        lines += [
            "",
            "## 5. A large study",
            "",
            f"{large['scenarios']} scenarios run by the API's own JobManager with {large['workers']} "
            f"workers and stored in PostgreSQL: a baseline of {large['vehicles']} trips leaving over "
            f"5 minutes, and {large['scenarios'] - 1} scenarios each closing {large['closures']} roads "
            f"drawn at random from the {large['candidate_roads']} roads within 1.5 km of the city centre.",
            "",
            f"- Wall time: **{large['wall'] / 60:.1f} minutes** ({large['per_hour']:,.0f} scenarios per hour); "
            f"{large['completed']} completed, {large['failed']} failed.",
            f"- Mean time per scenario in a worker: {large['mean_scenario']:.1f} s; peak memory per worker "
            f"{large['rss_max']:.0f} MB.",
            f"- Baseline network delay: {large['baseline_delay']:.1f} vehicle-hours; with closures: "
            f"median {large['delay_median']:.1f}, from {large['delay_min']:.1f} to {large['delay_max']:.1f}.",
            f"- Extra travel time against the baseline, trip by trip: median {large['extra_median']:+.2f} "
            f"vehicle-hours, 90th percentile {large['extra_p90']:+.2f}, worst {large['extra_max']:+.2f}, "
            f"best {large['extra_min']:+.2f}.",
            f"- {large['worse']} closure sets made total travel time longer and {large['better']} shorter; "
            f"in {large['cut_off_scenarios']} some trips had no route at all.",
        ]
    lines += ["", READING.strip()]
    return "\n".join(lines) + "\n"


# Interpretation of the 2026-11-16 run (docs/scenario_benchmark.md); check
# it still matches if the numbers above change
READING = """
## Reading the results

- **Measuring is cheap.** The impact accounting adds about 2% to a run
  (one pass over the vehicles on the road each second, with each
  vehicle's road cached until it moves on). A first version added 8.4%;
  caching the road and dropping finished vehicles from the pass fixed it.
- **A scenario costs more than its fleet size suggests.** Throughput
  stays near 10,000 to 12,000 vehicle-seconds per second, but bigger
  fleets also congest more, so trips take longer and there are more
  vehicle-seconds to simulate: 200 vehicles take about 6 s, 2,000 about
  105 s. A worker process peaks at 78 to 99 MB.
- **Parallel speed-up is real but limited by this machine.** Two workers
  are almost twice as fast; beyond that each scenario itself slows down
  (14.8 s alone, 21.7 s with four running, 31.7 s with eight), because
  this Mac has four performance and four efficiency cores that share
  power and memory bandwidth. The job manager is not the limit: it keeps
  every worker busy (one scenario per free worker), and the workers do
  not touch the database. On a machine with more performance cores the
  efficiency at 4 or more workers should be higher; that was not
  measured here.
- **Saving is negligible.** About 17 ms per result against 13 to 16 s of
  simulation.
- **The large study** ran 400 full simulations through the same job
  manager and database the API uses, with no failures, at about 1,100
  scenarios an hour on 4 workers. Random closures near the centre mostly
  cost time, but 77 of 399 closure sets made total travel shorter:
  closing a road can move traffic off a congested route (a Braess-like
  effect). Journey-time increase still counts the detours, which is why
  the comparison reports both.
"""


def main():
    parser = argparse.ArgumentParser(description="Scenario engine benchmark")
    parser.add_argument("--fleets", default="200,500,1000,2000")
    parser.add_argument("--repeats", type=int, default=4)
    parser.add_argument("--workers", default="1,2,4,6,8")
    parser.add_argument("--parallel-scenarios", type=int, default=16)
    parser.add_argument("--study-size", type=int, default=399)
    parser.add_argument("--study-vehicles", type=int, default=300)
    parser.add_argument("--study-workers", type=int, default=4)
    parser.add_argument("--save", action="store_true")
    arguments = parser.parse_args()

    from backend.api.settings import database_url

    url = database_url(os.environ.get("DATABASE_URL"))

    with open(GRAPH_FILE, "rb") as file:
        world = World(pickle.load(file))

    print("1. scaling", flush=True)
    scaling = part1_scaling([int(n) for n in arguments.fleets.split(",")])
    print("2. overhead", flush=True)
    overhead = part2_overhead(world, arguments.repeats)
    print("3. parallel", flush=True)
    parallel = part3_parallel(
        [int(n) for n in arguments.workers.split(",")], arguments.parallel_scenarios, 500
    )
    saving = large = None
    if url:
        print("4. saving", flush=True)
        saving = part4_saving(world, url, 10)
        print("5. large study", flush=True)
        large = part5_large_study(url, arguments.study_size, arguments.study_vehicles, 3, arguments.study_workers)

    text = report(scaling, overhead, parallel, saving, large)
    print()
    print(text)
    if arguments.save:
        REPORT.write_text(text)
        print(f"Saved {REPORT}")


if __name__ == "__main__":
    main()
