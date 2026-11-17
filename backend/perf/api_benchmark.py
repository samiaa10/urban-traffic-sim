"""Throughput and latency through the real HTTP API, as a client sees them.

    python -m backend.perf.api_benchmark           print the results
    python -m backend.perf.api_benchmark --save    also write docs/api_benchmark.md

Starts the backend with uvicorn on the Norwich map (port 8003, one
worker, as deployed; rate limit off, no database) and measures, with
httpx clients:

1. Request overhead: GET /health, one client after another and from 8
   clients at once (routing, middleware, JSON; no simulation work).
2. Routing: GET /route between random junctions.
3. A step as the website sees it: POST /simulations/{id}/step at 1x and
   5x with 2 (just the route), 500 and 2,000 vehicles, osm traffic model.
4. Several visitors: 8 runs of 500 vehicles, each stepped by its own
   client at once, against one run stepped alone.
5. Memory: the server's resident memory after loading the map, and with
   those 8 runs live.

The backend is one process (live runs are kept in its memory), so
simultaneous steps share one CPU core under Python's global interpreter
lock: section 4 shows what that costs.
"""

import argparse
import datetime
import os
import platform
import random
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPORT_FILE = PROJECT_ROOT / "docs" / "api_benchmark.md"
PORT = 8003
API = f"http://127.0.0.1:{PORT}"
SEED = 42


def start_server():
    env = dict(
        os.environ,
        RATE_LIMIT_PER_MINUTE="0",
        LOG_LEVEL="WARNING",
        TRAFFIC_MODEL="osm",
        MAX_LIVE_RUNS="20",
        RUN_IDLE_MINUTES="0",
        SCENARIO_WORKERS="0",
    )
    env.pop("DATABASE_URL", None)
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.api.main:app",
         "--host", "127.0.0.1", "--port", str(PORT), "--no-access-log", "--log-level", "warning"],
        cwd=PROJECT_ROOT, env=env,
    )
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            if httpx.get(f"{API}/health", timeout=1).status_code == 200:
                return server
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    server.kill()
    raise RuntimeError("The backend did not start")


def rss_mb(pid):
    output = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True).stdout
    return int(output.strip()) / 1024


def percentile(values, share):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(share * len(ordered)))]


def timed(call):
    started = time.perf_counter()
    call()
    return (time.perf_counter() - started) * 1000


def summary(times_ms):
    return {
        "n": len(times_ms),
        "mean": statistics.mean(times_ms),
        "p50": percentile(times_ms, 0.50),
        "p95": percentile(times_ms, 0.95),
    }


def check(response):
    response.raise_for_status()
    return response.json()


def junctions(client, count, rng):
    """Junctions near random points in central Norwich."""

    found = []
    while len(found) < count:
        latitude = 52.63 + rng.uniform(-0.02, 0.02)
        longitude = 1.29 + rng.uniform(-0.035, 0.035)
        found.append(check(client.get(f"{API}/nearest-node", params={"latitude": latitude, "longitude": longitude}))["node"])
    return found


def start_run(client, nodes, rng, vehicles):
    while True:
        start, destination = rng.sample(nodes, 2)
        answer = client.post(f"{API}/simulation/start", params={
            "start": start, "destination": destination, "replace_default": "false"})
        if answer.status_code == 200:
            break
    run_id = answer.json()["simulation_id"]
    if vehicles:
        check(client.post(f"{API}/simulations/{run_id}/fleet", params={"count": vehicles, "seed": SEED}))
    return run_id


def step_times(client, run_id, seconds, steps):
    url = f"{API}/simulations/{run_id}/step"
    return [timed(lambda: check(client.post(url, params={"seconds": seconds}))) for _ in range(steps)]


# ------------------------------------------------------------
# The measurements
# ------------------------------------------------------------

