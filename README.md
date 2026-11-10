# NORWICH//SIM

[![Tests](https://github.com/samiaa10/urban-traffic-sim/actions/workflows/tests.yml/badge.svg)](https://github.com/samiaa10/urban-traffic-sim/actions/workflows/tests.yml)

NORWICH//SIM is an urban traffic simulation and optimisation platform built on Norwich's real road network from OpenStreetMap. A Python/FastAPI backend does the routing and simulation, and a React/TypeScript map shows vehicles, congestion, road closures and emergency response live.

I built it as a computer science project to practise graph algorithms, simulation design, API design and testing on a realistic dataset. The main question it explores is: **what happens to traffic, and to ambulance response times, when roads get busy or close?**

> **Screenshots / demo video:** none yet. See the [demo guide](docs/DEMO.md) to try it yourself.

## Features

- **Real Norwich road network.** Downloaded from OpenStreetMap (drivable roads) and converted into a custom graph. The benchmark report records it as 5,058 junctions and 10,980 road segments.
- **A\* routing** with a great-circle (haversine) distance heuristic. Dijkstra is also implemented, and is used to check A\* gives the same optimal distance.
- **Live vehicle simulation.** Vehicles drive along their routes in one-second simulation steps.
- **Congestion visualisation.** The backend counts vehicles on each road segment and reports a congestion level per segment, which the map draws.
- **Congestion slows vehicles down.** The same levels set vehicle speed: a lone vehicle drives at full speed, a road with 2 vehicles runs at 60% and a road with 3 or more at 30%. Journeys therefore take longer in traffic, ambulance response times and ETAs include the delay, and the status panel shows how many vehicles are held up.
- **Congestion-aware routing.** Each vehicle on a road adds a 50 m penalty to its cost, so busy roads look "longer" to A\*.
- **Direction-aware traffic and ambulance priority.** Vehicles driving opposite ways on the same road are counted separately and do not slow each other. An ambulance on a call is not slowed by traffic, and its ETA matches that.
- **Road closures.** Close or reopen a road segment. Closures apply in both directions, and A\* avoids them.
- **Automatic rerouting.** When a road closes, only vehicles whose remaining route uses it get a new route, rather than recalculating every vehicle.
- **Emergency response.** Create emergency stations and ambulances, raise an emergency, and the nearest station with an available ambulance is dispatched along a route that accounts for traffic and closures. Response time is measured in simulated seconds, and you can close a road ahead of the ambulance to watch it reroute.
- **Emergency station optimiser.** Ranks about 38 candidate station locations by average response time, worst case, and share of emergencies reached within a target time, using real road distances (not straight lines) and avoiding closed roads. The web interface shows the ten best as numbered markers on the map, a ranked table and a plain-English explanation of why #1 won. Also available from the command line and the API.
- **Multiple-station optimiser.** Chooses 2 to 5 stations that work well *together*, where each emergency is answered by the nearest chosen station. It reports average and worst-case response, coverage within a target time (3, 4, 5 or 8 minutes) and how many emergencies no chosen station can reach, and compares the result with three baselines: the best single station, the top-ranked stations picked one by one, and random stations. Results appear as purple markers, a comparison table and a plain-English summary.
- **Benchmarks.** Scripts comparing Dijkstra vs A\*, shortest-path vs congestion-aware routing, and rerouting strategies.
- **Automated tests.** Backend tests (routing, simulation, optimiser and the FastAPI endpoints) and frontend tests, run on every push by GitHub Actions.

## Tech stack

| Part | Technology |
|---|---|
| Backend | Python, FastAPI, Uvicorn |
| Map data | OpenStreetMap via OSMnx (used once, to download the network) |
| Frontend | React 19, TypeScript, Vite, Leaflet / react-leaflet |
| Testing | pytest + FastAPI `TestClient` (backend); Node's built-in test runner for frontend logic, Vitest + jsdom + React Testing Library for frontend interactions; GitHub Actions CI |
| Frontend tooling | ESLint, `tsc` |

## Getting started

### Prerequisites

- Python 3 (developed on 3.9.6; CI runs 3.9 and 3.11. Other versions are untested.)
- Node.js and npm (developed with Node 24)
- An internet connection for the first-time map download

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

`requirements.txt` installs `fastapi`, `uvicorn`, `osmnx`, `pytest` and `httpx` (which FastAPI's test client needs). Versions are not pinned.

### 3. Download the Norwich road network

The backend cannot start without the road-network file. The finished Norwich map is committed to the repository (`data/osm/norwich_graph.pkl`, about 0.5 MB, so the deployed backend has it), which means a fresh clone already has it and **you can skip this step**. Run it only to refresh the map from OpenStreetMap or if the file is missing (see [Road-network data](#road-network-data)):

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

### 5. Run the frontend

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open the address Vite prints, which should be **`http://localhost:5173`**. Use `localhost`, not `127.0.0.1`: the backend only allows cross-origin requests from `http://localhost:5173`, and the frontend calls the backend at `http://127.0.0.1:8000` (both are hard-coded).

Other frontend scripts in `package.json`: `npm run build` (type-check and build), `npm run lint`, `npm test` (all frontend tests), `npm run test:unit`, `npm run test:ui`, `npm run preview`.

### Optional: run with Docker

If you have Docker installed, after generating the map (step 3) you can start both servers with one command instead of steps 4 and 5:

```bash
docker compose up --build
```

Then open http://localhost:5173. The backend container reads the map from your local `data/` folder (it is mounted rather than copied into the image). *Note: these Docker files were written to match the project but could not be run in the environment they were created in, so test them once on your machine.*

## Road-network data

| Item | Details |
|---|---|
| Source | OpenStreetMap, downloaded through OSMnx (`graph_from_place`, `network_type="drive"`) |
| Generated by | `python -m backend.routing.osm_loader` |
| Saved to | `data/osm/norwich_graph.pkl` |
| In Git? | **The graph file is** (`data/osm/norwich_graph.pkl`, about 0.5 MB, so Render can deploy it). The rest of `data/` and `cache/` are listed in `.gitignore` |
| Needed by | the API, the benchmarks, the optimiser command line tool |
| Not needed by | the test suite, which builds tiny hand-made graphs |

Things to know:

- The loader keeps each road's length (`length`) and each junction's latitude/longitude, and stores them in the project's own `Graph` class (`backend/routing/graph.py`). It does not keep street names or speed limits.
- A `cache/` folder may appear in the directory you ran the loader from. It looks like OSMnx's response cache, so repeated downloads are faster. It is safe to delete and is also ignored by Git.
- OpenStreetMap data changes over time, so a graph downloaded later may differ slightly from the one used for the benchmark figures below.
- The file is a Python pickle. Only load a `.pkl` you generated yourself, because unpickling untrusted files is unsafe.
- Map data is © OpenStreetMap contributors, available under the [Open Database Licence](https://www.openstreetmap.org/copyright). The committed graph is derived from it, so the same credit applies.

## Running the tests

### Backend

From the project root, with the virtual environment active:

```bash
python -m pytest backend/tests -v
```

The tests use small hand-built road networks (`backend/tests/helpers.py`, and a tiny grid map inside `test_api.py`), so they need neither the Norwich data file nor internet access.

`backend/tests/test_api.py` tests the HTTP endpoints through FastAPI's `TestClient`, so it needs `fastapi` and `httpx` (both in `requirements.txt`). If either is missing the file is skipped and everything else still runs. Without them, 155 tests run; `test_api.py` adds 127 more.

What they cover: Dijkstra/A\* agreement, road closures, rerouting, vehicles slowing in traffic and the longer journey times that result, emergency dispatch and response time, the road-ahead closure scenario, the single- and multiple-station optimiser (including a check against trying every possible set of stations), the congestion benchmark's logic, and every API endpoint: normal use, missing simulation, unknown junctions, bad or missing parameters, one-way roads, closed roads and unreachable places.

### Frontend

```bash
cd frontend
npm install        # once, to install the test tools
npm test           # runs both kinds of test below
```

There are two kinds of frontend test:

- **Logic tests** (`frontend/tests/`, `npm run test:unit`) use Node's built-in test runner and add no dependencies. They test the logic behind the simulation screen (status messages, traffic summary, route conversion), the optimiser's requests and error handling (with `fetch` replaced by a fake), and the optimiser result tables (rendered to HTML).
- **Interaction tests** (`frontend/ui-tests/`, `npm run test:ui`) use Vitest, jsdom and React Testing Library to render the real `App` and click through it like a person: choosing points on the map, calculating a route, watching the simulation run and finish, starting over, triggering a road closure, dispatching an ambulance and closing a road ahead of it, running the single- and multi-station optimiser (changing the number of stations and the target time), loading states, API failures, and clearing results and markers. The backend is replaced by a fake `fetch`, the one-second simulation timer is driven by the test (no waiting, no flakiness), and Leaflet is replaced by a simple stand-in that draws plain elements (real Leaflet needs a real browser layout), so the tests check what the app decides to draw, not Leaflet itself. There are 46 of them.

The app has no pause or reset button, so those are not tested: a simulation starts when a route is calculated, stops when every vehicle has finished or the backend stops answering, and a new map click starts over (that is tested).

CI (`.github/workflows/tests.yml`) runs the backend tests on Python 3.9 and 3.11, and lint, build and `npm test` (logic and interaction tests) for the frontend.

## Benchmarks

All benchmark commands need the Norwich data file and are run from the project root.

### Dijkstra vs A\*

```bash
python -m backend.routing.benchmark
```

Routes 100 random start/destination pairs (seed 42) with both algorithms, checks they find the same distance, and prints average runtime and nodes explored. Results are printed only, not saved in the repository.

### Congestion-aware routing

```bash
python -m backend.routing.benchmark_congestion
python -m backend.routing.benchmark_congestion --vehicles 2000 --journeys 100 --warmup 60 --seed 42 --save
```

Fills the map with traffic (vehicles heading for the city centre), then routes random journeys twice with the same A\* search, once ignoring traffic and once congestion-aware, and compares them on the same traffic snapshot. `--save` writes the report to [`docs/congestion_benchmark.md`](docs/congestion_benchmark.md).

Results from the saved report (run 2026-11-04, Python 3.12.3, Linux x86-64, seed 42, 2,000 vehicles):

| Average per journey | Shortest | Congestion-aware |
|---|---|---|
| Route distance | 4,153 m | 4,696 m (+13.1%) |
| Travel time with traffic | 518 s | 447 s (-13.6%) |
| Vehicles passed on route | 59.4 | 23.3 (-60.7%) |
| Search time | 4.74 ms | 7.66 ms |

In this snapshot, congestion-aware routing chose a different route for 68 of the 85 journeys. The routes are longer, but because vehicles now slow down on busy roads they are also quicker. "Travel time with traffic" drives each road at the speed the simulation allows for the vehicles on it in a frozen snapshot, so it is an estimate rather than a full simulation of each journey. (This report counts both directions of a road together, as the routing penalty does; the simulation itself now counts each direction separately.) The full report is in the linked file. The earlier report (2026-10-21) was made before vehicles slowed down, so its figures are not comparable.

### Rerouting after a road closure

```bash
python -m backend.simulation.benchmark_rerouting
```

Closes the busiest road and compares recalculating routes only for affected vehicles against recalculating every vehicle. This command saves its report to [`docs/rerouting_benchmark.md`](docs/rerouting_benchmark.md) automatically, overwriting the existing one. In the saved run (2026-10-18, Python 3.9.6, seed 42, fastest of 3 runs), affected-only rerouting was 2.0x faster with 100 vehicles and 3.9x faster with 1,000.

### Simulation throughput, memory, multiple incidents and optimiser runtime

```bash
python -m backend.simulation.benchmark_performance
```

Saves [`docs/performance_benchmark.md`](docs/performance_benchmark.md). Results measured on one Linux machine (Python 3.12.3, seed 42, fastest of 3 runs); yours will differ:

| Measurement | Result |
|---|---|
| Simulation speed, 100 / 500 / 1,000 / 2,000 vehicles | about 4,700 / 970 / 420 / 190 steps per second (roughly 385,000-485,000 vehicle updates per second) |
| Extra memory for 2,000 vehicles | about 0.7 MB (routes are shared); loading the map peaks at about 12.5 MB |
| Rerouting, 500 vehicles, 1 / 3 / 5 roads closed | 81 / 126 / 126 vehicles rerouted in about 0.7-1.0 s (roughly 7-8 ms per A* search) |
| Station optimiser, 38 candidates, 200 emergencies | 0.4 s to rank all candidates; 0.5 s to choose a set of 2-5 stations |

The Dijkstra vs A* result is in [`docs/routing_benchmark.md`](docs/routing_benchmark.md): A* explores about 65% fewer nodes but is not faster in wall-clock time in this pure-Python implementation (0.91x).

### Emergency station optimiser

```bash
python -m backend.optimiser.run
python -m backend.optimiser.run --scenarios 1000 --target 600
python -m backend.optimiser.run --candidate 52.63,1.29 --candidate 52.62,1.31
python -m backend.optimiser.run --stations 3 --target 240
python -m backend.optimiser.validate
python -m backend.optimiser.evaluate
```

- `run` ranks candidate stations (about 38 well-connected junctions spread over the map by default). With `--stations N` it instead chooses a set of N stations that work well together and compares it with the baselines.
- `validate` compares the optimiser's estimated response times with ambulances actually driven through the simulation.
- `evaluate` checks the optimiser's pick against baselines and across random seeds, and checks the multiple-station chooser against trying every possible set. It only reads the optimiser, so it is safe to run at any time.

Results from a default run on the Norwich graph (5,058 junctions, seed 1, 500 random emergencies, 20 m/s ambulance speed, 8 minute target):

| Check | Result |
|---|---|
| Optimiser's pick vs a typical random junction | about 25% lower mean response time (2.65 vs 3.52 min) |
| Optimiser's pick vs the city centre | 2.65 vs 2.97 min |
| Stability across 10 random seeds | the same junction ranked first every time |
| Optimiser estimate vs full simulation (150 random pairs) | simulation within -6.3% to +1.1% of the estimate, mean -1.2% |
| A plain central junction, and the best of 160 random junctions | slightly better than the pick (2.58 and 2.52 min), see Limitations |

These figures were produced with Python 3.12 and are not stored by the scripts, so re-run the commands to reproduce them (`validate` and `evaluate` were re-run after the congestion change and give the same numbers).

#### Choosing several stations

Same graph, seed 1, 500 random emergencies, 20 m/s, with a 4 minute target (`python -m backend.optimiser.run --stations 3 --target 240`):

| 3 stations | Average | Worst case | Within 4 min | Unreached |
|---|---|---|---|---|
| Chosen set | 1.6 min | 3.6 min | 99.6% | 2 |
| Best single station | 2.7 min | 5.2 min | 85.2% | 2 |
| Top 3 ranked one by one | 2.0 min | 5.2 min | 89.2% | 2 |
| 3 random stations (average of 100) | 2.7 min | 5.8 min | 84.6% | 1.9 |

How the set is chosen: greedy picking (add the station that improves the whole set most), then swapping each chosen station against each unchosen one while that helps. Greedy alone was 8% (2 stations) and 11% (3 stations) worse than the best possible set on Norwich; with swaps it found exactly the best set for both (checked against trying every set; `evaluate` reproduces this). For 4 or more stations trying every set is too slow, so there is no such check.

With the default 8 minute target even one station reaches 99.6% of emergencies, because the model has no traffic and Norwich is compact, so coverage only separates the options at tighter targets (3 to 5 minutes). The 2 unreached emergencies can only be reached from 1 of the 38 candidates (probably because of one-way roads), and the chosen set does not include it, so they appear in every row.

## Deploying to Render

This puts the app on the internet for free: the React frontend as a **static site** and the FastAPI backend as a **web service**, both built from this repository.

**What you get and what to expect (free plan)**

| | |
|---|---|
| Memory | 512 MB per service. The backend uses roughly 50 MB with the map loaded (measured: about 48 MB peak even running a 5,000-scenario station search, before the web server itself) |
| Speed | 0.1 CPU. Everything works but is slower than on your computer, so optimiser searches may take several seconds |
| Sleeping | The backend sleeps after 15 minutes without requests, and the first request after that takes about a minute to wake it. The map page loads at once but shows "backend not running" errors until the backend is awake. Open the backend address first, wait for `{"name":"NORWICH//SIM","status":"online"}`, then use the site |
| Map data | The map file is committed in Git, so the deployed backend has it. Nothing is written to disk, so the free plan's temporary filesystem is not a problem |

*These figures are Render's free-plan limits at the time of writing; check [render.com/pricing](https://render.com/pricing) if they matter to you.*

### Before you start

1. The repository must be on GitHub (or GitLab/Bitbucket) and pushed, including `render.yaml`, `requirements-deploy.txt`, `data/osm/norwich_graph.pkl` and `frontend/package-lock.json`. Check with `git ls-files data render.yaml requirements-deploy.txt frontend/package-lock.json`.
2. Create a free account at https://render.com and connect your GitHub account.

### Option A: one go, with the Blueprint (recommended)

1. In the Render dashboard click **New +**, then **Blueprint**.
2. Choose your repository and branch, then click **Connect**. Render reads `render.yaml` and lists two services: `norwich-sim-api` and `norwich-sim`.
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
| Start Command | `uvicorn backend.api.main:app --host 0.0.0.0 --port $PORT` |
| Instance Type | Free |
| Health Check Path (Advanced) | `/` |
| Env var `PYTHON_VERSION` | `3.12.3` |
| Env var `CORS_ORIGINS` | the frontend's address, added after the frontend exists (below) |

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

### Limits of this deployment

- **Everyone shares one simulation.** The backend keeps a single simulation in memory (as it always has), so two people using the site at the same moment would affect each other's vehicles and road closures. It is fine for a portfolio demo where one visitor looks at a time, but it is not a multi-user service.
- **The optimiser is open to anyone**, and searches use the shared free CPU. There is no login or rate limiting.
- Restarting or waking the backend resets the simulation, because nothing is saved to disk.

## Demo

[`docs/DEMO.md`](docs/DEMO.md) is a short guide to route calculation, traffic simulation, road closures and ambulance rerouting, through both the web interface and the API.

## Project structure

```
.
├── backend/
│   ├── api/          FastAPI application (backend/api/main.py)
│   ├── routing/      graph, Dijkstra, A*, OSM loader, routing benchmarks
│   ├── simulation/   vehicles, traffic, closures, rerouting, emergencies, stations
│   ├── optimiser/    emergency station optimiser (+ CLI and validation)
│   └── tests/        pytest suite
├── frontend/         React + TypeScript + Vite map interface (+ tests/ and ui-tests/)
├── docs/             architecture, demo guide, benchmark reports
├── data/osm/         the Norwich graph (committed so it can be deployed)
├── render.yaml, requirements-deploy.txt   Render deployment
├── docker-compose.yml  optional: run backend + frontend in containers
└── .github/workflows/tests.yml
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for how the pieces fit together and the full endpoint list.

## Limitations

- **The traffic model is simple.** Speed depends only on how many vehicles are on a road segment (full speed alone, 60% with two, 30% with three or more). Each direction of a road is counted separately, so vehicles passing in opposite directions do not slow each other (the map colours still show both directions together). Ambulances on a call have priority and are not slowed by traffic; an ambulance that is not on a call slows down like everyone else. A vehicle that crosses several short roads in one second uses the traffic from the start of that second. Congestion-aware routing still adds a flat 50 m per vehicle rather than predicting travel time.
- **Everything lives in memory.** The API holds one simulation in a global variable, so there is one simulation per server process, and it is lost when the server restarts.
- **The API starts a small simulation.** `/simulation/start` creates two vehicles (start to destination and back, if the return route exists). Large fleets (thousands of vehicles) are only used by the benchmark scripts.
- **Ambulances return to base instantly** after a call rather than driving back.
- **The optimiser ignores traffic** (it uses free-flowing road distance) and spreads emergencies evenly over road junctions, whereas real emergencies cluster where more people live. Response time is road distance divided by ambulance speed, plus any closures you supply.
- **The optimiser only chooses between its candidate sites** (a grid snapped to well-connected junctions), so it can miss a slightly better junction between grid points: `evaluate` finds a central junction about 3% better and a best-of-160 random junction about 5% better. The multiple-station chooser is a heuristic (greedy plus swaps) that is only checked against the true best set for 2 and 3 stations. It assumes a station never runs out of ambulances, so it measures coverage by location, not capacity. Emergencies that no chosen station can reach are counted at the slowest response seen when choosing, so a few can be left unreached if reaching them would cost more elsewhere; always check the Unreached column.
- **Nearest-junction lookup scans every junction**, which is simple but not optimised.
- **Frontend tests do not use real Leaflet or a real browser.** The interaction tests run in jsdom with a stand-in for the map, so map rendering, tile loading and real mouse behaviour on the map are not covered. There are no pause or reset buttons, and no authentication.

## Troubleshooting

| Problem | Likely cause and fix |
|---|---|
| `FileNotFoundError: ... norwich_graph.pkl` when starting the API | The map data has not been generated. Run `python -m backend.routing.osm_loader` from the project root. |
| `ModuleNotFoundError: No module named 'backend'` | Run commands from the project root, using `python -m ...` and `uvicorn backend.api.main:app`. |
| Frontend loads but requests fail | Check the backend is running on port 8000 and that you opened `http://localhost:5173` (not `127.0.0.1`). |
| `ModuleNotFoundError: No module named 'osmnx'` or `fastapi` | Activate the virtual environment and run `pip install -r requirements.txt`. |
