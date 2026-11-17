# NORWICH//SIM

[![Tests](https://github.com/samiaa10/urban-traffic-sim/actions/workflows/tests.yml/badge.svg)](https://github.com/samiaa10/urban-traffic-sim/actions/workflows/tests.yml)

NORWICH//SIM is an urban traffic simulation and optimisation platform built on Norwich's real road network from OpenStreetMap. A Python/FastAPI backend does the routing and simulation, and a React/TypeScript map shows vehicles, congestion, road closures and emergency response live.

I built it as a computer science project to practise graph algorithms, simulation design, API design and testing on a realistic dataset. The main question it explores is: **what happens to traffic, and to ambulance response times, when roads get busy or close?**

> **Screenshots / demo video:** none yet. See the [demo guide](docs/DEMO.md) to try it yourself.

## Features

- **Real Norwich road network.** Downloaded from OpenStreetMap (drivable roads) and converted into a custom graph: 5,058 junctions and 10,980 directed roads. Every road keeps its OpenStreetMap attributes: class, speed limit (tagged on 98.2% of roads), lanes (23.8%), one-way and name (99.3%), with documented UK defaults where a tag is missing ([road network guide](docs/road_network.md)).
- **Two traffic models.** `osm` (the default) uses those attributes: cars keep to each road's speed limit, each direction of a road is counted on its own, and congestion depends on how full the road is (lanes x length), so a long dual carriageway absorbs traffic that jams a short side street. `simple` keeps the original rules below. Movement, routing, ETAs and delay measurement all use the run's model, so planned and driven times agree (checked on 200 Norwich trips).
- **A\* routing** with a great-circle (haversine) distance heuristic. Dijkstra is also implemented, and is used to check A\* gives the same optimal distance.
- **Live vehicle simulation.** Vehicles drive along their routes in one-second simulation steps, covering the real OpenStreetMap length of each road (the same lengths the router and optimiser use).
- **Congestion visualisation.** The backend counts vehicles on each road segment and reports a congestion level per segment, which the map draws.
- **Congestion slows vehicles down.** In the `simple` model the same levels set vehicle speed: a lone vehicle drives at full speed, a road with 2 vehicles runs at 60% and a road with 3 or more at 30%; the `osm` model slows vehicles smoothly as the road fills. Journeys therefore take longer in traffic, ambulance response times and ETAs include the delay, and the status panel shows how many vehicles are held up.
- **Travel-time routing.** Vehicles plan the *fastest* route, not the shortest: each road costs its length divided by the speed traffic and accidents allow on it right now, using the same speed rule that moves vehicles, so the planned time matches the driven time (tested). A\* with a straight-line estimate stays exact under congestion; landmark (ALT) lower bounds are also available.
- **Dynamic rerouting.** Vehicles re-plan as traffic changes and switch only when a route is meaningfully faster (by default at least 20 s and 10%), with staggered checks every 10 s and a 60 s cooldown so they do not flip-flop. A landmark lower bound skips searches that could not save enough. On Norwich it cut mean journey time by 8 to 9% at the default settings, and by 25% for the vehicles caught by a severe accident (see [Dynamic traffic](#dynamic-traffic-fleets-rerouting-accidents)).
- **Accidents.** Distinct from closures: the road stays open, but for a set duration traffic drives slower and the road jams with fewer vehicles. Minor, moderate and severe accidents differ in speed, capacity and default duration, and clear by themselves.
- **Reacting to change.** An accident or closure on a vehicle's route makes it re-plan at the start of the next second, before it moves. When a road reopens or an accident clears, the vehicles that had changed route to avoid it re-plan at once (and switch only to a route that uses it); everyone else picks up the change at their next routine check.
- **Fleets and traffic surges.** Spawn up to 2,000 vehicles per request with trips across Norwich from a simple demand model, or start a rush-hour style surge that keeps sending trips into (or out of) a zone for a while.
- **Congestion-aware shortest routes.** `/route` still finds the shortest route with a 50 m penalty per vehicle on a road (unchanged); vehicles in the simulation use travel-time routing.
- **Road closures.** Close or reopen a road segment. Closures apply in both directions, and every search avoids them.
- **Automatic rerouting after closures.** When a road closes, only vehicles whose remaining route uses it get a new route, rather than recalculating every vehicle.
- **Emergency services.** Ambulance, fire and police, each with its own stations, vehicles, speed and response target; an emergency only gets its own service. With the realistic (`osm`) rules the vehicle that would arrive first by road is sent, an emergency with every vehicle busy waits in a queue, vehicles drive back to their station, and other traffic gives way. The original rules (nearest station in a straight line, refuse when busy, instant return) stay with the `simple` model. Response time is measured in simulated seconds, and you can close a road ahead of the vehicle to watch it reroute.
- **Emergencies where people live.** Scenarios and the optimiser can place emergencies in proportion to the 2021 Census population of Norwich's 471 output areas (143,901 people; ONS, Open Government Licence), instead of evenly over the junctions.
- **Emergency station optimiser.** Ranks about 38 candidate station locations by average response time, worst case, and share of emergencies reached within a target time, using real road distances (not straight lines) and avoiding closed roads. The web interface shows the ten best as numbered markers on the map, a ranked table and a plain-English explanation of why #1 won. Also available from the command line and the API.
- **The optimiser tested in traffic.** Its top sites (or station sets) are run as full traffic simulations at several demand levels and seeds, all facing the same trips and emergencies, plus a run without emergencies to measure the response's effect on other traffic. The report ranks them by what really happened, with 95% intervals, the gap to the optimiser's estimate for the same emergencies, and Kendall's tau between the two orders ([results](docs/optimiser_evaluation.md)).
- **Multiple-station optimiser.** Chooses 2 to 5 stations that work well *together*, where each emergency is answered by the nearest chosen station. It reports average and worst-case response, coverage within a target time (3, 4, 5 or 8 minutes) and how many emergencies no chosen station can reach, and compares the result with three baselines: the best single station, the top-ranked stations picked one by one, and random stations. Results appear as numbered navy markers, a comparison table and a plain-English summary.
- **Performance engineering.** A grid spatial index for nearest-junction lookups (31x faster, identical answers), distance searches that no longer touch every junction per call (A\* 3.26 to 1.41 ms, now faster than Dijkstra), travel-time landmark bounds that keep speed-limited A\* exact while exploring half the junctions, batched rerouting for vehicles heading to the same place (2.9x per step when trips share destinations), exhaustive station-set search where feasible, parallel candidate searches for large optimiser runs, profiling of the main workloads ([profiling](docs/profiling.md)), and runs of 10,000 vehicles ([traffic benchmark](docs/traffic_benchmark.md)).
- **Benchmarks.** Scripts comparing Dijkstra vs A\*, shortest-path vs congestion-aware routing, rerouting strategies, and dynamic traffic at increasing fleet sizes.
- **Automated tests.** Backend tests (routing, simulation, optimiser, the FastAPI endpoints, and database tests against a real PostgreSQL), frontend logic tests and frontend interaction tests, plus a Docker Compose smoke test, all run on every push by GitHub Actions.
- **Logging.** The API logs start-up, each emergency dispatch and one line per request (method, path, status, time taken, run ID; as text or JSON) through Python's `logging` module; `LOG_LEVEL` controls how much. The once-a-second polling requests are logged at `DEBUG` only.
- **Production protections.** Rate limits on expensive requests per visitor, a 2 MB request limit, security headers, `GET /health` (the process is up) and `GET /ready` (and the database answers), runs left unused stopped after 30 minutes, and visitors who never replace each other's runs. If the database cannot be reached at start-up the API still starts and simulates, without saving, and `/ready` reports `degraded`.
- **Several simulations at once.** Every run has an ID. The original endpoints act on the run started by `/simulation/start`, and every one of them is also available as `/simulations/{id}/...`, so runs can be driven independently (each with its own lock, at most `MAX_LIVE_RUNS` in memory). The web interface uses its run's ID.
- **PostgreSQL persistence (optional).** With `DATABASE_URL` set, each run's history is saved as it happens: closures and accidents (and whether they expired or were cleared), stations and ambulances, emergencies from dispatch to arrival (or why nothing could be sent), fleets, surges, a timeline of events, and a metrics snapshot every simulated minute. After a restart, runs that were live come back as `interrupted` with their history intact. The data model is designed properly (13 tables for runs and optimiser results, 20 with the scenario and evaluation tables; rules the database enforces itself) and explained in [the architecture guide](docs/ARCHITECTURE.md#persistence). Saving adds no measurable time to a simulation step ([benchmark](docs/persistence_benchmark.md)).
- **Saved optimiser results.** Every optimiser answer is saved and can be listed and fetched again (`/optimiser/runs`); `reuse=true` answers an identical request from the database in about 6 ms instead of computing it again (about 750 ms).
- **Database migrations** with Alembic; the API refuses to start on a database that is not up to date.
- **Docker.** `docker compose up --build` runs PostgreSQL, the migrations, the backend and the frontend (served by nginx), checked end to end in CI.
- **Network-wide impact analysis.** For any simulated situation: total delay against free-flow conditions (vehicle-hours lost to congestion, accidents and closures), average, 95th percentile and worst journey-time increases, and the roads and 500 m areas that lose the most time, all measured from full simulations (delay is exact: a lone vehicle loses exactly 0 s, and road delay adds up exactly to trip delay).
- **Scenario simulation engine.** Describe a scenario (background demand, surges, road closures and accidents with start and end times, rerouting on or off, emergency stations and emergencies, a seed) and run it as a full simulation of the real Norwich network. The same scenario always gives the same result, and scenarios with the same seed have the same trips, so a disruption can be compared with its baseline trip by trip.
- **Scenario comparison.** Rank scenarios by clearly defined metrics (network delay, travel time, journey-time increase, completion, emergency response) and get a plain-English explanation of why one did better: what differs, how many trips were slower or faster, and which roads lost the extra time.
- **Large studies in the background.** Run hundreds or thousands of scenarios (one per closed road, many random closures, many seeds, station layouts) as a background job in a pool of worker processes; the API answers at once and reports progress. Measured: 400 full simulations in 21.5 minutes on 4 workers ([benchmark](docs/scenario_benchmark.md)).
- **Scenario analysis in the web interface.** Build scenarios, pick closures and accidents by clicking the map, run them, and see the ranking, explanation, charts of delay over time, and the most affected roads drawn on the map.
- **Saved runs and studies in the web interface.** The History section lists the 20 most recent saved runs and studies, shows a run's saved history, and opens any past study in the scenario results.
- **Restarting an ended run.** A run that ended (stopped, replaced, or interrupted by a server restart) can be started again from its saved setup: the same route, rerouting setting, stations and ambulances, the closures and accidents still in place when it ended (accidents with the time they had left), and its fleets with the same trips. Vehicles start from the beginning; the new run is linked to the old one (`POST /simulations/{id}/restart`, or **Restart** in the History section).
- **Resuming interrupted studies.** Studies a server restart interrupts are resumed when it starts again (`RESUME_STUDIES`, on by default): completed scenarios keep their results and only the rest run again, giving the same results as an uninterrupted study. Interrupted or cancelled studies can also be resumed by hand (`POST /scenarios/studies/{id}/resume`, or **Resume** in the History section).

## Tech stack

| Part | Technology |
|---|---|
| Backend | Python, FastAPI, Uvicorn |
| Database (optional) | PostgreSQL 16, SQLAlchemy 2.0, Alembic, psycopg 3 |
| Containers | Docker, Docker Compose, nginx (serves the frontend and forwards `/api`) |
| Map data | OpenStreetMap via OSMnx (used once, to download the network) |
| Frontend | React 19, TypeScript, Vite, Leaflet / react-leaflet |
| Testing | pytest + FastAPI `TestClient` (backend; database tests on a real PostgreSQL); Node's built-in test runner for frontend logic, Vitest + jsdom + React Testing Library for frontend interactions; GitHub Actions CI with a PostgreSQL service and a Docker Compose smoke test |
| Frontend tooling | ESLint, `tsc` |

## Getting started

### Prerequisites

- Python 3 (CI runs 3.11 and 3.12, and the Render deployment uses 3.12. It was originally developed on 3.9.6, which still passed the tests on 2026-11-13 but is past end of life and no longer checked by CI.)
- Node.js and npm (developed with Node 24)
- An internet connection for the first-time map download
- Optional: PostgreSQL 16 to save runs (step 6), or Docker to run everything in containers (see [Docker](#docker))

### 1. Clone

```bash
git clone https://github.com/samiaa10/urban-traffic-sim.git
cd urban-traffic-sim
```

### 2. Install the backend dependencies

From the project root:

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows (PowerShell): .venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

`requirements.txt` installs `fastapi`, `uvicorn`, the database packages (`sqlalchemy`, `alembic`, `psycopg[binary]`; installed always, used only when `DATABASE_URL` is set), `osmnx`, `pytest`, `httpx` (which FastAPI's test client needs) and `pyyaml` (which the tests that check `render.yaml` and the Docker files need). Versions are not pinned, except a major-version range for the database packages.

### 3. Download the Norwich road network

The backend cannot start without the road-network file. The finished Norwich map is committed to the repository (`data/osm/norwich_graph.pkl`, about 1.6 MB with road attributes, so the deployed backend has it), which means a fresh clone already has it and **you can skip this step**. Run it only to refresh the map from OpenStreetMap or if the file is missing (see [Road-network data](#road-network-data)):

```bash
python -m backend.routing.osm_loader
```

This downloads the drivable network for "Norwich, Norfolk, England, United Kingdom" from OpenStreetMap, converts it into the project's own graph, and saves it to `data/osm/norwich_graph.pkl`.

### 4. Run the backend

From the project root, with the virtual environment active:

```bash
uvicorn backend.api.main:app --reload
```

The API runs at `http://127.0.0.1:8000`. FastAPI's interactive documentation is at `http://127.0.0.1:8000/docs`.

The backend reads seven optional environment variables:

| Variable | Default | What it does |
|---|---|---|
| `CORS_ORIGINS` | `http://localhost:5173` | Comma-separated list of websites allowed to call the API from a browser |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL`. `INFO` logs start-up and a one-line summary of each emergency dispatch; `DEBUG` also logs every dispatched route point by point. Any other value stops the server at start-up |
| `NORWICH_SIM_GRAPH` | `data/osm/norwich_graph.pkl` | A different road-graph file (the API tests use a tiny one) |
| `DATABASE_URL` | *(not set)* | A PostgreSQL URL (`postgresql://user:password@host:5432/name`; `postgres://` also works) to save runs and optimiser results. Not set: everything stays in memory, as before. See step 6 |
| `MAX_LIVE_RUNS` | `8` | How many simulation runs may be in memory at once (1 to 100). Starting one more stops the run idle longest |
| `SCENARIO_WORKERS` | `min(4, CPUs)` | Worker processes that run scenario studies (0 to 64; 0 runs them one at a time inside the API process). Each holds its own copy of the map, 80 to 100 MB |
| `MAX_STUDY_SCENARIOS` | `1000` | The most scenarios one study may have (1 to 5,000) |
| `TRAFFIC_MODEL` | `osm` | The traffic model for runs and scenarios that do not ask for one: `osm` (OpenStreetMap speed limits, lanes and per-direction traffic) or `simple` (the original rules). A run or scenario can choose with `traffic_model`; saved runs and studies keep the model they used |
| `RATE_LIMIT_PER_MINUTE` | `30` | Expensive requests (starting runs, fleets and surges, the optimiser, scenario studies) each visitor may make per minute; more get `429` with `Retry-After`. Stepping, status and history are never limited. `0` turns the limit off |
| `TRUST_PROXY` | `false` | `true` behind a proxy that sets `X-Forwarded-For` (Render, the Docker nginx), so each visitor is limited separately rather than the proxy as a whole |
| `RUN_IDLE_MINUTES` | `30` | Runs nobody has used for this long are stopped (and saved as `stopped`) when another run starts. `0` keeps them until `MAX_LIVE_RUNS` needs the room |
| `LOG_FORMAT` | `text` | `json` writes the one-line-per-request log as JSON (method, path, status, milliseconds, run ID) for log search tools |

Run the backend as **one** process (no `--workers` above 1): live simulation runs are kept in that process's memory.

### 5. Run the frontend

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open the address Vite prints, which should be **`http://localhost:5173`**. Use `localhost`, not `127.0.0.1`: by default the backend only allows cross-origin requests from `http://localhost:5173` (change it with `CORS_ORIGINS`). The frontend calls the backend at `http://127.0.0.1:8000` unless `VITE_API_URL` is set when it is built or started (see `frontend/src/config.ts`).

Other frontend scripts in `package.json`: `npm run build` (type-check and build), `npm run lint`, `npm test` (all frontend tests: `test:unit` then `test:ui`), `npm run test:unit`, `npm run test:ui`, `npm run preview`.

### 6. Optional: save runs in PostgreSQL

Without a database everything works, but nothing survives a restart. To save runs and optimiser results, create a database and user (tested with PostgreSQL 16), bring the schema up to date, and start the backend with `DATABASE_URL`:

```bash
createdb norwich_sim                     # or create it however you prefer
export DATABASE_URL=postgresql://<user>:<password>@localhost:5432/norwich_sim
alembic upgrade head                     # from the project root; creates the tables
uvicorn backend.api.main:app --reload
```

Run `alembic upgrade head` again after pulling changes that add a migration; the API refuses to start on an out-of-date schema and says so. Saved runs are listed at `/simulations`, and each run's history is under `/simulations/{id}/history` (see the [demo guide](docs/DEMO.md), part B9). In the web interface, the **History** section (link in the header) lists saved runs and studies; it loads only when you click **Load saved runs and studies**.

Studies left unfinished when the backend stops are resumed when it starts again. Set `RESUME_STUDIES=false` to leave them `interrupted` instead (they can still be resumed by hand).

### Docker

To run everything (PostgreSQL, the migrations, the backend and the frontend) in containers, with Docker and Docker Compose installed. On a Mac without Docker Desktop, [Colima](https://github.com/abiosoft/colima) works (the smoke test below passes with it):

```bash
brew install colima docker docker-compose
mkdir -p ~/.docker/cli-plugins
ln -sfn "$(brew --prefix)/opt/docker-compose/bin/docker-compose" ~/.docker/cli-plugins/docker-compose
colima start --cpu 4 --memory 4          # once per login; `colima stop` when done
```

Then:

```bash
docker compose up --build
```

Then open **http://localhost:8080**. The API's own documentation is at http://localhost:8000/docs. The first build takes a few minutes. Start-up order is: the database (once healthy), then a one-off `migrate` container (`alembic upgrade head`), then the backend (once the migration has succeeded), then the frontend. nginx serves the frontend and forwards `/api/...` to the backend, so the browser only talks to one address.

- `Ctrl+C` or `docker compose down` stops it; saved runs stay in the `pgdata` volume. `docker compose down -v` deletes them.
- Settings go in a `.env` file next to `docker-compose.yml` (not committed), for example `POSTGRES_PASSWORD=...`, `FRONTEND_PORT=8081`, `BACKEND_PORT=8001` (if 8080 or 8000 is already in use), `LOG_LEVEL=DEBUG`, `MAX_LIVE_RUNS=4`.
- `python scripts/compose_smoke_test.py` builds and starts the stack under its own name and volume, checks it end to end (including that saved runs survive a backend restart) and removes it again. CI runs it on every push.

## Road-network data

| Item | Details |
|---|---|
| Source | OpenStreetMap, downloaded through OSMnx (`graph_from_place`, `network_type="drive"`) |
| Generated by | `python -m backend.routing.osm_loader` (`--cache-only` rebuilds it from the saved OpenStreetMap response in `cache/` without downloading) |
| Saved to | `data/osm/norwich_graph.pkl` |
| In Git? | **The graph file is** (`data/osm/norwich_graph.pkl`, about 1.6 MB with road attributes, so Render can deploy it), and so are the OpenStreetMap responses it was built from (`data/osm/source/`, 777 KB gzipped). The rest of `data/` and `cache/` are listed in `.gitignore` |
| Needed by | the API, the benchmarks, the optimiser command line tool |
| Not needed by | the test suite, which builds tiny hand-made graphs |

Things to know:

- The loader keeps each road's length (`length`), each junction's latitude/longitude, and each road's class, speed limit, lanes, one-way and name, in the project's own `Graph` class (`backend/routing/graph.py`, attributes in `graph.road_info`). How tags become numbers and how much of Norwich is tagged: [docs/road_network.md](docs/road_network.md).
- `cache/` (project root) is OSMnx's response cache. The loader first restores the committed responses from `data/osm/source/` into it, so `--cache-only` rebuilds exactly the committed graph (same junctions and roads) on any machine without downloading; without `--cache-only`, anything not cached is downloaded as today's (different) data.
- OpenStreetMap data changes over time, so a graph downloaded later may differ slightly from the one used for the benchmark figures below.
- The file is a Python pickle. Only load a `.pkl` you generated yourself, because unpickling untrusted files is unsafe.
- Map data is © OpenStreetMap contributors, available under the [Open Database Licence](https://www.openstreetmap.org/copyright). The committed graph and source responses are OpenStreetMap data or derived from it, so they are under the ODbL with the same credit (`data/osm/README.md`); the project's code is separate.

## Running the tests

### Backend

From the project root, with the virtual environment active:

```bash
python -m pytest backend/tests -v
```

The tests use small hand-built road networks (`backend/tests/helpers.py`, and a tiny grid map inside `test_api.py`), so they need neither the Norwich data file nor internet access.

Tests that load the real Norwich map, and the station-set search, need `numpy` (in `requirements.txt`). `backend/tests/test_api.py` and `test_sessions.py` test the HTTP endpoints through FastAPI's `TestClient`, so they need `fastapi` and `httpx` (both in `requirements.txt`); if either is missing those files are skipped and everything else still runs. The checks of `render.yaml`, `docker-compose.yml` and the CI workflow need `pyyaml` and are skipped without it.

**Database tests.** `backend/tests/db/` (125 tests) runs against a real PostgreSQL named by `TEST_DATABASE_URL`. The database is wiped at the start, so use a separate one whose name contains `test`:

```bash
createdb norwich_sim_test
TEST_DATABASE_URL=postgresql://<user>:<password>@localhost:5432/norwich_sim_test python -m pytest backend/tests -v
```

Without `TEST_DATABASE_URL` they are skipped: **678 tests pass and 124 are skipped**; with it, **all 802 run** (counted on 2026-11-17). Setting `REQUIRE_DATABASE_TESTS=1` (as CI does) makes a missing database a failure instead of a skip.

What they cover: Dijkstra/A\* agreement, the travel-time router against a reference Dijkstra with random slowdowns, landmark bounds, accidents (speed, capacity, expiry), real road lengths, dynamic rerouting (event-driven and routine, thresholds, cooldown, damping against an undamped policy, reacting before moving), fleets, surges, the demand model, a combined scenario checked every simulated second (fleet + surge + accident + closure and reopening), road closures, rerouting, vehicles slowing in traffic and the longer journey times that result, emergency dispatch and response time, the road-ahead closure scenario, the single- and multiple-station optimiser (including a check against trying every possible set of stations), the congestion benchmark's logic, and every API endpoint: normal use, missing simulation, unknown junctions, bad or missing parameters, one-way roads, closed roads and unreachable places. Stage 3 added: runs kept apart (closures, accidents, fleets, time, emergencies), the per-run endpoints answering exactly like the originals, overlapping requests for one run all applied once, the live-run limit, and the Docker files and CI workflow agreeing with the code. The database tests cover the migrations (upgrade, downgrade, upgrade again, models matching), every rule the database enforces, a run's whole saved history through the API, restarts (live runs come back `interrupted` with their history), a database failing at start and mid-run, zero SQL statements on quiet steps, and saved optimiser results coming back identical.

### Frontend

```bash
cd frontend
npm install        # once, to install the test tools
npm test           # runs both kinds of test below
```

There are two kinds of frontend test:

- **Logic tests** (`frontend/tests/`, `npm run test:unit`) use Node's built-in test runner and add no dependencies. They test the logic behind the simulation screen (status messages, traffic summary, route conversion), the optimiser's requests and error handling (with `fetch` replaced by a fake), and the optimiser result tables (rendered to HTML).
- **Interaction tests** (`frontend/ui-tests/`, `npm run test:ui`) use Vitest, jsdom and React Testing Library to render the real `App` and click through it like a person: choosing points on the map, calculating a route, watching the simulation run and finish, starting over, triggering a road closure, dispatching an ambulance and closing a road ahead of it, running the single- and multi-station optimiser (changing the number of stations and the target time), loading states, API failures, and clearing results and markers. The backend is replaced by a fake `fetch`, the one-second simulation timer is driven by the test (no waiting, no flakiness), and Leaflet is replaced by a simple stand-in that draws plain elements (real Leaflet needs a real browser layout), so the tests check what the app decides to draw, not Leaflet itself. There are 67 of them, including checks on the page structure (landmarks, navigation, empty states, metric cards) and that, with a backend that gives the run an ID, every run request goes to `/simulations/{id}/...`. The test helpers live in `frontend/ui-tests/` (`helpers.ts`, `fakeLeaflet.tsx`, `mapState.ts`, `setup.ts`), and `npm run test:ui` type-checks them with `tsconfig.ui-tests.json` before running them.

The app has no pause or reset button, so those are not tested: a simulation starts when a route is calculated, stops when every vehicle has finished or the backend stops answering, and a new map click starts over (that is tested).

CI (`.github/workflows/tests.yml`) runs:

- the backend tests on Python 3.11 and 3.12, each with a PostgreSQL 16 service container, after `alembic upgrade head` and `alembic check` (models and migrations agree), with every database test required to run;
- for the frontend: lint, build, the logic tests (`npm run test:unit`, 94) and the interaction tests (`npm run test:ui`, 67), as separate steps;
- a Docker job: builds both images, starts the Compose stack and runs `scripts/compose_smoke_test.py` (including a scenario study run by the worker processes).

## Benchmarks

All benchmark commands need the Norwich data file and are run from the project root.

### Dijkstra vs A\*

```bash
python -m backend.routing.benchmark
```

Routes 100 random start/destination pairs (seed 42) with both algorithms, checks they find the same distance, and prints average runtime and nodes explored. Results are printed only, not saved in the repository.

Before Stage 5 A\* explored 64.5% fewer nodes (759 vs 2,139 on average) but was slower in wall-clock time (0.83x Dijkstra's speed), because both searches set up a table over all 5,058 junctions on every call and A\* built a road key for every road it looked at. Since Stage 5 the tables fill in as junctions are reached and keys are only built when closures or traffic need them: on 2026-11-17 (Python 3.9.6, macOS on Apple silicon) A\* took 1.42 ms against Dijkstra's 2.07 ms (**1.46x faster**), with the same nodes explored and the same routes (main, run in the same session: 3.25 vs 2.72 ms).

### Congestion-aware routing

```bash
python -m backend.routing.benchmark_congestion
python -m backend.routing.benchmark_congestion --vehicles 2000 --journeys 100 --warmup 60 --seed 42 --save
```

Fills the map with traffic (vehicles heading for the city centre), then routes random journeys twice with the same A\* search, once ignoring traffic and once congestion-aware, and compares them on the same traffic snapshot. `--save` writes the report to [`docs/congestion_benchmark.md`](docs/congestion_benchmark.md).

Results from the saved report (run 2026-11-03, Python 3.12.3, Linux x86-64, seed 42, 2,000 vehicles):

| Average per journey | Shortest | Congestion-aware |
|---|---|---|
| Route distance | 4,153 m | 4,696 m (+13.1%) |
| Travel time with traffic | 518 s | 447 s (-13.6%) |
| Vehicles passed on route | 59.4 | 23.3 (-60.7%) |
| Search time | 4.74 ms | 7.66 ms |

In this snapshot, congestion-aware routing chose a different route for 68 of the 85 journeys. The routes are longer, but because vehicles now slow down on busy roads they are also quicker. "Travel time with traffic" drives each road at the speed the simulation allows for the vehicles on it in a frozen snapshot, so it is an estimate rather than a full simulation of each journey. (Both directions of a road are counted together, as they are everywhere in the simulation and in the routing penalty.) The full report is in the linked file. The earlier report (2026-10-21) was made before vehicles slowed down, so its figures are not comparable.

### Rerouting after a road closure

```bash
python -m backend.simulation.benchmark_rerouting
```

Closes the busiest road and compares recalculating routes only for affected vehicles against recalculating every vehicle. This command saves its report to [`docs/rerouting_benchmark.md`](docs/rerouting_benchmark.md) automatically, overwriting the existing one. In the saved run (2026-10-18, Python 3.9.6, seed 42, fastest of 3 runs), affected-only rerouting was 2.0x faster with 100 vehicles and 3.9x faster with 1,000.

### Dynamic traffic: fleets, rerouting, accidents

```bash
python -m backend.simulation.benchmark_traffic --traffic-model osm --save   # or simple
```

Full results, both traffic models and main before Stage 5, in [`docs/traffic_benchmark.md`](docs/traffic_benchmark.md) (2026-11-17, Python 3.9.6, macOS on Apple silicon, seed 42; timings depend on the machine):

**Fleet size** (osm model, every vehicle setting off at once; "per simulated second" is the wall-clock time of one step):

| Vehicles | Spawn | Per simulated second, rerouting off | Per simulated second, dynamic rerouting (worst) | Memory (spawn + 60 s) |
|---|---|---|---|---|
| 100 | 0.08 s | 0.6 ms | 2.9 ms (11 ms) | 1.5 MB |
| 500 | 0.47 s | 3.2 ms | 17.6 ms (124 ms) | 6.9 MB |
| 1,000 | 0.88 s | 6.4 ms | 44.6 ms (97 ms) | 13.5 MB |
| 2,000 | 1.73 s | 13.0 ms | 146.8 ms (906 ms) | 26.8 MB |
| 5,000 | 7.50 s | 62.3 ms | 777.3 ms (1.3 s) | 64.8 MB |
| 10,000 | 13.90 s | 120.4 ms | 1.33 s (2.8 s) | 128.2 MB |

Up to 5,000 vehicles a simulated second takes under a real second; 10,000 run at about 0.75x real time with rerouting (moving them alone is 8x faster than real time). The simple model gives exactly main's counts (reroutes, arrivals, journeys) at the same speed (143.5 against 141.8 ms at 2,000 vehicles).

**Does rerouting help?** In the simple model, same 600 trips, 20 simulated minutes: 444 s without dynamic rerouting, 407 s with the default policy (8% faster, 3 route changes per vehicle), 384 s with the eager policy. In the osm model roads rarely fill up at that size, so the default saves only 6 s (362 to 356 s) and almost never switches; the travel-time bound skips most checks before searching.

**A severe accident** on the busiest road for 20 minutes, 1,000 vehicles: in the osm model the accident cuts the road's capacity, a queue forms, and without dynamic rerouting only 1 of the 137 vehicles heading through it got past; with it all 137 arrived, in 474 s on average. (Simple model: 125 of 127 arrived without rerouting, averaging 779 s; 588 s with.)

**Batched rerouting** shares one search between vehicles heading to the same junction: 2.9x faster per step (244.9 to 83.9 ms) when 2,000 trips all go into a 250 m zone, with identical journeys; it makes no difference on ordinary demand, whose destinations are spread out.

### Emergency station optimiser

```bash
python -m backend.optimiser.run
python -m backend.optimiser.run --scenarios 1000 --target 600
python -m backend.optimiser.run --candidate 52.63,1.29 --candidate 52.62,1.31
python -m backend.optimiser.run --stations 3 --target 240
python -m backend.optimiser.validate
python -m backend.optimiser.evaluate
python -m backend.optimiser.run_evaluation --save        # the top 5 sites tested in traffic
```

- `run` ranks candidate stations (about 38 well-connected junctions spread over the map by default). With `--stations N` it instead chooses a set of N stations that work well together and compares it with the baselines.
- `validate` compares the optimiser's estimated response times with ambulances actually driven through the simulation: on empty roads, and through 1,000 cars with the realistic rules.
- `run_evaluation` tests the optimiser's top answers in full traffic simulations (also `POST /optimiser/evaluations` and the website's "Test the optimiser in traffic"); its results are in [docs/optimiser_evaluation.md](docs/optimiser_evaluation.md).
- `evaluate` checks the optimiser's pick against baselines and across random seeds, and checks the multiple-station chooser against trying every possible set. It only reads the optimiser, so it is safe to run at any time.

Results from a default run on the Norwich graph (5,058 junctions, seed 1, 500 random emergencies, 20 m/s ambulance speed, 8 minute target):

| Check | Result |
|---|---|
| Optimiser's pick vs a typical random junction | about 25% lower mean response time (2.65 vs 3.52 min) |
| Optimiser's pick vs the city centre | 2.65 vs 2.97 min |
| Stability across 10 random seeds | the same junction ranked first every time |
| Optimiser estimate vs full simulation (150 random pairs) | with the ambulance driving real road lengths (as it does in the API): +0.0% to +1.4%, mean +0.3% (the simulation rounds up to whole seconds). With roads measured as straight lines, as before Stage 2: -6.3% to +1.1%, mean -1.2% |
| A plain central junction, and the best of 160 random junctions | slightly better than the pick (2.58 and 2.52 min), see Limitations |

These figures are not stored by the scripts, so re-run the commands to reproduce them. They were first produced with Python 3.12, and re-running `evaluate`, `validate` and `run --stations 3 --target 240` on 2026-11-13 (Python 3.9.6, macOS) gave exactly the numbers in these tables (the real-road-length `validate` row was added on 2026-11-15). They are deterministic for a given map and seed.

#### Choosing several stations

Same graph, seed 1, 500 random emergencies, 20 m/s, with a 4 minute target (`python -m backend.optimiser.run --stations 3 --target 240`):

| 3 stations | Average | Worst case | Within 4 min | Unreached |
|---|---|---|---|---|
| Chosen set | 1.6 min | 3.6 min | 99.6% | 2 |
| Best single station | 2.7 min | 5.2 min | 85.2% | 2 |
| Top 3 ranked one by one | 2.0 min | 5.2 min | 89.2% | 2 |
| 3 random stations (average of 100) | 2.7 min | 5.8 min | 84.6% | 1.9 |

How the set is chosen (optimiser version 2): where there are up to 2 million possible sets, every one is scored in bulk with numpy, so the answer is the best there is (all 501,942 sets of 5 from the 38 candidates take about 1.5 s). Before, greedy picking plus swaps was used; checked against every set on 5 seeds it found the best 4- and 5-station sets 7 times in 10 but missed by 1.2%, 2.2% and 0.01% the other times, which is why exhaustive search replaced it. Larger searches use greedy plus swaps from 12 extra random starting sets. (Greedy alone was 8% and 11% worse than the best 2- and 3-station sets.)

With the default 8 minute target even one station reaches 99.6% of emergencies, because the model has no traffic and Norwich is compact, so coverage only separates the options at tighter targets (3 to 5 minutes). The 2 unreached emergencies can only be reached from 1 of the 38 candidates (probably because of one-way roads), and the chosen set does not include it, so they appear in every row.

### Persistence

```bash
DATABASE_URL=postgresql://... python -m backend.db.benchmark_persistence --save
```

Two apps on the Norwich map, one saving to PostgreSQL and one not, run identical simulations and take turns stepping. Saving made no measurable difference to a step (within ±3%, which is noise) at 2, 502 and 2,002 vehicles, with and without a surge: only 2 of 120 steps wrote anything, because a step writes only when something happened (and a metrics snapshot every simulated minute). For the optimiser (default candidates, 200 scenarios), computing took about 730 to 780 ms, saving added 3 to 5 ms, and a saved answer came back in 5 to 7 ms. Full results: [`docs/persistence_benchmark.md`](docs/persistence_benchmark.md).

### Scenario engine

```bash
DATABASE_URL=postgresql://... python -m backend.scenarios.benchmark_scenarios --save
```

Full simulations on the Norwich map, on this Mac (4 performance + 4 efficiency cores), measured before Stage 5 with the original (simple) traffic rules; not re-measured for the osm model:

| | |
|---|---|
| One scenario | 200 vehicles 5.7 s, 500 vehicles 16.2 s, 1,000 vehicles 38.5 s, 2,000 vehicles 104.6 s; 78 to 99 MB |
| Cost of measuring the impact | +2.2% |
| Parallel speed-up (16 scenarios of 500 vehicles) | 2 workers 1.97x, 4 workers 2.61x, 8 workers 3.52x |
| Saving a result | median 17 ms |
| A 400-scenario study (random closures, 300 trips each) | 21.5 minutes on 4 workers (about 1,100 an hour), 0 failures |

Speed-up flattens beyond 2 workers because each scenario itself slows down as more run at once on this machine's mixed cores; the job manager keeps every worker busy. In the 400-scenario study, closing 3 random roads within 1.5 km of the centre added a median 0.18 vehicle-hours of travel (worst 1.23), but 77 of the 399 closure sets made total travel shorter, by moving traffic off congested roads. Full results and how to read them: [`docs/scenario_benchmark.md`](docs/scenario_benchmark.md).

## Deploying to Render

This puts the app on the internet for free: the React frontend as a **static site** and the FastAPI backend as a **web service**, both built from this repository.

**What you get and what to expect (free plan)**

| | |
|---|---|
| Memory | 512 MB per service. Measured locally (peak resident memory of the Python process, Python 3.9.6 on macOS, 2026-11-13): about 52 MB once the API has started with the map loaded, and about 74 MB after a 5,000-scenario station ranking plus a 5-station set search. Render's Linux figures will differ a little, but there is plenty of headroom |
| Speed | 0.1 CPU. Everything works but is slower than on your computer, so optimiser searches may take several seconds |
| Sleeping | The backend sleeps after 15 minutes without requests, and the first request after that takes about a minute to wake it. The map page loads at once but shows "backend not running" errors until the backend is awake. Open the backend address first, wait for `{"name":"NORWICH//SIM","status":"online"}`, then use the site |
| Map data | The map file is committed in Git, so the deployed backend has it. Nothing is written to disk, so the free plan's temporary filesystem is not a problem |
| Logs | The backend's start-up and dispatch log lines appear in the service's **Logs** tab. Add a `LOG_LEVEL` environment variable (for example `DEBUG`) to change how much is logged; it is optional and not in `render.yaml` |
| Database | The Blueprint creates a free PostgreSQL database (`norwich-sim-db`) and connects the backend to it, so runs, histories and scenario studies are saved. **Render's free database expires 30 days after it is created** and is deleted 14 days after that unless upgraded; it has 1 GB and no backups. See [The database](#the-database) |
| Scenario studies | The Blueprint sets `SCENARIO_WORKERS=0` (0.1 CPU, 512 MB: one worker process alone holds 80 to 100 MB), so scenarios run one at a time inside the API. Keep studies small; large studies belong on a bigger machine. A study the backend's sleep interrupts is resumed when it wakes |

*These figures are Render's free-plan limits at the time of writing; check [render.com/pricing](https://render.com/pricing) if they matter to you.*

### Before you start

1. The repository must be on GitHub (or GitLab/Bitbucket) and pushed, including `render.yaml`, `requirements-deploy.txt`, `data/osm/norwich_graph.pkl` and `frontend/package-lock.json`. Check with `git ls-files data render.yaml requirements-deploy.txt frontend/package-lock.json`.
2. Create a free account at https://render.com and connect your GitHub account.

### Option A: one go, with the Blueprint (recommended)

1. In the Render dashboard click **New +**, then **Blueprint**.
2. Choose your repository and branch, then click **Connect**. Render reads `render.yaml` and lists a database, `norwich-sim-db`, and two services: `norwich-sim-api` and `norwich-sim`. (A workspace can have only one free database; if you already have one, Render will say so.)
3. Render asks for two values (they are not stored in Git). If the service names are free, the web addresses will be:

   | Variable | Belongs to | Value to enter |
   |---|---|---|
   | `VITE_API_URL` | `norwich-sim` (frontend) | `https://norwich-sim-api.onrender.com` |
   | `CORS_ORIGINS` | `norwich-sim-api` (backend) | `https://norwich-sim.onrender.com` |

   No trailing slash and no path. If Render says a name is taken it adds a suffix to the address (for example `norwich-sim-x7k2.onrender.com`). In that case finish creating the services, copy the real addresses from each service's page, and fix the two variables as described in step 5.
4. Click **Apply** and wait for both builds to finish (a few minutes).
5. **If an address differed from the table:** on the backend's page open **Environment**, set `CORS_ORIGINS` to the frontend's real address and save (it restarts). On the frontend's page open **Environment**, set `VITE_API_URL` to the backend's real address, then click **Manual Deploy** and choose **Clear build cache & deploy**. The frontend must be rebuilt because Vite writes this value into the JavaScript when it builds.

### Option B: create the two services by hand

Use this if you prefer to type the settings in yourself. Create the backend first.

**Backend: New + > Web Service**

| Setting | Value |
|---|---|
| Name | `norwich-sim-api` |
| Language / Runtime | Python 3 |
| Branch | your main branch |
| Root Directory | *(leave empty)* |
| Build Command | `pip install -r requirements-deploy.txt` |
| Start Command | `alembic upgrade head && uvicorn backend.api.main:app --host 0.0.0.0 --port $PORT` |
| Instance Type | Free |
| Health Check Path (Advanced) | `/` |
| Env var `PYTHON_VERSION` | `3.12.3` |
| Env var `CORS_ORIGINS` | the frontend's address, added after the frontend exists (below) |
| Env var `DATABASE_URL` | the database's **Internal Database URL** (see [The database](#the-database)); without it runs are kept in memory only |
| Env var `SCENARIO_WORKERS` | `0` |

**Frontend: New + > Static Site**

| Setting | Value |
|---|---|
| Name | `norwich-sim` |
| Branch | your main branch |
| Root Directory | `frontend` |
| Build Command | `npm ci && npm run build` |
| Publish Directory | `dist` |
| Env var `NODE_VERSION` | `22` |
| Env var `VITE_API_URL` | the backend's address, for example `https://norwich-sim-api.onrender.com` |

Then go back to the backend's **Environment** tab and set `CORS_ORIGINS` to the frontend's address (the `https://...onrender.com` address shown on the static site's page).

### Check that it works

1. Open `https://<your-backend>.onrender.com/` and wait (up to a minute if it was asleep). You should see `{"name":"NORWICH//SIM","status":"online"}`. `/docs` shows the API documentation.
2. Open the frontend address, click two points in Norwich, then **Calculate Route**. A route and moving vehicles mean it is working end to end.
3. Try **Find best ambulance station locations**. Results mean the optimiser works too.

Every push to the deployed branch redeploys both services automatically.

### If something goes wrong

| What you see | Likely cause and fix |
|---|---|
| Browser console says a request was blocked by CORS | `CORS_ORIGINS` on the backend does not exactly match the address in the browser's address bar (check `https`, spelling, no trailing path). Fix it and the backend restarts |
| "Could not calculate route. Is the backend running?" at first | The backend is waking up. Wait about a minute and try again |
| The site tries to reach `127.0.0.1:8000` (see the Network tab) | `VITE_API_URL` was not set when the frontend was built. Set it, then **Clear build cache & deploy** |
| Backend log says `FileNotFoundError ... norwich_graph.pkl` | The map is not in your repository. Check `git ls-files data` shows `data/osm/norwich_graph.pkl`, then commit and push |
| Backend build or start fails with a numpy or pickle error | The Python or numpy version is not what the map was saved with. Keep `PYTHON_VERSION` at `3.12.3` and `numpy>=2` in `requirements-deploy.txt` |
| Frontend build fails at `npm ci` | `package-lock.json` is out of date: run `npm install` in `frontend/` locally and commit the lockfile |

### The database

With the Blueprint, Render creates the database and sets the backend's `DATABASE_URL` to it. The backend's start command runs `alembic upgrade head` before starting, so the schema is created on the first deploy and brought up to date by every deploy that adds a migration (when it is already up to date this does nothing). The log then shows `Database schema is up to date`.

By hand (Option B): **New +** > **Postgres**, plan Free, then set the backend's `DATABASE_URL` to its **Internal Database URL** and use the start command in the table above.

**The free database expires.** Render's free PostgreSQL stops working 30 days after it is created, and is deleted 14 days after that unless you upgrade it; it has no backups. Once it has expired the backend can no longer reach it. To carry on for free, delete the expired database, create a new free one, and set the backend's `DATABASE_URL` to its Internal Database URL (the start command creates the tables); saved runs and studies are lost. To keep the app running without any database, delete `DATABASE_URL` from the backend's environment: runs are then kept in memory only, and history and studies answer 503.

*These are Render's free-database terms at the time of writing; check [render.com/docs/free](https://render.com/docs/free).*

### Limits of this deployment

- **Runs are kept apart, but there is one default run.** Each visitor's simulation has its own ID and its own vehicles and closures. Starting a new route makes that run the default and replaces the previous default, so if two people start routes at the same time, the first one's run is replaced (and their next step fails). Fine for a portfolio demo where one visitor looks at a time; not a multi-user service.
- **The optimiser is open to anyone**, and searches use the shared free CPU. There is no login or rate limiting.
- Restarting or waking the backend ends every live run. Without a database nothing is kept; with one, their history is kept, they are marked `interrupted`, and they can be restarted from their setup in the History section. Studies that were running are resumed.

## Demo

[`docs/DEMO.md`](docs/DEMO.md) is a short guide to route calculation, traffic simulation, road closures and ambulance rerouting, through both the web interface and the API.

## Project structure

```
.
├── backend/
│   ├── api/          FastAPI application: main.py (entry point), app.py, routes/, run registry, recorder
│   ├── db/           PostgreSQL: models, repositories, Alembic migrations, persistence benchmark
│   ├── scenarios/    scenario engine: spec, runner, impact measures, comparison, background jobs, sweeps
│   ├── routing/      graph, Dijkstra, A*, OSM loader, routing benchmarks
│   ├── simulation/   vehicles, traffic, closures, rerouting, emergencies, stations
│   ├── optimiser/    emergency station optimiser (+ CLI and validation)
│   ├── tests/        pytest suite (tests/db: database tests)
│   └── Dockerfile    backend image
├── frontend/         React + TypeScript + Vite map interface (+ tests/, ui-tests/, Dockerfile, nginx.conf)
├── docs/             architecture, demo guide, benchmark reports
├── data/osm/         the Norwich graph (committed so it can be deployed)
├── scripts/          compose_smoke_test.py
├── alembic.ini       database migrations
├── docker-compose.yml, .dockerignore      Docker
├── render.yaml, requirements-deploy.txt   Render deployment
└── .github/workflows/tests.yml
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for how the pieces fit together and the full endpoint list.

## Limitations

- **The traffic model is still a model.** The `osm` model uses real speed limits (tagged on 98.2% of roads) and lanes, but lanes are only tagged on 23.8% of roads, so most use a default (1 per direction, 2 on one-way trunk and primary roads). Free-flow speed is the speed limit, an upper bound: there are no traffic lights, junction delays or turning penalties. Congestion follows Greenshields' straight-line rule with a 30 m minimum road length, a modelling choice (the traffic benchmark shows how sensitive results are to it). A vehicle that crosses several short roads in one second uses that second's traffic. `/route` still adds a flat 50 m per vehicle. The `simple` model keeps the original 1/2/3-vehicle rules.
- **Accident figures are modelling choices.** The speed and capacity factors and default durations per severity (`backend/simulation/accidents.py`) are reasonable guesses, not measured data. An accident affects both directions of the road.
- **Trips are synthetic.** There is no employment or travel data: trip origins are spread over road junctions and about half of destinations are drawn towards the city centre (Gaussian falloff, scale 1.2 km). Trips are only made between the 4,143 of 5,058 junctions that can all reach each other. Emergencies can follow the 2021 Census population, but only where people live (not workplaces, roads or the town centre's daytime population).
- **Big events still cost one slow step.** An accident or reopening that affects a few hundred vehicles re-plans them all in the same simulated second. Batched rerouting shares searches between vehicles heading to the same junction, which helps a lot when trips share destinations (2.9x with trips into one zone) but almost never applies to ordinary Norwich traffic, whose destinations are spread over 1,500+ junctions.
- **A vehicle rerouted part-way along a road is briefly counted on a made-up road.** Its new route starts at its current position (an existing test relies on that), so for the rest of that road it is counted on a segment from that point to the next junction rather than on the real road: it does not add to that road's congestion count, and an accident on that road does not slow it, for those few seconds.
- **Finished vehicles stay in the vehicle list** (their position in the list is their id in the API), so every `/simulation/step` response grows as fleets and surges add vehicles. Stepping skips them cheaply, but a long surge produces a large response.
- **Fleets and surges have no buttons in the web interface yet.** They work through the API (see the demo guide). Vehicles added that way do appear and move on the map, because the map draws every vehicle `/simulation/step` returns.
- **Dispatch realism depends on the traffic model.** With `osm` the vehicle that would arrive first by road is sent, queues form when every vehicle is busy and vehicles drive back; with `simple` the original rules apply (nearest station in a straight line, refused when busy, instant return). Fire and police response targets are illustrative (there is no single national target); crews take no time to set off.
- **Live runs live in memory.** Runs have IDs and are kept apart, but they exist in one process's memory: the backend must run as a single worker, and a run cannot carry on after a restart from where it was (its vehicles' positions are not saved). With a database its history survives (it comes back `interrupted`) and it can be **restarted from its setup**: same route, stations, closures, accidents and fleet trips, but vehicles start again from the beginning, and surges and past emergencies are not repeated. Without a database, nothing survives. After a crash, a saved run's simulated time and vehicle counts can be up to a minute (simulated) behind; its events, incidents and emergencies are saved as they happen.
- **Scenario results have limits worth knowing.** Each scenario keeps its 25 most delayed roads and 10 most delayed areas (totals and per-trip figures are complete). A vehicle crossing into another road during a second has that second counted on the first road. A scenario that was running when the server stopped starts again from the beginning when its study is resumed (finished scenarios are kept). Comparing scenarios from different studies is only available through the API (`/scenarios/compare`).
- **The History section is short.** It lists the 20 most recent runs and studies (the API pages through all of them). Saved optimiser results are only available through the API (`/optimiser/runs`). The optimiser panel plans around the default run's closed roads rather than passing its own run's ID.
- **The API starts a small simulation.** `/simulation/start` creates two vehicles (start to destination and back, if the return route exists); add more with `/simulation/fleet` or `/traffic/surge`.
- **The optimiser's fast estimate ignores traffic**, so its ranking is a screen: "Test the optimiser in traffic" (or `run_evaluation`) checks its top answers in full simulations, which take minutes rather than seconds, and Render's free plan is too small for large ones. The screen still assumes a vehicle is always free; only the simulation models busy vehicles.
- **The optimiser only chooses between its candidate sites** (a grid snapped to well-connected junctions), so it can miss a slightly better junction between grid points: `evaluate` finds a central junction about 3% better and a best-of-160 random junction about 5% better. The multiple-station chooser is a heuristic (greedy plus swaps) that is only checked against the true best set for 2 and 3 stations. It assumes a station never runs out of ambulances, so it measures coverage by location, not capacity. Emergencies that no chosen station can reach are counted at the slowest response seen when choosing, so a few can be left unreached if reaching them would cost more elsewhere; always check the Unreached column.
- **Python sets the speed limit of the project.** Everything is pure Python apart from numpy in the station-set search, so with 10,000 vehicles a simulated second takes 1.3 s with rerouting (see the traffic benchmark), and the optimiser parallelises only its candidate searches, and only for 200+ candidates.
- **Frontend tests do not use real Leaflet or a real browser.** The interaction tests run in jsdom with a stand-in for the map, so map rendering, tile loading and real mouse behaviour on the map are not covered. There are no pause or reset buttons, and no authentication.

## Troubleshooting

| Problem | Likely cause and fix |
|---|---|
| `FileNotFoundError: ... norwich_graph.pkl` when starting the API | The map data has not been generated. Run `python -m backend.routing.osm_loader` from the project root. |
| `ModuleNotFoundError: No module named 'backend'` | Run commands from the project root, using `python -m ...` and `uvicorn backend.api.main:app`. |
| Frontend loads but requests fail | Check the backend is running on port 8000 and that you opened `http://localhost:5173` (not `127.0.0.1`). |
| `ModuleNotFoundError: No module named 'osmnx'` or `fastapi` | Activate the virtual environment and run `pip install -r requirements.txt`. |
| `SchemaOutOfDate: The database is at migration none, the code needs 0001. Run: alembic upgrade head` | Run `alembic upgrade head` from the project root with the same `DATABASE_URL`. |
| `DATABASE_URL must be a PostgreSQL URL` | Only PostgreSQL is supported; use a `postgresql://` (or `postgres://`) URL. |
| `503 ... History needs a database` | The backend was started without `DATABASE_URL`; history and saved optimiser results need one. |
| `409 Simulation has ended (interrupted)` | The run was live when the backend restarted (or it was stopped or replaced). Its history is still at `/simulations/{id}/history`. Start a new run, or **Restart** it from the History section (`POST /simulations/{id}/restart`). |
| `docker compose up` says a port is already allocated | Something else uses 8080 or 8000. Set `FRONTEND_PORT` / `BACKEND_PORT` in a `.env` file. |
| A scenario study answers `503 ... need a database` | Scenario studies are stored in PostgreSQL; start the backend with `DATABASE_URL`. |
| A study stays at "0 of N scenarios" | The first scenarios are still running (500 vehicles take about 16 s each), or `SCENARIO_WORKERS` processes could not start: check the backend log. |
| The backend on Render fails with database connection errors about a month after it was set up | Render's free database has expired. See [The database](#the-database). |
| Database tests are all skipped | Set `TEST_DATABASE_URL` to a PostgreSQL database whose name contains `test` (it is wiped). |
