# Architecture

This document explains how NORWICH//SIM is organised, so another developer can find their way around the code. For setup and run instructions, see the [README](../README.md).

## Overview

The project has two parts that talk over HTTP:

- **Backend** (`backend/`): Python. It holds the road graph, calculates routes, runs the simulation, and exposes everything through a FastAPI app.
- **Frontend** (`frontend/`): React + TypeScript. It draws the map and calls the backend.

```mermaid
flowchart TB
    OSM[(OpenStreetMap)] -->|"osm_loader.py (run once)"| PKL[("Norwich road graph<br/>data/osm/norwich_graph.pkl<br/>5,058 junctions, 10,980 segments")]

    subgraph FE["Frontend: React + TypeScript + Vite"]
        APP["App.tsx + OptimiserPanel<br/>controls, status panel, polling"]
        MAP["Leaflet map (react-leaflet)<br/>routes, vehicles, congestion, markers"]
        APP --> MAP
    end

    subgraph BE["Backend: FastAPI (backend/api/main.py)"]
        API["HTTP endpoints + CORS"]
        ROUTING["Routing engine<br/>Graph, A*, Dijkstra"]
        SIM["Simulation engine<br/>vehicles, congestion,<br/>closures, rerouting"]
        EMER["Emergency response<br/>stations, dispatch,<br/>ETA, response time"]
        OPT["Optimisation engine<br/>single + multi-station optimiser"]
        API --> SIM
        API --> ROUTING
        API --> EMER
        API --> OPT
        SIM --> ROUTING
        EMER --> SIM
        OPT --> ROUTING
    end

    PKL -->|"loaded at start-up"| API
    APP <-->|"HTTP + JSON<br/>127.0.0.1:8000"| API
    TILES[OpenStreetMap tile server] -->|map tiles| MAP

    subgraph QA["Testing and benchmarking"]
        PYT["pytest: routing, simulation,<br/>emergency, optimiser, API"]
        BEN["Benchmark scripts<br/>(two save to docs/*.md)"]
        FET["Frontend: node:test logic tests<br/>+ Vitest / React Testing Library"]
        CI["GitHub Actions CI"]
    end

    PYT -.-> BE
    BEN -.-> ROUTING
    BEN -.-> SIM
    BEN -.-> OPT
    FET -.-> FE
    CI -.-> PYT
    CI -.-> FET
```

