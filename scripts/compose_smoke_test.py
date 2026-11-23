"""
End-to-end check of the Docker Compose stack
--------------------------------------------

Run from the repository root (needs Docker; Python standard library only):

    python scripts/compose_smoke_test.py
    python scripts/compose_smoke_test.py --keep     # leave it running

It builds and starts everything with `docker compose up`, then talks to
it only through the frontend's nginx, exactly as a browser does:

  1. the site loads, and the migrate container finished successfully
  2. /api reaches the backend, which is connected to PostgreSQL
  3. a simulation run on the real Norwich map: station, ambulance,
     emergency, road closure, steps; the history endpoints show them
  4. an optimiser result is saved and can be fetched back
  5. a scenario study (two full simulations on the Norwich map) runs in
     the backend's worker processes and is ranked
  6. the backend container is restarted: the run comes back as
     "interrupted" with its history, the optimiser result and the study
     are still there, and nginx still reaches the restarted backend

It runs under its own Compose project name with its own volume, which is
deleted afterwards (unless --keep), so it never touches your data. On a
failure it prints the containers' logs.
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

PROJECT = os.environ.get("COMPOSE_PROJECT_NAME", "norwich-sim-smoke")
SITE = f"http://localhost:{os.environ.get('FRONTEND_PORT', '8080')}"
API = SITE + "/api"

# Norwich city centre and a point to the north-west (inside the map)
CENTRE = (52.6286, 1.2924)
NORTH_WEST = (52.6400, 1.2700)


class SmokeTestFailed(Exception):
    pass


def compose(*args, capture=False):
    command = ["docker", "compose", "--project-name", PROJECT, *args]
    print("$", " ".join(command), flush=True)
    result = subprocess.run(
        command, check=True, text=True,
        stdout=subprocess.PIPE if capture else None,
    )
    return result.stdout if capture else None


def request(method, path, params=None, expect=200, base=None, json_body=None):
    url = (base or API) + path
    if params:
        url += "?" + urllib.parse.urlencode(params)

    data = None
    headers = {}
    if json_body is not None:
        data = json.dumps(json_body).encode()
        headers["Content-Type"] = "application/json"
    elif method != "GET":
        data = b""

    req = urllib.request.Request(url, method=method, data=data, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            status, body = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, body = error.code, error.read()

    if status != expect:
        raise SmokeTestFailed(
            f"{method} {path}: expected {expect}, got {status}: {body[:300]!r}"
        )

    content = body.decode()
    try:
        return json.loads(content)
    except ValueError:
        return content


def check(condition, message):
    if not condition:
        raise SmokeTestFailed(message)
    print("  ok:", message, flush=True)


def wait_until_up(path, seconds=180):
    deadline = time.time() + seconds
    last = None

    while time.time() < deadline:
        try:
            return request("GET", path)
        except (SmokeTestFailed, OSError) as error:
            last = error
            time.sleep(2)

    raise SmokeTestFailed(f"{API + path} did not come up in {seconds} s: {last}")


def nearest(point):
    return request(
        "GET", "/nearest-node", {"latitude": point[0], "longitude": point[1]}
    )


def run_checks():
    # 1. The site and the migrations
    page = wait_until_up("/", seconds=240)
    check(page.get("status") == "online", "the API answers through nginx")

    index = request("GET", "/", base=SITE)
    check("NORWICH//SIM" in index, "the frontend is served")

    # The 3D map's worker: browsers only run it as a module worker when it
    # is served as JavaScript (the build saves it as .js: vite.config.ts)
    assets = compose("exec", "-T", "frontend", "ls", "/usr/share/nginx/html/assets", capture=True).split()
    workers = [name for name in assets if name.startswith("maplibre-gl-worker") and name.endswith(".js")]
    check(len(workers) == 1, "the 3D map's worker is in the build")
    head = urllib.request.Request(f"{SITE}/assets/{workers[0]}", method="HEAD")
    with urllib.request.urlopen(head, timeout=30) as response:
        served_as = response.headers.get("Content-Type", "")
    check(served_as.startswith(("application/javascript", "text/javascript")),
          f"the worker is served as JavaScript ({served_as})")

    migrate = compose("ps", "--all", "--quiet", "migrate", capture=True).split()
    exit_code = subprocess.run(
        ["docker", "inspect", "--format", "{{.State.ExitCode}}", migrate[0]],
        check=True, text=True, stdout=subprocess.PIPE,
    ).stdout.strip()
    check(exit_code == "0", "the migrate container finished successfully")

    listed = request("GET", "/simulations")
    check(listed["persistence"] is True, "the backend is connected to PostgreSQL")

    # 2. A simulation run
    start, destination = nearest(CENTRE), nearest(NORTH_WEST)
    run = request(
        "POST", "/simulations",
        {"start": start["node"], "destination": destination["node"]}, expect=201,
    )
    run_id = run["simulation_id"]
    base = f"/simulations/{run_id}"

    request("POST", base + "/stations",
            {"latitude": start["latitude"], "longitude": start["longitude"]})
    request("POST", base + "/stations/add-vehicle", {"station_id": 0})
    request("POST", base + "/emergencies",
            {"latitude": destination["latitude"], "longitude": destination["longitude"]})
    request("POST", base + "/fleet", {"count": 50, "seed": 1})

    for _ in range(5):
        request("POST", base + "/step")

    closed = request("POST", base + "/emergencies/block-ahead", expect=200)
    check("blocked_segment" in closed, "a road was closed ahead of the ambulance")

    history = request("GET", base + "/history")
    check(history["counts"]["stations"] == 1, "the station was saved")
    check(history["counts"]["emergencies"] == 1, "the emergency was saved")
    check(history["counts"]["closures"] == 1, "the road closure was saved")
    check(history["counts"]["fleets"] == 1, "the fleet was saved")

    # 3. An optimiser result
    answer = request("GET", "/optimiser/stations", {"scenarios": 20})
    check(answer["optimiser_run_id"] is not None, "the optimiser result was saved")

    # 4. A scenario study in the worker processes
    created = request("POST", "/scenarios/studies", expect=202, json_body={
        "name": "Smoke study",
        "scenarios": [
            {"name": "Baseline", "seed": 1, "demand": {"vehicles": 100}},
            {"name": "Busier", "seed": 1, "demand": {"vehicles": 200}},
        ],
        "baseline": 0,
    })
    study_path = f"/scenarios/studies/{created['study_id']}"
    deadline = time.time() + 300
    while True:
        study = request("GET", study_path)
        if study["status"] not in ("queued", "running") or time.time() > deadline:
            break
        time.sleep(2)
    check(study["status"] == "completed" and study["completed"] == 2,
          "a scenario study ran in the worker processes")
    check([row["name"] for row in study["ranking"]] == ["Baseline", "Busier"],
          "and ranked the busier scenario second on network delay")

    events_before = request("GET", base + "/history/events")["events"]

    # 5. Restart the backend
    compose("restart", "backend")
    wait_until_up("/")

    after = request("GET", base)
    check(after["status"] == "interrupted" and after["live"] is False,
          "after a restart the run is saved as interrupted")

    events_after = request("GET", base + "/history/events")["events"]
    check(events_after[:len(events_before)] == events_before,
          "its timeline survived the restart")
    check(events_after[-1]["type"] == "run_ended", "and ends with run_ended")

    request("POST", base + "/step", expect=409)
    print("  ok: an interrupted run cannot be stepped (409)")

    saved = request("GET", f"/optimiser/runs/{answer['optimiser_run_id']}")
    check(saved["answer"]["results"] == answer["results"],
          "the optimiser result survived the restart unchanged")

    kept = request("GET", study_path)
    check(kept["status"] == "completed" and kept["ranking"] == study["ranking"],
          "the scenario study survived the restart unchanged")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--keep", action="store_true", help="leave the stack running")
    parser.add_argument("--no-build", action="store_true", help="use images already built")
    arguments = parser.parse_args()

    os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

    failed = False

    try:
        compose("up", "--detach", *([] if arguments.no_build else ["--build"]))
        run_checks()
        print("\nCompose smoke test passed.")

    except (SmokeTestFailed, subprocess.CalledProcessError) as error:
        failed = True
        print(f"\nCompose smoke test FAILED: {error}\n", file=sys.stderr)
        try:
            compose("ps", "--all")
            compose("logs", "--no-color", "--tail", "200")
        except subprocess.CalledProcessError:
            pass

    finally:
        if not arguments.keep:
            compose("down", "--volumes", "--remove-orphans")

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