def request_overhead():
    with httpx.Client() as client:
        for _ in range(50):
            client.get(f"{API}/health")
        sequential = [timed(lambda: client.get(f"{API}/health")) for _ in range(2000)]

    def worker(results):
        with httpx.Client() as client:
            for _ in range(500):
                results.append(timed(lambda: client.get(f"{API}/health")))

    results = []
    threads = [threading.Thread(target=worker, args=(results,)) for _ in range(8)]
    started = time.perf_counter()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    elapsed = time.perf_counter() - started

    return {
        "sequential": summary(sequential),
        "sequential_per_second": 1000 / statistics.mean(sequential),
        "concurrent": summary(results),
        "concurrent_per_second": len(results) / elapsed,
    }


def routing(nodes, rng):
    with httpx.Client(timeout=30) as client:
        times, explored = [], []
        for _ in range(200):
            start, destination = rng.sample(nodes, 2)
            started = time.perf_counter()
            answer = client.get(f"{API}/route", params={"start": start, "destination": destination})
            times.append((time.perf_counter() - started) * 1000)
            if answer.status_code == 200:
                explored.append(answer.json()["nodes_explored"])
    return {**summary(times), "nodes_explored": statistics.mean(explored)}


def stepping(nodes, rng):
    rows = []
    with httpx.Client(timeout=120) as client:
        for vehicles in (0, 500, 2000):
            run_id = start_run(client, nodes, rng, vehicles)
            step_times(client, run_id, 1, 5)        # warm up
            for seconds in (1, 5):
                times = step_times(client, run_id, seconds, 30)
                rows.append({"vehicles": vehicles + 2, "seconds": seconds, **summary(times),
                             "speed": seconds * 1000 / statistics.mean(times)})
            client.delete(f"{API}/simulations/{run_id}")
    return rows


def visitors(nodes, rng, server_pid):
    with httpx.Client(timeout=120) as client:
        run_ids = [start_run(client, nodes, rng, 500) for _ in range(8)]
        for run_id in run_ids:
            step_times(client, run_id, 1, 3)

        memory_with_runs = rss_mb(server_pid)

        # One of them stepped alone (the others idle) as the reference
        alone = step_times(client, run_ids[0], 1, 20)

    results = []

    def worker(run_id):
        with httpx.Client(timeout=120) as client:
            results.extend(step_times(client, run_id, 1, 20))

    threads = [threading.Thread(target=worker, args=(run_id,)) for run_id in run_ids]
    started = time.perf_counter()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    elapsed = time.perf_counter() - started

    with httpx.Client() as client:
        for run_id in run_ids:
            client.delete(f"{API}/simulations/{run_id}")

    return {
        "alone": summary(alone),
        "together": summary(results),
        "steps_per_second_alone": 1000 / statistics.mean(alone),
        "steps_per_second_together": len(results) / elapsed,
        "memory_with_runs": memory_with_runs,
    }


# ------------------------------------------------------------
# Report
# ------------------------------------------------------------