The Norwich data is downloaded once and saved as a pickle file. The API loads it when it starts, and the benchmark and optimiser scripts load it themselves. See [Road-network data](../README.md#road-network-data).

## Folder guide

```
backend/
├── api/
│   ├── main.py                 FastAPI app: every HTTP endpoint, CORS, logging set-up, graph loading
│   └── settings.py             Reads CORS_ORIGINS and LOG_LEVEL (no FastAPI, so testable on its own)
├── routing/
│   ├── graph.py                Graph class (nodes with lat/lon, weighted directed edges)
│   ├── osm_loader.py           Downloads the Norwich network and saves it as a pickle
│   ├── a_star.py               A* search; optional traffic penalty and blocked roads
│   ├── dijkstra.py             Dijkstra search (used for comparison and benchmarking)
│   ├── benchmark.py            Dijkstra vs A* on random journeys (prints its results)
│   ├── benchmark_congestion.py Shortest-path vs congestion-aware routing (--save writes docs/congestion_benchmark.md)
│   └── routing_demo.py         Tiny standalone Dijkstra/A* demo on a 5-node graph
├── simulation/
│   ├── simulation.py           Simulation class: vehicles, time, closures, traffic and slowdown, emergencies, stations
│   ├── vehicle.py              Vehicle that follows a route (slows down when told the road is busy)
│   ├── rerouting.py            Finds vehicles affected by a closure and gives them new routes
│   ├── emergency.py            Emergency (location, state, response time)
│   ├── station.py              EmergencyStation (holds available emergency vehicles)
│   ├── dispatch.py             Emergency status (congestion-aware ETA), and the "close a road ahead of the ambulance" scenario
│   └── benchmark_rerouting.py  Affected-only vs recalculate-all rerouting benchmark (writes docs/rerouting_benchmark.md)
├── optimiser/
│   ├── station_optimiser.py    Ranks candidate station locations, chooses SETS of stations, baselines, explanations
│   ├── run.py                  Command-line interface for the optimiser (--stations N for a set)
│   ├── validate.py             Compares optimiser estimates with the full simulation
│   └── evaluate.py             Audit tool: compares the optimiser's pick with baselines, checks it is stable, checks multi-station choice against trying every set
└── tests/                      pytest suite (+ helpers.py with tiny test road networks)

frontend/
├── src/App.tsx                 The main interface: map, controls, simulation loop, API calls
├── src/simulationHelpers.ts    Plain functions App uses: status text, emergency text, traffic summary, route conversion
├── src/OptimiserPanel.tsx      Station optimiser controls, results area and map markers (single-station and station-set)
├── src/OptimiserResults.tsx    The optimiser's summaries, bar charts and tables (no Leaflet, so testable)
├── src/components.tsx          Shared pieces: inline icons, logo, panel and KPI card, spinner
├── src/mapStyles.ts            Colours, marker sizes and line weights used on the map (shared by the map, legend and tests)
├── src/config.ts               Backend address: VITE_API_URL at build time, else http://127.0.0.1:8000
├── src/index.css               The whole design system: colour tokens, layout, components, responsive rules
├── src/optimiser.ts            Optimiser response types, requests, error handling, number formatting
├── src/main.tsx                React entry point
├── tests/                      Logic tests (Node's test runner, `npm run test:unit`)
├── ui-tests/                   Interaction tests (Vitest + jsdom + React Testing Library, `npm run test:ui`)
│                               plus their helpers: helpers.ts, fakeLeaflet.tsx, mapState.ts, setup.ts
├── vitest.config.ts            Vitest settings for ui-tests/ (jsdom, setup file)
├── tsconfig.test.json          Compiles tests/ for Node's test runner
├── tsconfig.ui-tests.json      Type-checks ui-tests/ (Vitest does not)
└── vite.config.ts, package.json, tsconfig.json, tsconfig.app.json, tsconfig.node.json, eslint.config.js

docs/                           This file, the demo guide, saved benchmark reports (congestion, rerouting)
render.yaml, requirements-deploy.txt   Render deployment (backend web service + static frontend)
.github/workflows/tests.yml     CI: backend tests (Python 3.9 and 3.11); frontend lint, build, logic tests and interaction tests
```

## Key ideas

### The graph

`routing/graph.py` is a small custom class. Each node (junction) has a latitude and longitude, and each directed edge has a length in metres. Because OpenStreetMap roads can be one-way, a route from A to B may exist while B to A does not.

### Routing

`a_star` takes the graph, a start and a destination, and optionally `traffic` and `blocked_segments`:

- The heuristic is the great-circle distance to the destination.
- With `traffic`, each vehicle on a road adds `CONGESTION_PENALTY_METRES` (50 m) to that road's cost.
- With `blocked_segments`, those roads are skipped.
- With `return_stats=True` it also returns how many nodes it explored.

Dijkstra exists so A\* can be checked against it. Both must return the same distance without traffic.

### Road segments are identified by coordinates

A road segment is represented as a tuple of its two end points' `(latitude, longitude)` pairs, sorted so direction does not matter (`get_segment` in `a_star.py`, `segment_key` in `rerouting.py`). That is why closing a road blocks it in both directions. `rerouting.build_node_lookup` maps a coordinate pair back to its junction id, which the API builds once at start-up.

### Simulation time

`Simulation.time` goes up by 1 for every `step()`, which is one simulated second. Emergency response times use this simulated time rather than the computer's clock, so they don't depend on how fast the frontend polls. The frontend advances the simulation by repeatedly calling `POST /simulation/step`.

### Congestion and vehicle movement

Congestion is not only drawn on the map, it changes how vehicles move:

1. At the start of every `step()`, `get_traffic_density()` counts the vehicles on each road segment **once**, before anyone moves. Every vehicle sees the same snapshot, so the order of the vehicle list cannot change the result.
2. `congestion_level(count)` turns a count into a level (`clear`, `light`, `moderate`, `heavy`). The map colours roads by this level, and `SPEED_FACTORS` turns the same level into a fraction of full speed: 1.0 for clear or light (a lone vehicle is never slowed), 0.6 for moderate, 0.3 for heavy.
3. `Vehicle.move(time, is_blocked, speed_factor)` takes an optional function giving the speed factor for the road it is on. Movement is time-based: it works out how long the rest of the current road takes at the reduced speed, and carries any leftover time on to the next road. Without the function a vehicle drives at full speed, as before.
4. Each vehicle keeps `journey_seconds`, a clock that runs every step until it arrives, including time stuck behind a closed road. Congestion and closures both lengthen a journey, and `/simulation/step` reports `speed_factor` and `journey_seconds` per vehicle.
5. `dispatch.remaining_seconds` computes an ambulance's ETA the same way, using each remaining road's current speed factor, so the ETA shown in the status panel grows when the route is congested.

One traffic count drives everything: `get_traffic_density()` keys each road by `segment_between`, which sorts the two end points, so **both directions of a road are counted together**. The same count sets vehicle speed, colours the map, and feeds A\*'s congestion penalty. Every vehicle is slowed by it, ambulances on a call included (there is no ambulance priority). Simplifications: a vehicle that crosses several short roads in one second keeps using that second's snapshot. Routing still uses the flat 50 m per vehicle penalty rather than predicted travel time.

### Rerouting

When a road closes, `rerouting.reroute_affected_vehicles` first checks which vehicles' remaining routes use that road. This check is cheap. A\* only runs for those vehicles. A vehicle in the middle of a road finishes that road first, so the new route starts at the next junction it will reach. A vehicle with no alternative keeps its route (and waits at the closure until it reopens). The rerouting benchmark compares this against recalculating every vehicle.

### Emergencies

1. `add_station` creates an `EmergencyStation`, and `add_emergency_vehicle` puts an ambulance in it.
2. `create_emergency` records an emergency at a location, and `dispatch_from_nearest_station` sends an ambulance from the nearest station that has one available. "Nearest" is measured in a straight line on latitude/longitude, not by road. If no ambulance is free the API cancels the emergency and answers 404; there is no queue.
3. The API calculates the ambulance's route with A\*, using current traffic and closures, and gives it to the vehicle.
4. When the ambulance reaches the emergency, the emergency is resolved and the response time is recorded in simulated seconds. The ambulance becomes available again.
5. `dispatch.block_road_ahead` closes a road on the ambulance's route ahead of it and reroutes it using the same rerouting code as any other vehicle. It reports the extra distance and time of the detour.

`dispatch.py` deliberately has no FastAPI code in it, so the scenario can be tested on a tiny map.

### Station optimiser

For each candidate location, `station_optimiser.py` runs one Dijkstra search to get the distance to every junction. It reuses the same random emergencies for every candidate, so the comparison is fair. Response time is distance divided by ambulance speed. This is what keeps it fast: one search per candidate rather than one per emergency.

- **Candidates.** `dense_candidates` lays a 7x7 grid over the map and moves each point to the nearest well-connected junction (one that can reach at least 90% of the network). Duplicates and points with no well-connected junction nearby are dropped, which leaves about 38 candidates for Norwich.
- **Ranking.** Candidates are ranked by average response time. An emergency a station cannot reach counts as the slowest response seen in that run. Without this rule a station cut off from most of the map averaged only the few emergencies it *could* reach and ranked first (there is a regression test for this).
- **Explanation.** `explain_best` turns the numbers into a few plain sentences (why #1 won, and whether another candidate has a better worst case). The API returns them and the frontend displays them.
- **Checking it.** `validate.py` checks the estimate against ambulances driven through the full simulation. (Its ambulance has the road to itself; a car on the same road would count as traffic and slow it down, which is exactly what the simulation is meant to do but not what that check measures.) `evaluate.py` compares the winner with baselines (city centre, middle of the map, random junctions) on the same set of junctions and re-runs the optimiser with several seeds to check the answer is stable. It only reads the optimiser, so it is safe to run at any time.

### Choosing several stations

`optimise_station_set` reuses the same ingredients as the single-station ranking (`prepare_stations` snaps and searches from each candidate once, `sample_emergencies` draws the same random emergencies) and adds:

- **A table of response times.** `response_table` gives `times[candidate][emergency]` in seconds (`math.inf` if unreachable). A set of stations answers each emergency with the fastest member, and `measure_set` turns that into the same measures as a single station: average and worst response over the emergencies reached, percentage within the target out of *all* emergencies, how many were unreached, and the ranking value with unreached emergencies counted at the slowest response seen (the same rule as before, so a set of one station gives exactly the single-station numbers).
- **Selection.** `greedy_selection` adds, one at a time, the station that lowers the ranking value the most. Greedy can get stuck (the best first station is not always part of the best pair; Norwich: 8% to 11% worse than the best possible for 2 and 3 stations), so `refine_by_swapping` then tries replacing each chosen station with each unchosen one and keeps any improvement until none is left. `order_by_contribution` finally lists the stations in the order they add value. `exhaustive_selection` tries every set; it is only practical for small sizes, so it is used by the tests and `evaluate.py` to check the heuristic, not by the API.
- **Baselines.** The best single station; the `count` stations that rank best one by one (they tend to bunch together); and the average of 100 random sets. `explain_station_set` reports honestly if a baseline does as well or better.
- **Validation.** `check_settings` rejects scenarios below 1 and a speed or target that is zero, negative, NaN or infinite. (Before, speed 0 crashed with a division by zero and NaN or a non-positive target gave nonsense.)

Simplification: each emergency is treated on its own, so a station never runs out of ambulances.

### Logging

`main.py` sets up Python's `logging` with `logging.basicConfig`, at the level given by `LOG_LEVEL` (default `INFO`; `settings.log_level` validates it and refuses unknown names). The API logs, under the logger name `backend.api.main`:

- `INFO` at start-up: which graph file was loaded and its size.
- `INFO` for each emergency dispatch: type, location, vehicle, route length, junctions on the route and nodes explored.
- `DEBUG`: where the dispatched vehicle started and the full route, point by point.

`basicConfig` does nothing if logging is already set up (pytest's `caplog` relies on this), and uvicorn's own request logs keep uvicorn's format. The command-line tools (benchmarks, the optimiser CLI and `Simulation.run`'s demo) still `print`, because printing a report to the terminal is their job.

## API endpoints

All defined in `backend/api/main.py`. FastAPI generates interactive docs at `/docs`. Endpoints that act on the simulation return 404 if no simulation has been started.

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Health check (`{"name": "NORWICH//SIM", "status": "online"}`) |
| GET | `/nearest-node?latitude&longitude` | Nearest junction to a point |
| GET | `/route?start&destination` | A\* route between two junction ids. Uses the running simulation's traffic and closures if there is one. |
| POST | `/simulation/start?start&destination` | Start a simulation with a vehicle from start to destination, plus one coming back if a return route exists |
| GET | `/simulation/state` | Position and progress of vehicle 0 |
| POST | `/simulation/step` | Advance one simulated second, return all vehicles (position, `speed_factor` where 1.0 is free flowing, `journey_seconds`) |
| GET | `/simulation/congestion` | Vehicle count and congestion level per road segment |
| POST | `/simulation/reroute?vehicle_id` | Recalculate one vehicle's route using traffic and closures |
| POST | `/incident/block?start&destination` | Close the road between two adjacent junctions and reroute affected vehicles |
| POST | `/incident/unblock?start&destination` | Reopen a closed road |
| GET | `/incidents` | List closed roads |
| POST | `/station/create?latitude&longitude&station_type` | Add an emergency station |
| POST | `/station/add-vehicle?station_id&speed&vehicle_type` | Add an emergency vehicle to a station (`speed` must be above 0, otherwise 400) |
| POST | `/emergency/create?latitude&longitude&emergency_type` | Create an emergency and dispatch the nearest available vehicle |
| GET | `/emergency/status` | State, ETA, response time and reroutes for every emergency |
| POST | `/emergency/block-ahead` | Close a road ahead of the ambulance and reroute it |
| GET | `/optimiser/stations?candidates&scenarios&seed&target_seconds&speed` | Rank candidate station locations. `candidates` is optional (2 to 20 points written as `lat,lon;lat,lon`); leave it out to use the default spread of about 38 locations. Returns the ranked results and a plain-English explanation of the winner. 400 for bad candidates, `scenarios` outside 1 to 5000, or a `speed` or `target_seconds` that is not above 0 |
| GET | `/optimiser/station-set?stations&candidates&scenarios&seed&target_seconds&speed` | Choose `stations` (1 to 10, default 3) stations that work well together. Same options as above. Returns the chosen stations in pick order, the set's average, worst case, coverage within the target and unreachable count, three baselines, and a plain-English explanation. 400 if more stations are asked for than there are distinct candidate junctions |

## Frontend

The frontend is mostly one React component file, `frontend/src/App.tsx`, using `react-leaflet` for the map. The station optimiser panel and its map markers live in `frontend/src/OptimiserPanel.tsx`; the result tables are in `OptimiserResults.tsx` and the requests and types in `optimiser.ts`. Logic that does not need React (status messages, traffic summary, route conversion) lives in `simulationHelpers.ts`. It calls the backend at the address in `config.ts` (the `VITE_API_URL` build setting, defaulting to `http://127.0.0.1:8000` for local development), converts chosen map locations (latitude/longitude) into junction ids with `/nearest-node`, and runs the simulation by stepping the backend and re-drawing vehicles and congestion. It also polls `/emergency/status`. By default the backend's CORS setting only allows the origin `http://localhost:5173`; `CORS_ORIGINS` changes that (see `backend/api/settings.py`).

## Interface design

The screen is one workspace: a fixed navy header, a controls column on the left, and on the right a five-metric strip, the Norwich map (the largest element), and an analysis area for optimiser results. On narrow screens it becomes a single column in the order metrics, map, controls, results.

- **Design system.** `index.css` defines colour, type-scale, control-height, radius and elevation tokens as CSS variables (navy and slate for structure, a muted teal for positive/optimal results, amber and red only for warnings and emergencies) and the components built from them (panels, buttons, selects, the metrics strip, badges, notices, tables, bar charts). Hierarchy comes from surface contrast and spacing rather than outlines: the controls column is one surface split by hairlines, the metrics are one strip, and borders are kept for places that need them (secondary buttons, table rules). Buttons come in four variants (primary, secondary, destructive, quiet) with one shared disabled style. There are no UI or icon libraries: icons are inline SVG (`components.tsx`) and the charts are plain HTML bars.
- **Map.** The OpenStreetMap tiles are desaturated with a CSS filter so routes, congestion and markers stand out. Map colours, marker sizes and line weights live in `mapStyles.ts`: point markers share a white keyline, congestion is drawn as a wide translucent band under the thinner route line, and closures are the widest line. The toolbar shows what the next map click will do, the legend lists congestion plus only the layers currently on the map, and the latest action's message floats over the map instead of moving the layout.
- **Meaning of colour.** Route = blue, reroute (diversion) = violet so it cannot be confused with amber congestion, ambulance and emergency = red, closed road = near-black, best/optimal = green, chosen station set = navy. Congestion uses green, amber and red.
- **Accessibility.** Every group of controls is a named region, the header navigation and a skip link work with the keyboard, the simulation state is announced as a status, charts are written out as text beside each bar, the metric cards have a plain-sentence version for screen readers, and animations are switched off for people who ask for reduced motion.
- **What did not change.** All logic, requests and button labels are as before; the redesign only changed markup and styling (plus a loading spinner and disabled state on the button that was just clicked).

## Testing

**Backend.** Tests live in `backend/tests/` and use tiny hand-built graphs from `helpers.py`, so they run in a few seconds without the Norwich data. Files by area: `test_routing`, `test_road_closures`, `test_rerouting`, `test_emergency`, `test_emergency_response`, `test_edge_cases`, `test_station_optimiser`, `test_multi_station`, `test_congestion_movement`, `test_benchmark_congestion`, `test_settings` (`CORS_ORIGINS` and `LOG_LEVEL` parsing), `test_deployment_files` (`render.yaml` and the deploy requirements; the `render.yaml` checks need `pyyaml` and are skipped without it) and `test_api`.

`test_api.py` drives the real FastAPI app through `TestClient`. `main.py` reads the `NORWICH_SIM_GRAPH` environment variable (default: the Norwich pickle), and the test file points it at a small 4x4 grid map with a one-way dead end and an unconnected junction, so the endpoints are tested without the Norwich data. The file is skipped if `fastapi` or `httpx` is not installed. The simulation is a module-level variable in `main.py`, so a fixture resets it before every test.

**Frontend logic tests.** `frontend/tests/` holds tests run by `npm run test:unit`. They use Node's built-in test runner: `tsconfig.test.json` compiles the tests and the few source files they import (`simulationHelpers.ts`, `optimiser.ts`, `OptimiserResults.tsx`) into `.test-build/`, then `node --test` runs them. Components are rendered to HTML with `react-dom/server`; `fetch` is replaced by a fake. Source files that tests import must write their relative imports with the extension (`./optimiser.ts`).

**Frontend interaction tests.** `frontend/ui-tests/` holds tests run by `npm run test:ui` (Vitest, configured in `vitest.config.ts`, with jsdom and React Testing Library). They render the whole `App` and click through it. Three things are replaced: `fetch` (`helpers.ts` provides a fake backend where each test says what each endpoint answers, and records every request), `react-leaflet` (`fakeLeaflet.tsx` turns map objects into plain elements with `data-*` attributes, and `mapState.ts` lets a test "click the map"), and the app's one-second polling timer (`controlPolling` lets a test deliver exactly one simulation update at a time; other timers, such as Testing Library's own, run normally). `setup.ts` unmounts the app and undoes every fake after each test. The marker sizes the tests look for (`POINT`, `VEHICLE`, `EMERGENCY_SPOT`, ...) are read from `mapStyles.ts`, so a design change to sizes does not break them. `npm run test:ui` type-checks the tests with `tsconfig.ui-tests.json` first, because Vitest does not check types. Files: `simulation.test.tsx` (route, simulation, start over, failures, road closure), `emergency.test.tsx` (ambulance dispatch, progress, close road ahead, failures), `optimiser.test.tsx` (ranking, station set, loading, clearing, failures), `layout.test.tsx` (page structure and landmarks, navigation, empty states, route steps, metric cards, the busy state of Calculate Route). Real Leaflet and real browser layout are not covered.

`npm test` runs both kinds (`test:unit`, then `test:ui`).

GitHub Actions runs `python -m pytest backend/tests -v` on Python 3.9 and 3.11 (with `pytest fastapi httpx pyyaml` installed), and for the frontend `npm run lint`, `npm run build`, `npm run test:unit` and `npm run test:ui` as separate steps.

## Known design trade-offs

- The simulation is a module-level variable in `main.py`, which keeps the code simple but means one simulation per server process.
- The pickle format is quick to load but couples the data file to the `Graph` class and is not safe to load from untrusted sources.
- `frontend/src/App.tsx` holds all the UI logic in one file. Splitting it into components and a small API client would be a natural clean-up.
- The frontend has no pause or reset button: a simulation starts when a route is calculated and a new map click starts over.
- Routing still adds a flat 50 m per vehicle on a road instead of using predicted travel time. The congestion benchmark shows it helps (see `docs/congestion_benchmark.md`); predicted travel time would be a larger change.
- **Deployment (Render).** `render.yaml` describes a free Python web service (the backend, started with `uvicorn ... --port $PORT`) and a static site (the frontend, built with `npm run build`). The frontend learns the backend's address from `VITE_API_URL` at build time (`frontend/src/config.ts`), and the backend allows only the origins listed in `CORS_ORIGINS` (`backend/api/settings.py`). The Norwich graph is committed to Git so the backend can start without downloading anything. Because the simulation lives in one in-memory variable, a deployed instance is shared by everyone who uses it.