def report(results):
    machine = f"{platform.system()} {platform.machine()}, Python {platform.python_version()}"
    overhead, route, steps, many = results["overhead"], results["routing"], results["steps"], results["visitors"]
    lines = [
        "# API throughput and latency",
        "",
        f"Measured through the real HTTP API by `python -m backend.perf.api_benchmark --save` on "
        f"{datetime.date.today().isoformat()} ({machine}, {os.cpu_count()} cores). The backend ran as deployed: "
        "uvicorn, one worker, the Norwich map, the osm traffic model; the rate limit and the database were off. "
        "Times are wall-clock milliseconds per request as the client saw them, over localhost, so they include "
        "HTTP, JSON and the protections middleware. They depend on the machine.",
        "",
        "## Request overhead",
        "",
        "`GET /health`: no simulation work, so this is the cost of a request itself.",
        "",
        "| Clients | Requests | Mean | p95 | Requests per second |",
        "|---|---|---|---|---|",
        f"| 1, one after another | {overhead['sequential']['n']:,} | {overhead['sequential']['mean']:.2f} ms | "
        f"{overhead['sequential']['p95']:.2f} ms | {overhead['sequential_per_second']:,.0f} |",
        f"| 8 at once | {overhead['concurrent']['n']:,} | {overhead['concurrent']['mean']:.2f} ms | "
        f"{overhead['concurrent']['p95']:.2f} ms | {overhead['concurrent_per_second']:,.0f} |",
        "",
        "## Routing",
        "",
        f"`GET /route` between {route['n']} random pairs of central junctions (A*, no traffic): mean "
        f"{route['mean']:.1f} ms, p50 {route['p50']:.1f} ms, p95 {route['p95']:.1f} ms, "
        f"{route['nodes_explored']:,.0f} junctions explored on average.",
        "",
        "## A step, as the website sees it",
        "",
        "`POST /simulations/{id}/step?seconds=N`, 30 steps after 5 to warm up, one run. The website asks for "
        "one step a second and sends the next only when the last has been answered, so a step slower than "
        "a second makes the simulation run slower than its speed setting. *Real-time factor* is simulated "
        "seconds per real second when stepping back to back.",
        "",
        "| Vehicles | Speed | Mean | p50 | p95 | Real-time factor |",
        "|---|---|---|---|---|---|",
    ]
    for row in steps:
        lines.append(
            f"| {row['vehicles']:,} | {row['seconds']}x | {row['mean']:.1f} ms | {row['p50']:.1f} ms | "
            f"{row['p95']:.1f} ms | {row['speed']:.1f}x |"
        )
    lines += [
        "",
        "## Several visitors at once",
        "",
        "Eight runs of 502 vehicles, each stepped 20 times (1x) by its own client at the same time, against "
        "one such run stepped alone.",
        "",
        "| | Mean step | p95 | Steps per second (all runs) |",
        "|---|---|---|---|",
        f"| One run alone | {many['alone']['mean']:.1f} ms | {many['alone']['p95']:.1f} ms | "
        f"{many['steps_per_second_alone']:.1f} |",
        f"| Eight runs at once | {many['together']['mean']:.1f} ms | {many['together']['p95']:.1f} ms | "
        f"{many['steps_per_second_together']:.1f} |",
        "",
        "Each run has its own lock, so runs never wait for each other's locks, but the backend is one Python "
        "process: simultaneous steps share one core, and total steps per second stays about the same while "
        "each visitor's steps take longer. With eight visitors of 500 vehicles each, every run still gets "
        f"{many['steps_per_second_together'] / 8:.1f} steps a second "
        f"({'faster' if many['steps_per_second_together'] / 8 >= 1 else 'slower'} than real time at 1x).",
        "",
        "## Memory",
        "",
        "| Server state | Resident memory |",
        "|---|---|",
        f"| Map loaded, no runs | {results['memory_idle']:.0f} MB |",
        f"| Eight runs of 502 vehicles | {many['memory_with_runs']:.0f} MB |",
        "",
        f"So about {(many['memory_with_runs'] - results['memory_idle']) / 8:.0f} MB per 500-vehicle run "
        "(including its traffic manager and route tables); `MAX_LIVE_RUNS` (default 8) bounds the total.",
        "",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--save", action="store_true", help=f"write {REPORT_FILE.relative_to(PROJECT_ROOT)}")
    args = parser.parse_args()

    server = start_server()
    try:
        rng = random.Random(SEED)
        results = {"memory_idle": rss_mb(server.pid)}
        with httpx.Client() as client:
            nodes = junctions(client, 120, rng)

        # First, while nothing else has run: Python keeps memory it has
        # freed, so runs made after bigger ones would reuse it and look
        # smaller than they are
        print("Several visitors...", flush=True)
        results["visitors"] = visitors(nodes, rng, server.pid)
        print("Request overhead...", flush=True)
        results["overhead"] = request_overhead()
        print("Routing...", flush=True)
        results["routing"] = routing(nodes, rng)
        print("Steps...", flush=True)
        results["steps"] = stepping(nodes, rng)
    finally:
        server.terminate()
        server.wait(timeout=30)

    text = report(results)
    print(text)
    if args.save:
        REPORT_FILE.write_text(text)
        print(f"Saved {REPORT_FILE.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
