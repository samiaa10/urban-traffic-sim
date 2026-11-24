# Architecture

This document explains how the Norwich Urban Traffic Simulator is organised, so another developer can find their way around the code. For setup and run instructions, see the [README](../README.md).

## Overview

The project has two parts that talk over HTTP, plus an optional database:

- **Backend** (`backend/`): Python. It holds the road graph, calculates routes, runs simulations (several at once, each with its own ID), and exposes everything through a FastAPI app.
- **Frontend** (`frontend/`): React + TypeScript. It draws the map and calls the backend.
- **PostgreSQL** (optional, Stage 3): when `DATABASE_URL` is set, every simulation run's history and every optimiser result is saved, and survives restarts. Without it the backend keeps everything in memory, exactly as before Stage 3. See [Persistence](#persistence).
- **Scenario engine** (Stage 4, `backend/scenarios/`): full simulations of different situations (demand, closures, accidents, surges, station layouts), run as background studies in worker processes, stored in PostgreSQL, ranked and explained; measured network-wide (delay, journey-time increase, the roads and areas hit hardest). See [Scenarios](#scenarios).

`docker compose up --build` runs all three, with nginx serving the frontend; see [Containers](#containers).

```mermaid
flowchart TB
    OSM[(OpenStreetMap)] -->|"osm_loader.py (run once)"| PKL[("Norwich road graph<br/>data/osm/norwich_graph.pkl<br/>5,058 junctions, 10,980 segments")]

    subgraph FE["Frontend: React + TypeScript + Vite"]
        APP["App.tsx: route, closures, emergencies,<br/>one update at a time"]
        LIVE["Live controls + live charts<br/>pause, speed, fleets, rush hour,<br/>incidents picked on the map"]
        PANELS["Optimiser, In traffic,<br/>Scenarios, History"]
        MAP["Leaflet map (react-leaflet)<br/>routes, vehicles, congestion, markers"]
        APP --> MAP
        LIVE --> APP
        PANELS --> MAP
    end

    subgraph BE["Backend: FastAPI (backend/api: app.py + routes/)"]
        PROT["protection.py<br/>rate limits, size limit,<br/>request log, headers"]
        API["HTTP endpoints + CORS<br/>original paths + /simulations/{id}/...<br/>/health, /ready"]
        PROT --> API
        REG["Run registry (sessions.py)<br/>live runs by ID, one lock each"]
        REC["Recorder (recorder.py)<br/>saves what happens to a run"]
        ROUTING["Routing engine<br/>Graph, A*, Dijkstra,<br/>RoadNetwork: travel-time A*, landmarks"]
        SIM["Simulation engine<br/>vehicles, congestion, accidents,<br/>closures, events"]
        TM["Traffic manager<br/>fleets, surges, demand model,<br/>dynamic rerouting"]
        EMER["Emergency response<br/>stations, dispatch,<br/>ETA, response time"]
        OPT["Optimisation engine<br/>single + multi-station optimiser,<br/>tested in traffic (evaluation.py)"]
        SCEN["Scenario engine (backend/scenarios)<br/>run, measure, compare;<br/>studies in worker processes"]
        API --> REG
        REG --> TM
        REG --> REC
        API --> SIM
        API --> ROUTING
        API --> EMER
        API --> OPT
        TM --> SIM
        TM --> ROUTING
        SIM --> ROUTING
        EMER --> SIM
        OPT --> ROUTING
        API --> SCEN
        SCEN --> TM
        OPT --> SCEN
    end

    PKL -->|"loaded at start-up"| API
    PG[("PostgreSQL (optional)<br/>runs, history, optimiser results,<br/>scenario studies")]
    SCEN -->|"results"| PG
    BACKUP["backup_database.sh<br/>weekly encrypted backup (Actions)"] -.-> PG
    REC -->|"SQLAlchemy<br/>(backend/db)"| PG
    OPT -.->|"results saved<br/>via the API"| PG
    APP <-->|"HTTP + JSON<br/>127.0.0.1:8000, or /api via nginx"| API
    TILES[OpenStreetMap tile server] -->|map tiles| MAP

    subgraph QA["Testing and benchmarking"]
        PYT["pytest: routing, simulation,<br/>emergency, optimiser, API,<br/>database (real PostgreSQL)"]
        BEN["Benchmark scripts<br/>(three save to docs/*.md)"]
        FET["Frontend: node:test logic tests<br/>+ Vitest / React Testing Library"]
        E2E["Playwright end-to-end:<br/>real browser, real backend,<br/>real Norwich map"]
        CI["GitHub Actions CI: 5 jobs<br/>(+ PostgreSQL service,<br/>Docker Compose smoke test)"]
    end

    PYT -.-> BE
    BEN -.-> ROUTING
    BEN -.-> SIM
    BEN -.-> OPT
    FET -.-> FE
    CI -.-> PYT
    CI -.-> FET
    CI -.-> E2E
    E2E -.-> FE
    E2E -.-> BE
```

The Norwich data is downloaded once and saved as a pickle file. The API loads it when it starts, and the benchmark and optimiser scripts load it themselves. See [Road-network data](../README.md#road-network-data).

## Folder guide

```
backend/
├── api/
│   ├── main.py                 Entry point (`uvicorn backend.api.main:app`): logging, reads the environment, creates the app
│   ├── app.py                  create_app(): builds an app with its own context, database and routers; start-up/shutdown
│   ├── context.py              AppContext: graph, RoadNetwork, optimiser candidates, run registry, database (shared, read-only)
│   ├── sessions.py             LiveRun (a simulation + traffic manager + lock + recorder) and RunRegistry (runs by ID)
│   ├── recorder.py             RunRecorder: saves a live run's history to the database (NullRecorder without one)
│   ├── settings.py             Reads CORS_ORIGINS, LOG_LEVEL, MAX_LIVE_RUNS, DATABASE_URL, the rate limit and proxy settings (no FastAPI, so testable on its own)
│   ├── protection.py           Rate limits, request size limit, request log, security headers
│   └── routes/                 The endpoints, one file per area:
│       ├── common.py           Finding a request's run (default or by ID), run_route (registers both paths), junction checks
│       ├── routing.py          /, /nearest-node, /route
│       ├── simulation.py       /simulation/start, /simulations (list, create, get, stop), step, state, congestion, reroute, fleet
│       ├── incidents.py        Closures and accidents
│       ├── emergencies.py      Dispatch, status, close a road ahead of the ambulance
│       ├── stations.py         Stations and their vehicles
│       ├── traffic.py          Surges, traffic status, the rerouting switch
│       ├── optimiser.py        /optimiser/stations, /optimiser/station-set, /optimiser/runs (saved results)
│       ├── history.py          /simulations/{id}/history/... (read from the database)
│       └── scenarios.py        /scenarios/...: studies, sweeps, results, comparison, presets, nearest road
├── scenarios/                  The scenario engine (Stage 4; see Scenarios)
│   ├── spec.py                 ScenarioSpec: what a scenario is; validation against the map
│   ├── runner.py               run_scenario: one full simulation, start to finish; World (per-process map data)
│   ├── impact.py               ImpactTracker: delay per trip, per road, per area, per minute
│   ├── compare.py              Metrics, ranking, trip-by-trip and road-by-road comparison, explanations
│   ├── jobs.py                 JobManager: studies in a pool of worker processes; results stored by the API process
│   ├── sweep.py                Large studies from one scenario: seeds, each closure, random closures, station layouts
│   └── benchmark_scenarios.py  Cost by fleet size, measuring overhead, parallel speed-up, saving, a 400-scenario study
│                               (--save writes docs/scenario_benchmark.md)
├── db/
│   ├── models.py               SQLAlchemy 2.0 models: the 20 tables (see Persistence and Scenarios)
│   ├── repository.py           RunRepository and OptimiserRepository: all database reads and writes
│   ├── scenario_repository.py  ScenarioRepository: studies, scenarios and their results
│   ├── serializers.py          Rows -> the API's JSON (including rebuilding optimiser answers exactly)
│   ├── database.py             Database: engine, transactions, refuses an un-migrated schema
│   ├── engine.py               Engine and session factory
│   ├── benchmark_persistence.py  What saving costs a step; optimiser compute vs saved (--save writes docs/persistence_benchmark.md)
│   └── migrations/             Alembic environment and versions/ (0001_initial_schema.py, 0002_scenario_studies.py, 0003_restarted_runs.py)
├── routing/
│   ├── graph.py                Graph class (nodes with lat/lon, weighted directed edges)
│   ├── osm_loader.py           Downloads the Norwich network and saves it as a pickle
│   ├── a_star.py               A* search; optional traffic penalty and blocked roads
│   ├── travel_time.py          RoadNetwork: lengths and segment keys built once, fastest-route A* (travel time),
│   │                           main connected area (Kosaraju), landmark (ALT) lower bounds
│   ├── dijkstra.py             Dijkstra search (used for comparison and benchmarking)
│   ├── benchmark.py            Dijkstra vs A* on random journeys (prints its results)
│   ├── benchmark_congestion.py Shortest-path vs congestion-aware routing (--save writes docs/congestion_benchmark.md)
│   └── routing_demo.py         Tiny standalone Dijkstra/A* demo on a 5-node graph
├── simulation/
│   ├── simulation.py           Simulation class: vehicles, time, closures, accidents, events, traffic and slowdown,
│   │                           emergencies, stations; speed_factor_on_road (the one road-speed rule)
│   ├── vehicle.py              Vehicle that follows a route (slows down when told the road is busy; real road lengths)
│   ├── accidents.py            Accident: severity -> speed and capacity factors, duration
│   ├── demand.py               DemandModel (where trips start and end) and Zone
│   ├── traffic_manager.py      TrafficManager: fleets, surges, event reactions, dynamic rerouting (ReroutePolicy)
│   ├── benchmark_traffic.py    Search comparison, fleet-size scaling, accident scenario, rerouting policies
│   │                           (--save writes docs/traffic_benchmark.md)
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
    └── db/                     Database tests (need TEST_DATABASE_URL: a real PostgreSQL)

frontend/
├── src/App.tsx                 The main interface: map, controls, simulation loop, API calls
├── src/simulationHelpers.ts    Plain functions App uses: status text, emergency text, traffic summary, route conversion,
│                               which run a request is for (runPath, simulationIdFrom)
├── src/OptimiserPanel.tsx      Station optimiser controls, results area and map markers (single-station and station-set)
├── src/ScenarioWorkspace.tsx   Scenario analysis: builder (roads picked on the map), study progress, ranking, charts, details
├── src/scenarios.ts            Scenario types, requests, study requests from the builder, formatting, chart helpers
├── src/OptimiserResults.tsx    The optimiser's summaries, bar charts and tables (no Leaflet, so testable)
├── src/components.tsx          Shared pieces: inline icons, logo, panel and KPI card, spinner
├── src/mapStyles.ts            Colours, marker sizes and line weights used on the map (shared by the map, legend and tests)
├── src/config.ts               Backend address: VITE_API_URL at build time, else http://127.0.0.1:8000
├── src/index.css               The whole design system: colour tokens, layout, components, responsive rules
├── src/optimiser.ts            Optimiser response types, requests, error handling, number formatting
├── src/main.tsx                React entry point
├── tests/                      Logic tests (Node's test runner, `npm run test:unit`)
├── ui-tests/                   Interaction tests (Vitest + jsdom + React Testing Library, `npm run test:ui`)
├── e2e/                        End-to-end tests (Playwright, real browser and backend, `npm run test:e2e`)
│                               plus their helpers: helpers.ts, fakeLeaflet.tsx, mapState.ts, setup.ts
├── vitest.config.ts            Vitest settings for ui-tests/ (jsdom, setup file)
├── tsconfig.test.json          Compiles tests/ for Node's test runner
├── tsconfig.ui-tests.json      Type-checks ui-tests/ (Vitest does not)
├── Dockerfile, nginx.conf      Frontend image: Vite build served by nginx, which forwards /api to the backend
└── vite.config.ts, package.json, tsconfig.json, tsconfig.app.json, tsconfig.node.json, eslint.config.js

docs/                           This file, the demo guide, saved benchmark reports (congestion, rerouting, traffic, persistence,
                                scenarios)
alembic.ini                     Alembic settings (the database address always comes from DATABASE_URL)
backend/Dockerfile, .dockerignore   Backend image (also runs the migrations)
docker-compose.yml              db -> migrate -> backend -> frontend
scripts/compose_smoke_test.py   Builds and starts the Compose stack and checks it end to end (CI runs it)
render.yaml, requirements-deploy.txt   Render deployment (PostgreSQL database, backend web service, static frontend)
.github/workflows/tests.yml     CI: backend tests on PostgreSQL (Python 3.11 and 3.12) with migration checks; frontend lint,
                                build, logic and interaction tests; Docker build + Compose smoke test
```

## Key ideas

### The graph

`routing/graph.py` is a small custom class. Each node (junction) has a latitude and longitude, and each directed edge has a length in metres. Because OpenStreetMap roads can be one-way, a route from A to B may exist while B to A does not.

`graph.road_info[(from, to)]` keeps each directed road's OpenStreetMap attributes as a `RoadInfo` (`routing/road_attributes.py`): road class, speed limit (m/s), lanes in its own direction, one-way, and name, each marked as tagged or defaulted. The edges themselves are unchanged, so every search and the optimiser work as before; maps without attributes (the hand-made test maps) simply have an empty `road_info`. How tags become numbers, the defaults, and how much of Norwich is tagged are in [the road network guide](road_network.md).

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
3. `speed_factor_on_road(count, accident)` is **the one rule for how fast a road is**: the level's factor, after an accident's lost capacity raises the effective count, times the accident's own speed factor. `Simulation.road_speed_factor(segment, count)` looks up the accident. Movement, ETAs and the travel-time router all call it.
4. `Vehicle.move(time, is_blocked, speed_factor)` takes an optional function giving the speed factor for the road it is on. Movement is time-based: it works out how long the rest of the current road takes at the reduced speed, and carries any leftover time on to the next road. Without the function a vehicle drives at full speed, as before.
5. Each vehicle keeps `journey_seconds`, a clock that runs every step until it arrives, including time stuck behind a closed road. Congestion and closures both lengthen a journey, and `/simulation/step` reports `speed_factor`, `journey_seconds` and `reroutes` per vehicle.
6. `dispatch.remaining_seconds` computes an ETA the same way, so the ETA shown in the status panel grows when the route is congested or has an accident.

One traffic count drives everything: `get_traffic_density()` keys each road by its traffic key (below). The same count sets vehicle speed, colours the map, and feeds both routers. Every vehicle is slowed by it, ambulances on a call included (there is no ambulance priority). Simplification: a vehicle that crosses several short roads in one second keeps using that second's snapshot.

### Traffic models

Each `Simulation` has a road model (`simulation/road_model.py`), chosen per run (`traffic_model` on `/simulation/start` and `/simulations`), per scenario (`ScenarioSpec.traffic_model`), or by the server's `TRAFFIC_MODEL` (default `osm`). Everything that needs a road's speed asks it: movement, routing, ETAs and delay measurement, so planned and driven times stay equal in both models (tested on random maps with random limits).

| | `simple` (the original rules, above) | `osm` |
|---|---|---|
| Traffic key | sorted end points: both directions counted together | end points in driving direction: each direction on its own |
| Free-flow speed | the vehicle's own speed everywhere | `min(vehicle speed, the road's limit)`; cars get a top speed of 70 mph, so the limit decides. Emergency vehicles are exempt (UK law allows it on a call, and the station optimiser assumes it) |
| Congestion | 1 vehicle free, 2 at 60%, 3+ at 30% | Greenshields: `factor = max(0.05, 1 - others / capacity)`, capacity = lanes x max(length, 30 m) / 7.5 m. Only *other* vehicles count, so a lone vehicle is never slowed; the 5% floor keeps a queue creeping |
| Accidents | raise the effective count, then their speed factor | divide the capacity by their capacity factor, then their speed factor |
| Map colours | by count | by speed factor: light >= 0.8, moderate >= 0.4, heavy below. Each road is drawn once, in its busier direction's colour |

Closures and accidents always cover both directions in both models. The shortest-route search (`a_star.py`, `/route`) keeps its flat 50 m penalty per vehicle; `Simulation.get_segment_traffic()` gives it both directions together whatever the model.

In the osm model, `RoadNetwork.fastest_path(..., directed=True, limits=True)` costs each road at `length / (min(speed, limit) x factor)`; its estimate divides the straight-line (or landmark) distance by the fastest speed anywhere on the network, so it still never overestimates. `Vehicle.move` takes the limit as a `limit_factor` (the limit as a fraction of the vehicle's speed) and records `last_free_flow_seconds`, so `scenarios/impact.py` measures delay as time taken minus the time at the speed limits: a lone vehicle still loses exactly 0 s.

**The experimental `ca` model** (`simulation/cellular.py`, migration `0007` allows it in saved runs) keeps the osm model's roads, limits and lanes but moves ordinary cars with a Nagel-Schreckenberg cellular automaton: each lane of each directed road is a row of 2.5 m cells, cars take 3 cells, and every second they accelerate, brake to the gap ahead, sometimes slow at random, and move. `Simulation.step` hands ordinary cars to it when `road_model.cellular` is true (the other models never take that branch); emergency vehicles move as in the osm model. The mean speed the CA measures on each road becomes that road's speed factor, so routing, ETAs, rerouting and the congestion map read the CA's traffic. Design, validation and the comparison with osm: [`CELLULAR_AUTOMATON.md`](CELLULAR_AUTOMATON.md).

**Disruptions** (`simulation/disruptions.py`, `infrastructure.py`): closures remember who closed each road (`block_segment(segment, source)`), so overlapping closures, floods and failures reopen a road only when all are over; floods (`Simulation.add_flood`) close or slow a set of roads and recover gradually; `Simulation.road_incident` combines an accident and floods on one road into the speed and capacity factors every traffic model already applies. Scenarios take `floods`, `failures` and `resilience`; `scenarios/resilience.py` measures accessibility and emergency reach before, during and after. Design and results: [`RESILIENCE.md`](RESILIENCE.md).

**Traffic signals** (`simulation/signals.py`, optional) sit on top of any traffic model: `Simulation.signals`, when set, observes every vehicle and advances each signalised junction's controller (fixed time or adaptive, optional emergency priority) at the start of every step, and the movement code asks it whether a vehicle may go through a junction (`Vehicle.move`'s `may_cross`; in the CA a red light is a wall at the end of the lane). Locations come from OpenStreetMap (`signal_sites.py`, `data/osm/norwich_signals.json`). Scenarios take `signals` in their spec; live runs switch them with `POST /traffic/signals`. Design and results: [`TRAFFIC_SIGNALS.md`](TRAFFIC_SIGNALS.md).

Old data stays correct: runs saved before migration `0004` have `traffic_model = simple`, and Restart uses the run's model. Scenario specs saved before the field existed have none and run the simple model (`ScenarioSpec.effective_traffic_model`); a simple spec keeps its old fingerprint. The API writes the server's default into specs that do not give one, so a study resumed later runs the model it started with. The scenario engine version is 2.

The 30 m minimum length matters because a quarter of Norwich's roads are shorter than 20 m (mostly at junctions); the sensitivity of the results to it is in [the traffic benchmark](traffic_benchmark.md).

### Real road lengths

OpenStreetMap roads curve, so a road's length is longer than the straight line between its two junctions. `Simulation.use_road_lengths(mapping)` gives every vehicle the real lengths (`RoadNetwork.length_between_points`), and `Vehicle.segment_distance` uses them for movement, map position and remaining distance. The traffic manager turns this on, so in the API vehicles drive exactly the distance the router planned (`test_planned_time_matches_the_simulated_journey_on_empty_roads`). A Simulation created on its own still measures straight lines, which keeps the older tests and tools unchanged. The bit of road from a vehicle's position to the next junction after a reroute has no OSM length and is measured as a straight line.

### Travel-time routing (`routing/travel_time.py`)

`RoadNetwork` wraps the graph once: junction positions, the length of every directed road (the shorter one where OSM has two parallel roads), each road's segment key, and lengths keyed by coordinates. `fastest_path(start, destination, speed, speed_factor, blocked_segments)` is A\* where each road costs `length / (speed x speed_factor(segment))`. Its estimate is the straight-line distance at top speed, computed on a flat projection (y = R x latitude, x = R x longitude x the smallest cos(latitude) on the map, then shrunk by 0.1%) so it is always a lower bound yet needs no trigonometry per junction. Speed factors are never above 1, so the estimate never overestimates and the result is exactly the fastest route (checked against a plain Dijkstra with random slowdowns in the tests). Unlike `a_star`, it creates its tables lazily rather than for all 5,058 junctions on every call, and each junction's roads are precomputed as (neighbour, length, segment key). On Norwich it averages 2.0 ms per congested trip (`docs/traffic_benchmark.md`).

- **Travel-time landmarks.** With speed limits, the straight-line estimate must assume the fastest road anywhere (40 mph on Norwich) while most roads are 20 or 30 mph, so A\* explored about twice the junctions. `use_landmarks` therefore also stores, for the same landmarks, the seconds to and from each at the speed limits. A vehicle that keeps to the limits never covers a road faster than that and traffic only slows it, so these are lower bounds in seconds; speed-limited searches use the best 2 for their start (checking all 8 at every junction cost more than it saved). On 1,000 Norwich routes under real simulation traffic they explored 525 junctions against 1,126 and took 2.18 ms against 3.02 ms (measured with Low Power Mode on, so the ratio is what counts), with exactly the same costs as plain Dijkstra; `lower_bound_seconds` gives the traffic manager the same tighter bound for skipping searches that could not save enough.
- **Main connected area.** `main_component()` finds the largest set of junctions that can all reach each other (Kosaraju's algorithm, written iteratively). On Norwich that is 4,143 of 5,058; the rest are one-way pockets and roads cut off at the map's edge. Trips are only generated inside it.
- **Landmarks (ALT).** `use_landmarks(k)` picks k junctions far apart and runs 2k Dijkstra searches once. By the triangle inequality, `d(L, v) - d(L, u)` and `d(u, L) - d(v, L)` are lower bounds on the road distance from u to v. On Norwich this bound averages 89% of the true distance, against 74% for the straight line. Because congestion only slows roads, free-flow bounds stay valid in traffic. Used as the A\* estimate (`heuristic="landmarks"`) it explores fewer junctions but is slower in pure Python, because each estimate checks every landmark; so A\* keeps the straight line by default, and the traffic manager uses the landmark bound once per decision to skip hopeless searches (`docs/traffic_benchmark.md`).

### Performance engineering (Stage 5)

Each change was measured before and after, and each keeps the answers exactly as they were (checked by tests against the code it replaced):

- **Spatial index** (`routing/spatial.py`). A uniform grid over the junctions, searched ring by ring outwards and stopped as soon as no unsearched cell could hold anything closer; far outside the map, or on a map that is one long line, it falls back to checking every junction. It returns exactly the junction the old scan did, ties included. Norwich: 31.7 µs per nearest-junction lookup against 967.7 µs (31x), and the optimiser's 50-nearest list 23x faster. Used by dispatch, the API's nearest-node and nearest-road, and the optimiser's snapping.
- **Distance searches** (`dijkstra.py`, `a_star.py`). Their tables now fill in as junctions are reached instead of over all 5,058 junctions per call, and A\* builds a road's segment key only when there are closures or traffic to check. Same paths, distances and nodes explored; A\* went from 3.26 ms to 1.41 ms per Norwich route and is now faster than Dijkstra (2.00 ms) in wall-clock time, as its 64% fewer explored junctions say it should be.
- **Travel-time landmarks** for speed-limited searches (see Travel-time routing).
- **Reachability cache.** Snapping a candidate needs to know whether a junction can reach 90% of the map; that is a fixed property of the map, now found with a breadth-first search and remembered (`reach_count`) instead of a full Dijkstra every time. The optimiser's profile was 55% `shortest_distances`, mostly these.
- **Batched rerouting** (`TrafficManager._batched_route`). When three or more vehicles due a check in the same second head to the same destination, one backward search from it (`RoadNetwork.fastest_tree_to`) gives every one of them its fastest route. It is exact in the osm model: a vehicle's own road is the only one that costs it differently, and a fastest route from the next junction never uses the road it is on (that would pass the junction twice); vehicles exactly at a junction, and the simple model (where both directions share one count), search on their own. On ordinary Norwich traffic destinations are too spread out for it to matter (98 of about 25,600 plans with 2,000 vehicles, no change in step time); with 2,000 trips into a 250 m zone it cut the time per step from 244.9 ms to 83.9 ms (2.9x) with identical journeys.
- **Profiling.** `python -m backend.perf.profile --save` profiles dynamic traffic, route searches, the optimiser and a scenario with cProfile (`docs/profiling.md`).
- **The API under load.** `python -m backend.perf.api_benchmark --save` starts the backend as deployed and measures request overhead, routing, step latency at 1x and 5x with up to 2,000 vehicles, eight visitors stepping at once, and memory, through HTTP (`docs/api_benchmark.md`). It measures memory before anything else runs, because Python keeps memory it has freed and later runs would reuse it.

### Accidents (`simulation/accidents.py`)

An accident is not a closure: the road stays open. Each severity sets a speed factor (minor 0.7, moderate 0.45, severe 0.2), a capacity factor (0.75, 0.5, 0.34) and a default duration (10, 20, 30 simulated minutes). Capacity works through the effective vehicle count: with half the capacity, 2 vehicles count as 4, so the road reaches "heavy" sooner (a lone vehicle is not affected by capacity, only by the speed factor). `Simulation.add_accident` replaces any accident already on that road, `clear_accident` ends one early, and `step()` clears accidents whose time is up. The congestion level reported for a road includes the lost capacity, so the map colour matches the slowdown.

### Events

`Simulation.events` records `road_closed`, `road_reopened`, `accident` and `accident_cleared` with the simulated time and the road (only real changes: closing a closed road records nothing). The simulation does not act on them; the traffic manager reads new events and decides who should re-plan, so a change has the same effect whether it came from the API, a step or a test.

### Traffic manager and dynamic rerouting (`simulation/traffic_manager.py`)

`TrafficManager(network, simulation, policy)` owns the running simulation together with the road network. `step()` runs surges, moves everyone (`Simulation.step`), reads new events, then runs a rerouting pass.

- **Costs.** A road costs `length / (speed x road_speed_factor(segment, vehicles + 1))`: the speed the vehicle would have after joining it. The vehicle counts are refreshed at the start of every planning batch (`planning_traffic`), because vehicles can change road between steps. Roads that are not free flowing are worked out once per batch (`_slow_roads`); every other road is 1.0, so a search does a dictionary lookup per road instead of re-applying the speed rules. The vehicle's own presence on its current road is not counted twice. Current route and alternative are costed the same way from the same traffic, so the comparison is fair. Closed roads cost infinity.
- **When a vehicle is checked.** Routinely once every `check_interval` seconds, staggered (vehicle i when `(time + i) % interval == 0`), and not within `cooldown_seconds` of its last switch. Urgently, ignoring both, when an event affects it: an accident or closure on its remaining route, or a reopened road or cleared accident that it had changed route to avoid (each vehicle remembers those roads in `avoided_roads`). Anyone else who might now gain finds it at their next routine check. (Two broader rules were tried first and measured on Norwich with 2,000 vehicles: "the road is roughly on the way" and an exact lower-bound test both flagged over 1,200 vehicles for one reopened central road, because in heavy traffic almost any vehicle could in theory gain, and the resulting step took over 5 s.) Urgent vehicles re-plan at the start of the next step, before anyone moves, so they react at the moment of the change. A vehicle re-checked because a road got worse takes any route that saves enough; one re-checked because a road got better only switches to a route that uses that road (other gains wait for its routine check and cooldown, otherwise an out-of-schedule check becomes a back door around the cooldown).
- **Whether it switches.** Only if the new route saves at least `min_saving_seconds` and `min_saving_fraction` of the remaining time. A vehicle part-way along a road finishes it first (the new route starts with its current position and the next junction).
- **Skipping hopeless searches.** If the remaining time minus the landmark lower bound at top speed is already below the threshold, no route could save enough, so no search is run. This removed about 27% of searches on Norwich without changing any decision.
- **Defaults** (check every 10 s, 60 s cooldown, save at least 20 s and 10%) were chosen by comparing policies on Norwich (`benchmark_traffic.py`, section 4). `smoothing_seconds` (plan with traffic averaged over time) is available but off by default, because in those comparisons it made journeys longer.
- **Closures** still go through `rerouting.reroute_affected_vehicles`, but with `route_finder=traffic.route_finder()` so they are planned with travel time too. The default search (shortest route plus 50 m per vehicle) is kept for code that calls it without a route finder.

### Demand, fleets and surges (`simulation/demand.py`)

`DemandModel.random_trip` picks an origin evenly over junctions in the main connected area, and a destination that is drawn towards the city centre with probability `centre_share` (Gaussian weight, scale 1.2 km) and otherwise even. Trips shorter than 800 m in a straight line are not made. A `Zone` (centre and radius) can restrict either end. `TrafficManager.spawn_vehicles(count)` routes each trip with travel-time A\* on the current traffic. A `Surge` adds `vehicles_per_minute` trips into (inbound) or out of (outbound) a zone each simulated second (carrying fractions over) until its duration ends. With no population data this is a plausible, explainable model, not a calibrated one.

### Rerouting

When a road closes, `rerouting.reroute_affected_vehicles` first checks which vehicles' remaining routes use that road. This check is cheap. A\* only runs for those vehicles. A vehicle in the middle of a road finishes that road first, so the new route starts at the next junction it will reach. A vehicle with no alternative keeps its route (and waits at the closure until it reopens). The rerouting benchmark compares this against recalculating every vehicle.

### Emergencies

1. `add_station` creates an `EmergencyStation`, and `add_emergency_vehicle` puts an ambulance in it.
2. `create_emergency` records an emergency at a location, and `dispatch_from_nearest_station` sends an ambulance from the nearest station that has one available. "Nearest" is measured in a straight line on latitude/longitude, not by road. If no ambulance is free the API cancels the emergency and answers 404; there is no queue.
3. The API calculates the ambulance's route with A\*, using current traffic and closures, and gives it to the vehicle.
4. When the ambulance reaches the emergency, the emergency is resolved and the response time is recorded in simulated seconds. The ambulance becomes available again.
5. `dispatch.block_road_ahead` closes a road on the ambulance's route ahead of it and reroutes it using the same rerouting code as any other vehicle. It reports the extra distance and time of the detour.

`dispatch.py` deliberately has no FastAPI code in it, so the scenario can be tested on a tiny map.

That is the original behaviour, which runs with the `simple` traffic model. With the `osm` model the realistic rules apply (below).

### Emergency services (`simulation/services.py`)

Three services, each with its own stations, vehicles, speed and response target: ambulance (20 m/s, 7 min, the NHS England Category 1 mean target), fire engine (17 m/s, 8 min) and police car (22 m/s, 15 min); there is no single national target for fire or police, so those two are illustrative defaults that can be changed per station, vehicle and study. A station holds one service and only that service's vehicles (the API refuses anything else with 400); an emergency names its service and only that service is sent.

`EmergencyRules` say how emergencies are handled; `rules_for(road_model)` gives the original rules with the `simple` model and the realistic ones with `osm`:

| | Original (`simple`) | Realistic (`osm`) |
|---|---|---|
| Who is sent | nearest station in a straight line with a vehicle free | the free vehicle of the service that would arrive first: one fastest-route search per free vehicle under current traffic and closures (`dispatch._send`) |
| Nothing free | refused (404) | queued (202), answered oldest first as soon as a vehicle of that service is free (`dispatch.after_step`, called by `TrafficManager.step`); the response time includes the wait, and the run records `dispatched_at`; an emergency no station of the service can reach at all (with the roads closed now) is refused as `no_route` instead, as the original rules do, and a queued one is refused the same way if closures later cut it off (re-checked only when a road closes or reopens) |
| After the call | back at the station at once | drives back (`going_home`), free to be sent again from on the way, parked when it arrives |
| Traffic | slows it like any car | gives way: it loses half the time other traffic would (`emergency_factor`), and speed limits do not apply |

Planning, movement and the ETA all use the same right-of-way factor, so a dispatched vehicle's estimate matches its drive (tested). Saved runs keep the queue: an emergency is saved when its vehicle is sent, and one still waiting when the run ends is saved as cancelled with reason `still_queued` (migration `0005`, which also adds `dispatched_sim_time`).

### Where emergencies happen (`simulation/population.py`)

By default emergencies are spread evenly over the usable junctions. With `distribution="population"` (scenarios, the optimiser and its evaluation) they follow where people live: the 2021 Census population of each of Norwich's 471 output areas (143,901 people; ONS, Open Government Licence, `data/population/`) is given to the junction nearest the area's population-weighted centroid, and locations are drawn in proportion. Uniform draws are unchanged (the same random calls), so existing results stay the same.

### Station optimiser

For each candidate location, `station_optimiser.py` runs one Dijkstra search to get the distance to every junction. It reuses the same random emergencies for every candidate, so the comparison is fair. Response time is distance divided by ambulance speed. This is what keeps it fast: one search per candidate rather than one per emergency.

- **Candidates.** `dense_candidates` lays a 7x7 grid over the map and moves each point to the nearest well-connected junction (one that can reach at least 90% of the network). Duplicates and points with no well-connected junction nearby are dropped, which leaves about 38 candidates for Norwich.
- **Ranking.** Candidates are ranked by average response time. An emergency a station cannot reach counts as the slowest response seen in that run. Without this rule a station cut off from most of the map averaged only the few emergencies it *could* reach and ranked first (there is a regression test for this).
- **Explanation.** `explain_best` turns the numbers into a few plain sentences (why #1 won, and whether another candidate has a better worst case). The API returns them and the frontend displays them.
- **Service and population.** `service` sets the speed and target to that service's (`/optimiser/stations?service=fire`); `distribution=population` places the emergencies by Census population. Without them the original 20 m/s, 8 minutes and even spread stay, and so do saved answers' reuse keys.
- **Checking it.** `validate.py` checks the estimate against ambulances driven through the full simulation, on empty roads and, since Stage 5, through 1,000 cars with the realistic rules (+2.2% on average, worst +7.1%). (Its ambulance has the road to itself; a car on the same road would count as traffic and slow it down, which is exactly what the simulation is meant to do but not what that check measures.) `evaluate.py` compares the winner with baselines (city centre, middle of the map, random junctions) on the same set of junctions and re-runs the optimiser with several seeds to check the answer is stable. It only reads the optimiser, so it is safe to run at any time.

### Choosing several stations

`optimise_station_set` reuses the same ingredients as the single-station ranking (`prepare_stations` snaps and searches from each candidate once, `sample_emergencies` draws the same random emergencies) and adds:

- **A table of response times.** `response_table` gives `times[candidate][emergency]` in seconds (`math.inf` if unreachable). A set of stations answers each emergency with the fastest member, and `measure_set` turns that into the same measures as a single station: average and worst response over the emergencies reached, percentage within the target out of *all* emergencies, how many were unreached, and the ranking value with unreached emergencies counted at the slowest response seen (the same rule as before, so a set of one station gives exactly the single-station numbers).
- **Selection** (`best_station_set`). Up to 2 million possible sets (`EXHAUSTIVE_SET_LIMIT`) every set is scored, in bulk with numpy (`exhaustive_best_set`, the same score as `set_score`), so the answer is the best there is: all 501,942 sets of 5 from Norwich's 38 candidates take about 1.5 s. This replaced greedy + swaps as the default because, checked against every set on 5 seeds, greedy + swaps missed the best 4- or 5-station set 3 times in 10, by up to 2.2%. Beyond the limit, `greedy_selection` (add the station that lowers the ranking value most) and `refine_by_swapping` (swap each chosen station for each unchosen one while it helps) are run, plus swaps from 12 random starting sets, and the best is kept. `order_by_contribution` finally lists the stations in the order they add value. `OPTIMISER_VERSION` is 2, so answers saved by the old search are not reused.
- **Baselines.** The best single station; the `count` stations that rank best one by one (they tend to bunch together); and the average of 100 random sets. `explain_station_set` reports honestly if a baseline does as well or better.
- **Validation.** `check_settings` rejects scenarios below 1 and a speed or target that is zero, negative, NaN or infinite. (Before, speed 0 crashed with a division by zero and NaN or a non-positive target gave nonsense.)

Simplification: each emergency is treated on its own, so a station never runs out of ambulances. The traffic-aware evaluation below is what checks that.

- **Parallel searches.** The searches from each candidate are independent; with 200 or more distinct candidates they run in 4 processes, sending each result back as a compact array rather than a dict (measured on Norwich: 0.64x at 90 candidates, 1.76x at 416, 1.90x at 800; below about 200 starting the processes costs more than it saves, so the default 38 candidates stay in one process).

### The optimiser tested in traffic (`optimiser/evaluation.py`)

The optimiser's estimate assumes free-flowing roads and a vehicle always free. `POST /optimiser/evaluations` (and `python -m backend.optimiser.run_evaluation`) takes its top answers (the top 5 sites, or the optimised set with the top sites ranked one by one and the best single site) and simulates each as a scenario with the osm model, at every demand level and seed, plus a run with no emergencies for each demand level and seed. For a given demand level and seed every candidate gets exactly the same trips and the same emergencies (same seed, same streams; only the stations differ), so the comparison is fair. The scenarios run as an ordinary study; `station_evaluations` (migration `0006`) records the candidates and which scenario is which.

The report, per candidate and demand level: the simulated mean (95% interval over the seeds, Student's t), 90th percentile and worst response pooled over all runs, the share within target, unreached emergencies, network delay, and the delay the emergency response added to other traffic (paired with the no-emergency run of the same seed). The simulated time is compared with the optimiser's estimate for exactly the emergencies that were simulated (each emergency's junction is recorded), so the gap is traffic, queueing and dispatch, not a different sample of emergencies. Candidates are ranked by simulated mean response, and Kendall's tau says how well the optimiser's own order held up. Measured results for Norwich: `docs/optimiser_evaluation.md`.

### Logging

`main.py` sets up Python's `logging` with `logging.basicConfig`, at the level given by `LOG_LEVEL` (default `INFO`; `settings.log_level` validates it and refuses unknown names). Each module logs under its own name (`backend.api.context`, `backend.api.routes.emergencies`, ...):

- `INFO` at start-up: which graph file was loaded and its size; with a database, that the schema is up to date and how many runs were marked interrupted.
- `INFO` when a run starts, stops or is replaced, and for each fleet.
- `INFO` for each emergency dispatch (`backend.api.routes.emergencies`): type, location, vehicle, route length, junctions on the route and nodes explored.
- `ERROR` (with the traceback) when a run or optimiser result could not be saved.
- `DEBUG`: where the dispatched vehicle started and the full route, point by point.

`basicConfig` does nothing if logging is already set up (pytest's `caplog` relies on this), and uvicorn's own request logs keep uvicorn's format. The command-line tools (benchmarks, the optimiser CLI and `Simulation.run`'s demo) still `print`, because printing a report to the terminal is their job.

### Running in public (`api/protection.py`)

The deployed API is open to anyone, so one middleware, in-process like the runs themselves (no extra service), adds:

- **Rate limits** on requests that cost real CPU time: starting runs, fleets and surges, the optimiser, scenario studies and evaluations. Each visitor has a token bucket of `RATE_LIMIT_PER_MINUTE` (default 30), refilled evenly; over it they get `429` with `Retry-After`. Stepping, status and history are never limited, so the once-a-second polling is unaffected. Behind a proxy (`TRUST_PROXY=true`) the visitor is the first address in `X-Forwarded-For`; the Docker nginx sets that header to the visitor's own address only, so a browser cannot choose it.
- **A 2 MB request limit** (`413`); a 1,000-scenario study is about 0.5 MB.
- **Security headers** (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`) on every answer; nginx and the Render static site send the same for the website, except that the website uses `strict-origin-when-cross-origin`: OpenStreetMap's tile servers refuse map tiles requested with no referrer.
- **One log line per request** (method, path, status, milliseconds, run ID), as text or `LOG_FORMAT=json`. The polling requests are logged at `DEBUG`.

**Health.** `GET /health` answers `200` while the process is up; Render and the Docker health check use it. `GET /ready` also checks the database (`SELECT 1`) and answers `503 degraded` when it cannot be reached. These are separate on purpose: a database outage should not make Render restart an API that can still simulate.

**Starting without the database.** If `DATABASE_URL` is set but the database cannot be reached at start-up, the API logs the error and starts anyway with persistence off (runs show `recording: off`, history endpoints answer 503, `/ready` says why). Restarting the API once the database is back turns saving on again. A database that is reachable but has an out-of-date schema still stops start-up: that is a deployment mistake, not an outage.

## Persistence

Stage 3 added two things: **simulation runs with their own IDs**, so several can run independently, and **PostgreSQL persistence**, so what happened in a run, and every optimiser result, survives a restart. Persistence is optional: with no `DATABASE_URL` the API behaves exactly as it did before Stage 3, and the history endpoints answer 503.

### Runs and their IDs

Before Stage 3 the API kept one `Simulation` and one `TrafficManager` in module-level variables, so there was one run per server process and every request shared it. Now:

- A **`LiveRun`** (`backend/api/sessions.py`) holds one run's `Simulation`, `TrafficManager`, a `threading.Lock`, and its recorder. Its ID is a UUID.
- The **`RunRegistry`** keeps the live runs by ID and knows which one is the **default run**: the one most recently started with `/simulation/start`.
- Every endpoint that acts on a run is registered twice by `run_route` (`routes/common.py`): at its original path (`/simulation/step`, `/incident/block`, ...), where it acts on the default run, and under `/simulations/{simulation_id}/...`, where it acts on the named run. It is the same handler function either way; only the dependency that finds the run differs. So existing clients and tests keep working, and `/simulation/start` still replaces the previous default run, as the single global simulation used to be replaced. `POST /simulations` starts a run that is *not* the default and replaces nothing.
- **Locking.** FastAPI runs these endpoints on a thread pool, so the frontend's polling and a user's action could change the same simulation at once (that was already possible before Stage 3). Every handler holds `run.lock` while it reads or changes the run. Different runs never wait for each other. The shared `RoadNetwork` is read-only, except that it builds its landmark tables the first time a traffic manager is created, by emptying and refilling them, so traffic managers are created one at a time (`AppContext.network_lock`).
- **Memory.** Each live run can hold thousands of vehicles, so at most `MAX_LIVE_RUNS` (default 8) are kept. Starting one more stops the run that has been idle longest, and runs nobody has used for `RUN_IDLE_MINUTES` (default 30) are stopped whenever a run starts.
- **One process.** Live runs exist in one process's memory, so the backend must run as a single worker (`uvicorn ... --workers 1`, as in the Dockerfile and on Render). Saved history is in PostgreSQL and could be read by any number of processes.

The frontend starts its run with `/simulation/start?replace_default=false`, so a visitor never replaces somebody else's run (without it, the original behaviour stays: the previous default run is replaced). It keeps the `simulation_id` that `/simulation/start` returns and sends every request about the running simulation to `/simulations/{id}/...` (`runPath` in `simulationHelpers.ts`), so its requests reach its own run even if someone else starts another. With an older backend that returns no ID it falls back to the original paths.

### What is saved, and when

Each live run has a **recorder** (`backend/api/recorder.py`): a `NullRecorder` that does nothing when there is no database, otherwise a `RunRecorder`. Endpoints call it while they hold the run's lock, and every call is one transaction:

- **Explicit calls** for things only the endpoint knows: a station or vehicle added, an emergency dispatched or refused (and why: no vehicle, or no route), a fleet, a surge, rerouting switched on or off.
- **`sync()`** after anything that changes the simulation, including every step. It reads what the simulation itself recorded since last time: roads closed and reopened and accidents started and cleared (the simulation's `events` list, which already existed for the traffic manager), ambulances that arrived, and every 60 simulated seconds a metrics snapshot. An accident cleared during a step expired; one cleared by `/incident/accident/clear` was cleared by a user; the recorder tells them apart. A road closed by `/emergency/block-ahead` is linked to the emergency it was closed for.

A step in which none of that happened does **no database work at all** (a test counts the SQL statements: zero). Counters that change every step (the run's simulated time and vehicle counts, a running surge's counts) are saved along with those writes, not on their own, so after a crash they can be up to one snapshot interval behind; events, incidents and emergencies are saved as they happen. [The persistence benchmark](persistence_benchmark.md) measured the step time with saving within ±3% of without (noise) at 2, 502 and 2,002 vehicles, with and without a surge. A first version saved surge counts every step, which cost 2.2 ms a step (+13.5% on a small run); that is why they are batched.

**When the database fails.** A new run that cannot be saved is not started (503). If saving fails part-way through a run, the simulation carries on (it is correct, in memory), but its history would now have a gap, so the recorder stops recording that run and reports `"recording": "failed"` rather than presenting an incomplete history as complete. An optimiser answer that cannot be saved is still returned, with `optimiser_run_id: null`.

### Restarts

A live run cannot be resumed after a restart: vehicle positions and routes are not saved (with 2,000 vehicles that would be millions of rows an hour), and replaying the run would need every random choice to be reproducible. What survives is its **history**. So:

- At start-up, `open_database` (`app.py`) marks every run still `active` in the database as `interrupted`: a server that stopped without warning left it there. On a clean shutdown the lifespan handler does the same for the runs that were live.
- Ending a run (interrupted, stopped by `DELETE`, stopped to make room, or replaced by a new `/simulation/start`) ends what was still going on in it: open closures and accidents get `end_reason = run_ended`, and emergencies whose ambulance had not arrived get status `run_ended`.
- An ended run's history stays readable from `/simulations/{id}/history/...`; trying to drive it (`POST /simulations/{id}/step`, ...) answers **409** with its status, not 404.
- An ended run can be **restarted from its setup** (`POST /simulations/{id}/restart`, `routes/simulation.py`): a new run with the same route and rerouting setting, its stations and ambulances, the closures and accidents that were still in place when it ended (those with `end_reason = run_ended`; an accident gets the time it had left, at least 1 s), and its fleets spawned again with their saved seeds, so the same trips. Everything is applied under the new run's lock and recorded like the original requests, then the run is added (by default as the default run). Vehicles start from the beginning; surges and emergencies are not repeated. `simulation_runs.restarted_from` (migration `0003`, `ON DELETE SET NULL`) links the new run to the old one, so deleting the old run keeps the new one.

### The data model

`backend/db/models.py` (SQLAlchemy 2.0). The run and optimiser tables, 13 of the 20, in two groups (the scenario and evaluation tables are under Scenarios).

```mermaid
erDiagram
    simulation_runs ||--o{ stations : has
    simulation_runs ||--o{ emergency_vehicles : has
    stations ||--o{ emergency_vehicles : "parks (same run)"
    simulation_runs ||--o{ emergencies : has
    emergency_vehicles ||--o{ emergencies : "is sent to (same run)"
    simulation_runs ||--o{ incidents : has
    emergencies |o--o{ incidents : "closed for (block-ahead)"
    simulation_runs ||--o{ surges : has
    simulation_runs ||--o{ fleet_spawns : has
    simulation_runs ||--o{ run_events : "timeline of"
    simulation_runs ||--o{ run_metrics : "snapshots of"
    simulation_runs |o--o{ optimiser_runs : "closed roads used by"
    optimiser_runs ||--o{ optimiser_candidates : "ranked (ranking)"
    optimiser_runs ||--o{ optimiser_selected_stations : "chosen (station set)"
    optimiser_runs ||--o{ optimiser_baselines : "compared with (station set)"
```

| Table | One row per | Notes |
|---|---|---|
| `simulation_runs` | run | UUID; `status` (`active`, `stopped`, `replaced`, `interrupted`); start and destination junctions; rerouting setting; last saved simulated time and vehicle counts; created/updated/ended timestamps; `graph_sha256`; `restarted_from` (the run it was restarted from, if any) |
| `stations` | emergency station | `station_index` is the API's `station_id` (unique per run) |
| `emergency_vehicles` | ambulance added to a station | `vehicle_index` is the API's `vehicle_id`; speed |
| `emergencies` | emergency raised | status (`dispatched`, `arrived`, `cancelled`, `run_ended`), cancel reason, the vehicle sent, created and arrival times (simulated), response time, planned route length and search effort, reroutes on the way |
| `incidents` | closure or accident, from start to end | `kind`; the road (two junction ids and their coordinates); accident severity, speed and capacity factors and planned end; when and why it ended (`reopened`, `cleared`, `expired`, `replaced`, `run_ended`); the emergency it was closed for |
| `surges`, `fleet_spawns` | demand added | settings and how many vehicles were spawned or failed |
| `run_events` | thing that happened, in order | `seq` 1, 2, 3 ... within the run; links to the one row it is about (incident, emergency, station, vehicle, surge or fleet) |
| `run_metrics` | snapshot | primary key `(run_id, sim_time)`: vehicles, finished, mean speed factor, congested and closed roads, accidents, total reroutes |
| `optimiser_runs` | optimisation | kind (`ranking` or `station_set`), settings, candidate source, closed roads, `request_key`, explanation (a text array), compute time, the set's own figures |
| `optimiser_candidates`, `optimiser_selected_stations`, `optimiser_baselines` | ranked candidate / chosen station / baseline | the results as rows, keyed by rank, pick order or baseline name |

**Relationships and why.**

- **A run owns its history.** Every history table has `run_id → simulation_runs ON DELETE CASCADE`: deleting a run (`DELETE /simulations/{id}?purge=true`) deletes all of it, and nothing can point at a run that does not exist.
- **"Of the same run" is enforced by the database.** An ambulance's station and an emergency's ambulance must belong to the same run. Ordinary foreign keys cannot say that, so `stations` and `emergency_vehicles` have a unique `(run_id, id)` and the references are composite foreign keys `(run_id, station_id)` and `(run_id, vehicle_id)`.
- **The timeline points, it does not copy.** A `run_events` row holds only its order, time and type and a link to the row it describes; check constraints require the right link for each type (a `road_closed` event must link an incident, an `emergency_arrived` event an emergency, ...). The details live once, in their own table, so the timeline cannot disagree with them. Partial indexes on those link columns keep cascaded deletes from scanning the whole events table.
- **Optimiser results outlive runs.** An optimisation may record whose closed roads it avoided (`simulation_run_id`, `ON DELETE SET NULL`): deleting the run keeps the result and drops the link.
- **Roads are not a table.** The road network is the committed graph file (about 11,000 roads that never change); copying it into the database would duplicate it. A road is stored the way the API already names it, by its two junction ids plus their coordinates. Because junction ids only mean something on the map they came from, every run and optimisation records the SHA-256 of the graph file.

**Rules the database enforces itself**, whatever code writes the data (each has a test that tries to break it): a run has `ended_at` exactly when it is no longer `active`; finished vehicles never exceed vehicles; accident details (severity, factors, planned end) are present for accidents and absent for closures; closures end by `reopened` or `run_ended`, accidents never by `reopened`; nothing ends before it started; an arrived emergency has its arrival time and response time, a cancelled one its reason, and any other has a vehicle; a partial unique index allows one open closure and one open accident per road at a time (a new accident on the same road ends the old one as `replaced`, as the simulation does); speeds, rates and counts are positive; and the optimiser's settings stay in the ranges the API accepts.

**Optimiser results are rows, not JSON.** The API rebuilds the original answer from them (`serializers.optimiser_response`), and a test checks the rebuilt answer is the same JSON text as the original for both kinds, so nothing is lost and whole numbers stay whole.

### Optimiser results and reuse

With a database, `/optimiser/stations` and `/optimiser/station-set` save every answer and add `optimiser_run_id` and `reused` to it. `GET /optimiser/runs` lists saved results and `GET /optimiser/runs/{id}` returns one, with the answer exactly as it was given.

The optimiser is deterministic, so an identical request gets an identical answer. `request_key` is a SHA-256 of everything the answer depends on: the map (its fingerprint), the settings, the candidates (in order, which can break ties), the closed roads (in any order) and `OPTIMISER_VERSION` (`station_optimiser.py`). With `reuse=true` (opt-in, so the default behaviour is unchanged) the newest saved answer with the same key is returned instead of computing again: about 6 ms instead of about 750 ms on Norwich. **Bump `OPTIMISER_VERSION` whenever a change to the optimiser would change its answers**, so old answers are never reused for new code.

### Backups and an expiring database

`scripts/backup_database.sh` writes one `pg_dump` custom-format file (every table, including `alembic_version`) under a temporary name, checks it with `pg_restore --list`, and only then renames it; `scripts/restore_database.sh` restores it in a single transaction with `--exit-on-error`, so a damaged file changes nothing. `backend/tests/db/test_backup.py` backs up a database with a saved run, wipes it, restores it and checks the run's timeline is identical (skipped where no `pg_dump` of the server's version is installed, including CI's runners). `.github/workflows/backup.yml` runs the same script weekly in a `postgres:16` container against the deployed database and uploads the dump encrypted (AES-256, PBKDF2), since public repositories' artifacts are downloadable by any GitHub user. Because the hosted free database expires, the API also treats an unreachable database as an outage to survive rather than a reason not to start (see [Running in public](#running-in-public-apiprotectionpy)).

### Migrations

The schema is created and changed only by Alembic (`alembic.ini`, `backend/db/migrations/`). Revisions: `0001` (runs, history, optimiser results), `0002` (scenario studies), `0003` (`simulation_runs.restarted_from`), `0004` (`simulation_runs.traffic_model`), `0005` (the emergency queue: `emergencies.dispatched_sim_time`, the `still_queued` reason, `scenario_results.emergency_detail`), `0006` (`station_evaluations`). Revision `0001` was generated by autogenerate from the models and then reviewed by hand: the downgrade also drops the PostgreSQL enum types (autogenerate leaves them, so a second upgrade would fail), redundant indexes were removed, and the partial indexes on `run_events` were added. The database address always comes from `DATABASE_URL`.

- The API checks at start-up that the database is at the latest revision and refuses to start otherwise, with the command to run (`alembic upgrade head`).
- The tests run upgrade, downgrade to empty and upgrade again, and check that the models and migrations agree (what `alembic check` does; CI also runs `alembic check`).
- To change the schema: edit `models.py`, run `alembic revision --autogenerate -m "..."` against a migrated database, **read and fix** the generated file (enum types, data migrations, index choices), then run the database tests.

## Scenarios

Stage 4 added a **scenario engine** (`backend/scenarios/`): full simulations of different situations on the real network, run in the background, stored, ranked and explained. Everything is measured from complete simulations with the same `Simulation` and `TrafficManager` the live API uses; nothing is estimated from distance and speed.

### What a scenario is (`spec.py`)

A `ScenarioSpec` is everything needed to run a scenario again, exactly: a name and a **seed**; **demand** (how many background trips, and whether their departures are spread over a period); **surges** (trips into or out of a zone, each with a start time); **closures** and **accidents** (a road by its two junction ids, a start time, and an end time or duration); whether **dynamic rerouting** is on; emergency **stations** and **emergencies**; and a time limit. It is validated with pydantic (unknown fields, impossible values and out-of-range limits are refused) and against the map (`validate_against_graph`: the junctions exist and are adjacent). Limits keep one scenario bounded: at most 2,000 background trips and 7,200 simulated seconds.

### Running one (`runner.py`)

`run_scenario(world, spec)` builds a simulation and a traffic manager, then steps one simulated second at a time, applying the scenario's events at their times (closures through the same rerouting code as `/incident/block`, accidents as `/incident/accident`, emergencies through `dispatch.dispatch_emergency`, which `/emergency/create` also uses), until every trip has arrived, every surge has ended and every emergency is answered, or the time limit. The `World` holds what is built once per process from the map (the road network with its landmark tables, the demand model, the junction lookup, and a cache of free-flow trip times) and is reused by every scenario that process runs.

**Reproducible.** Every random choice comes from a stream seeded from the spec: trips, each surge, the emergencies and the traffic manager each have their own (`Random(sha256("<seed>:<name>"))`), so adding emergencies does not change the trips. Trips are drawn first and routed when they leave; a trip a closure has cut off keeps its place (as "unroutable"). So two scenarios with the same **trip signature** (seed, demand and surges) have the same trips in the same order and can be compared trip by trip. A test runs the same spec twice and compares the results exactly; another checks that two worker processes give the same results as running in-process.

### What is measured (`impact.py`)

| Measure | Definition |
|---|---|
| **Delay of a trip** | Time spent on the road minus `distance_travelled / speed`. A vehicle moves at speed × speed factor, so this is exactly the time lost to traffic, accidents and waiting at closures. It excludes the unused rest of the second in which the vehicle arrives (`Vehicle.last_move_seconds`), so a vehicle alone on the network has a delay of exactly 0 (tested). |
| **Network delay** | The sum over all trips, in vehicle-hours (trips still on their way at the time limit count what they lost so far). |
| **Journey-time increase** | Journey time minus the free-flow time of the trip's fastest route (no traffic, no closures). Unlike delay it counts detours: a closure that sends a trip round has a cost even if the detour is free-flowing. Reported as mean, 95th percentile and worst, over completed trips. |
| **Delay on a road** | Each second, the delay each vehicle picks up is added to the road it was on at the start of that second (the same snapshot the simulation moves vehicles by). The top 25 roads are kept. Summed over all roads it equals the network delay exactly (tested). |
| **Areas** | Road delay grouped into fixed 500 m grid cells by road midpoint; the top 10 cells are kept. |
| **Time series** | Every simulated minute: vehicles still driving and finished, delay in that minute, mean speed factor, closed roads, accidents. |
| **Emergencies** | Dispatched, refused, arrived; mean and worst response time; share of all emergencies reached within the target. |
| **Execution** | Wall and CPU time, peak memory of the process, vehicle-seconds simulated per second. |

Two caveats, both deliberate. A vehicle that crosses into another road during a second has that whole second attributed to the first road. A vehicle rerouted part-way along a road drives a short "made-up" road to the next junction (`rerouting.find_restart_point`); delay there is counted under that road, which has no junction id (the interface labels it "part of a road (after a reroute)"). The network totals are exact either way. Measuring costs about 2% of a run ([benchmark](scenario_benchmark.md)).

**Delay and journey-time increase can disagree, and that is information.** With rerouting on, vehicles avoid a crashed road, so slowdown delay can fall while journeys lengthen through detours; and closing a busy road can move traffic onto parallel roads and *reduce* total travel (a Braess-like effect: 77 of 399 random closure sets in the [large study](scenario_benchmark.md#5-a-large-study)). The explanations call this out.

### Comparing and ranking (`compare.py`)

`METRICS` defines each ranking metric with a label, unit, direction (lower or higher is better) and a one-line definition. `rank()` orders scenarios by one metric, best first, with the gap to the best, ties broken by total travel time then name; scenarios without a value (an emergency metric for a scenario without emergencies) come last. `paired()` compares two scenarios with the same trips trip by trip: trips slower, faster or unchanged (by more than 5 s), extra vehicle-hours, the worst increase, and trips newly cut off or unfinished. Scenarios with different trips are never paired, only compared on totals. `road_changes()` finds the roads whose delay changed most (exact for roads in both top-25 lists, a lower bound otherwise). `explain()` writes why the leader leads: by how much, how the two setups differ (`spec_differences`: closures, accidents, surges, demand, rerouting, stations, seed), the trip-by-trip evidence, the roads with the most extra delay, and the Braess-like note when a scenario that closes more roads loses less time.

### Studies and background jobs (`jobs.py`, `sweep.py`)

A **study** is a batch of scenarios compared with each other, optionally against a baseline. A study can be thousands of simulations of seconds to a minute and a half each, so the API never runs one inside a request: `POST /scenarios/studies` validates and stores the study and answers **202** at once.

The `JobManager` runs it. Simulation is CPU-bound Python, so it uses a pool of **worker processes** (threads would share one core because of the GIL): `SCENARIO_WORKERS`, default `min(4, CPUs)`, started with `spawn`, each loading the map and building its `World` once. A dispatcher thread hands the pool only as many scenarios as there are workers, so a scenario marked `running` in the database really is running, and cancelling a study just stops handing out its remaining scenarios (any already running finish, and their results are dropped). Workers only simulate and return plain results; the API process writes each result in one transaction, so workers never touch the database. A scenario that raises is stored as `failed` with its error, and the study carries on. `SCENARIO_WORKERS=0` runs scenarios one at a time on a thread in the API process (small machines, tests). Studies left queued or running when the server stopped are marked `interrupted` at the next start, like live runs, and then **resumed** (`RESUME_STUDIES`, on by default): `ScenarioRepository.resume_study` puts the study and its unfinished scenarios back to `queued` (completed and failed scenarios keep their results) and the JobManager queues them again. A scenario's result depends only on its spec, so a resumed study gives the same results as one that was never interrupted (tested). `POST /scenarios/studies/{id}/resume` does the same by hand for an interrupted or cancelled study. On shutdown the JobManager stops handing out work at once, so scenarios still queued stay `queued` for the next start.

Every write that touches both a study and one of its scenarios locks the **study first**. (Saving a result used to lock the scenario then the study while cancelling locked them the other way round, which deadlocked; a test that cancels and restarts during a study found it.)

`sweep.py` generates large studies from one scenario: the same scenario with many **seeds** (how much results vary by chance), **each road closed** in turn after a baseline, **random closures** of N roads drawn from the roads in a zone (Monte Carlo), or alternative **station layouts**.

### Stored results (migration `0002`)

```mermaid
erDiagram
    scenario_studies ||--o{ scenarios : contains
    scenarios ||--o| scenario_results : "has (one)"
    scenarios ||--o{ scenario_road_impacts : "top 25 roads"
    scenarios ||--o{ scenario_area_impacts : "top 10 areas"
    scenarios ||--o{ scenario_timeseries : "per minute"
```

| Table | One row per | Notes |
|---|---|---|
| `scenario_studies` | study | status (`queued`, `running`, `completed`, `cancelled`, `interrupted`), ranking metric, scenario / completed / failed counts (the database checks they add up and that a study has 1 to 5,000 scenarios), workers, map fingerprint, engine version, timings |
| `scenarios` | scenario in a study | its position; at most one baseline per study (partial unique index); the validated spec as JSONB, with its fingerprint and trip signature as columns; status and, exactly when failed, the error |
| `scenario_results` | completed scenario | every summary, emergency and execution figure as a typed column; per-trip data as parallel ordered arrays (trip ends, journey and free-flow time, delay, status code). The database checks every array has one entry per trip and that trip and emergency counts add up. The arrays are deferred columns, loaded only for a full scenario, never for listing or ranking a large study |
| `scenario_road_impacts`, `scenario_area_impacts` | top road / area | keyed by rank |
| `scenario_timeseries` | simulated minute | keyed by `(scenario_id, sim_time)` |

The spec is stored as JSON because it is an input document (validated before it is stored, identified by its fingerprint); everything measured is in typed columns and child tables. Deleting a study deletes everything in it. `ScenarioRepository.result_from_rows` rebuilds a stored result into exactly the dict `run_scenario` returns, so comparisons of stored results equal comparisons of fresh ones (tested).

### Performance

Measured on the Norwich map ([full report](scenario_benchmark.md)): one scenario takes 5.7 s with 200 vehicles, 16.2 s with 500 and 104.6 s with 2,000 (bigger fleets congest more, so there is more to simulate); a worker peaks at 78 to 99 MB; saving a result takes about 17 ms. Two workers are 1.97 times as fast as one; four 2.61 times and eight 3.52 times, because on this 4 performance + 4 efficiency core machine each scenario slows down as more run at once. A 400-scenario random-closure study ran through the job manager and database in 21.5 minutes on 4 workers (about 1,100 scenarios an hour), with no failures.

## Containers

`docker compose up --build` runs everything (`docker-compose.yml`):

```
db (postgres:16, volume pgdata, health check)
  -> migrate (backend image, `alembic upgrade head`, exits)
       -> backend (uvicorn, one worker, health check; port 8000 on 127.0.0.1 only)
            -> frontend (nginx on 8080: the built site, and /api/... forwarded to the backend)
```

Each service starts only when the one before it is healthy (or, for `migrate`, finished successfully). The backend image (`backend/Dockerfile`, built from the repository root) installs `requirements-deploy.txt` into a virtual environment in a first stage, then copies only the code, `alembic.ini` and the Norwich map, and runs as a non-root user; `.dockerignore` keeps tests, docs, the OpenStreetMap cache, `.venv` and `.git` out. The frontend image builds the site with `VITE_API_URL=/api`, so the browser only talks to nginx: no CORS set-up and no backend address baked into the build. nginx finds the backend through Docker's DNS each time (`resolver 127.0.0.11` with the address in a variable), because a fixed host name is looked up only once at start-up and would send every request to a restarted backend's old address (502).

Scenario studies run in worker processes inside the backend container (`SCENARIO_WORKERS`, 2 by default in Compose; each worker holds its own copy of the map, 80 to 100 MB).

`scripts/compose_smoke_test.py` builds and starts the stack under its own project name and volume, checks it end to end through nginx (a run on the Norwich map, its saved history, an optimiser result, a two-scenario study run by the worker processes), restarts the backend container, checks the run is `interrupted` with its history intact and the optimiser result and study unchanged, then removes everything. CI runs it on every push.

## API endpoints

Defined in `backend/api/routes/`. FastAPI generates interactive docs at `/docs` (and `/redoc`): an overview, then every operation grouped by tag (Service, Routing, Simulation runs, Traffic, Incidents, Emergency services, Optimiser, Scenarios, History), each with a description; `backend/tests/test_api_docs.py` keeps it that way.

**Which run.** The endpoints that act on a simulation exist twice (see [Runs and their IDs](#runs-and-their-ids)): at the path in the table below, acting on the **default run** (404 `No simulation is running` if there is none), and under `/simulations/{simulation_id}`, acting on that run (404 if there never was such a run, 409 if it has ended). The second column gives the per-run path; the parameters and answers are the same.

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | `{"name": "Norwich Urban Traffic Simulator", "status": "online"}` |
| GET | `/health` | Liveness: `{"status": "ok"}` while the process answers (Render's and Docker's health check) |
| GET | `/ready` | Readiness: the database `ok` or `not configured` (200), or 503 `degraded` when it cannot be reached, with the number of live runs |
| GET | `/nearest-node?latitude&longitude` | Nearest junction to a point |
| GET | `/route?start&destination&simulation_id` | Shortest A\* route between two junction ids (plus 50 m per vehicle on a road). Uses the traffic and closures of the run named by `simulation_id`, or of the default run, if any |
| POST | `/simulation/start?start&destination&traffic_model&replace_default` | Start a simulation with a vehicle from start to destination, plus one coming back if a return route exists. Also creates the traffic manager (real road lengths, dynamic rerouting). It becomes the default run (the previous default stops, recorded as `replaced`); with `replace_default=false`, as the website sends, it replaces nothing. The answer includes its `simulation_id`; 503 if a database is configured but the run could not be saved |
| GET | `/simulation/state` | Position and progress of vehicle 0 |
| POST | `/simulation/step?seconds` | Run surges, advance `seconds` (1 to 60, default 1) simulated seconds, react to events and re-plan after each. Returns all vehicles (position, `speed_factor` where 1.0 is free flowing, `journey_seconds`, `reroutes`), `simulation_time`, `rerouted_this_step` and a `summary` for the live charts (vehicles driving and held up, average speed, arrivals, mean journey, reroutes, closed roads, accidents) |
| GET | `/simulation/congestion` | Vehicle count and congestion level per road segment (the level includes any accident's lost capacity) |
| POST | `/simulation/reroute?vehicle_id` | Recalculate one vehicle's route (travel time, traffic, accidents, closures) |
| POST | `/simulation/fleet?count&seed` | Add `count` (1 to 2,000) vehicles with demand-model trips and fastest routes. Returns `spawned`, `failed`, `seconds`, `total_vehicles` |
| POST | `/incident/block?start&destination` | Close the road between two adjacent junctions and reroute affected vehicles (travel time) |
| POST | `/incident/unblock?start&destination` | Reopen a closed road; `vehicles_to_recheck` says how many vehicles will re-plan on the next step |
| POST | `/incident/accident?start&destination&severity&duration_seconds` | Start an accident (`minor`, `moderate` (default) or `severe`; duration 0 to 86,400 s, default by severity). The road stays open but slow. Returns the accident and `vehicles_to_recheck` |
| POST | `/incident/accident/clear?start&destination` | Clear an accident early (404 if there is none on that road) |
| GET | `/incidents` | `incidents`: closed roads; `accidents`: active accidents with severity, factors and time left |
| POST | `/traffic/surge?latitude&longitude&radius_metres&direction&vehicles_per_minute&duration_seconds` | Start a surge into (`inbound`) or out of (`outbound`) a zone (radius 100 to 5,000 m, up to 600 vehicles a minute, up to 7,200 s). 400 if the zone has no usable junction |
| GET | `/traffic/status` | Vehicles (all and still driving), surges, accidents, closed roads, rerouting policy and counters (searches, skipped searches, reroutes, search time) |
| POST | `/traffic/rerouting?enabled` | Turn dynamic rerouting on or off (closures still reroute) |
| POST | `/station/create?latitude&longitude&station_type` | Add an emergency station |
| POST | `/station/add-vehicle?station_id&speed&vehicle_type` | Add an emergency vehicle to a station (`speed` must be above 0, otherwise 400) |
| POST | `/emergency/create?latitude&longitude&emergency_type` | Create an emergency and dispatch the nearest available vehicle |
| GET | `/emergency/status` | State, ETA, response time and reroutes for every emergency |
| POST | `/emergency/block-ahead` | Close a road ahead of the ambulance and reroute it |
| POST | `/command/start?vehicles_per_station&background_vehicles&seed` | Start a command centre run (`osm` model, not the default run): units at OpenStreetMap's fire and police stations and the optimiser's ambulance standby points, plus ordinary traffic. Returns its `simulation_id` and state ([command centre](COMMAND_CENTRE.md)) |
| POST | `/command/fleet?vehicles_per_station` | Give an existing run a command centre and its fleet (409 if it has one) |
| GET | `/command` | Incidents with units and timelines, units (state, incident, ETA, route), stations and response figures (409 without a command centre) |
| POST | `/command/incidents?kind&latitude&longitude` | Report an incident (`medical`, `fire`, `crime`, `road_accident`, `flood`, `road_closure`): its road effect starts and its units are sent (or queued). 422 for an unknown kind or no road there |
| POST | `/command/incidents/{incident_id}/resolve` | Stand units at the scene down and end the road effect (409 while a unit is on its way) |
| GET | `/command/replay` | Every unit's recorded positions (one sample a simulated second while out) and every incident's timeline |
| GET | `/optimiser/stations?candidates&scenarios&seed&target_seconds&speed&simulation_id&reuse` | Rank candidate station locations. `candidates` is optional (2 to 20 points written as `lat,lon;lat,lon`); leave it out to use the default spread of about 38 locations. Returns the ranked results and a plain-English explanation of the winner. 400 for bad candidates, `scenarios` outside 1 to 5000, or a `speed` or `target_seconds` that is not above 0 |
| GET | `/optimiser/station-set?stations&candidates&scenarios&seed&target_seconds&speed&simulation_id&reuse` | Choose `stations` (1 to 10, default 3) stations that work well together. Same options as above. Returns the chosen stations in pick order, the set's average, worst case, coverage within the target and unreachable count, three baselines, and a plain-English explanation. 400 if more stations are asked for than there are distinct candidate junctions |

Both optimiser endpoints avoid the closed roads of the run named by `simulation_id` (or of the default run), and add two fields to their answer: `optimiser_run_id` (the saved result; `null` without a database or if saving failed) and `reused`. `reuse=true` returns the newest saved answer to an identical request instead of computing (see [Optimiser results and reuse](#optimiser-results-and-reuse)).

**Per-run paths** (under `/simulations/{simulation_id}`):

| Original path (default run) | Per-run path |
|---|---|
| `POST /simulation/step`, `GET /simulation/state`, `GET /simulation/congestion` | `POST .../step`, `GET .../state`, `GET .../congestion` |
| `POST /simulation/reroute`, `POST /simulation/fleet` | `POST .../reroute`, `POST .../fleet` |
| `POST /incident/block`, `POST /incident/unblock`, `GET /incidents` | `POST .../incidents/block`, `POST .../incidents/unblock`, `GET .../incidents` |
| `POST /incident/accident`, `POST /incident/accident/clear` | `POST .../incidents/accident`, `POST .../incidents/accident/clear` |
| `POST /station/create`, `POST /station/add-vehicle` | `POST .../stations`, `POST .../stations/add-vehicle` |
| `POST /emergency/create`, `GET /emergency/status`, `POST /emergency/block-ahead` | `POST .../emergencies`, `GET .../emergencies/status`, `POST .../emergencies/block-ahead` |
| `POST /traffic/surge`, `GET /traffic/status`, `POST /traffic/rerouting` | the same paths after `.../` |
| `POST /command/fleet`, `GET /command`, `POST /command/incidents`, `POST /command/incidents/{id}/resolve`, `GET /command/replay` | the same paths after `.../` |

**Runs** (Stage 3):

| Method | Path | Purpose |
|---|---|---|
| POST | `/simulations?start&destination` | Start an independent run (201). It is not the default run and replaces nothing |
| GET | `/simulations?status&limit&offset` | Runs, newest first. With a database: every saved run (filter by `active`, `stopped`, `replaced`, `interrupted`; `total` for paging), each marked `live` or not. Without: the live runs |
| GET | `/simulations/{id}` | One run: status, `live`, `is_default`, `recording` (`on`, `off`, `failed`), times, vehicles, closed roads, ... Works for ended runs with a database |
| DELETE | `/simulations/{id}?purge` | Stop a live run (history kept, status `stopped`); `purge=true` also deletes it and its history. 409 if it has already ended and `purge` is not set |
| POST | `/simulations/{id}/restart?make_default` | Start a new run from an ended run's saved setup (201; see [Restarts](#restarts)). Answers like `/simulation/start` plus `restarted_from`, `applied` (stations, ambulances, closures, accidents, fleet vehicles) and `route`. 409 if the run is still live, 404 if unknown, 503 without a database |

**History** (read from the database; live or ended runs; 503 without a database):

| Method | Path | Purpose |
|---|---|---|
| GET | `/simulations/{id}/history` | The run's summary, counts of each kind of history, mean response time |
| GET | `/simulations/{id}/history/incidents?kind` | Every closure and accident: road, junctions, start, end and why it ended, accident details, the emergency a closure was made for |
| GET | `/simulations/{id}/history/emergencies` | Every emergency: status, vehicle and station, times, response time, planned route, reroutes, why it was refused |
| GET | `/simulations/{id}/history/stations` | Stations and their vehicles |
| GET | `/simulations/{id}/history/demand` | Fleets and surges (with their spawned/failed counts) |
| GET | `/simulations/{id}/history/events?after_seq&limit` | The timeline in order; page (or follow a live run) with `after_seq` |
| GET | `/simulations/{id}/history/metrics?from_time&to_time` | Snapshots every 60 simulated seconds |

**Saved optimiser results** (503 without a database):

| Method | Path | Purpose |
|---|---|---|
| POST | `/optimiser/runs?kind&stations&...` | Run the optimiser (`ranking` or `station_set`, same options as the GET endpoints) and save it (201) |
| GET | `/optimiser/runs?kind&simulation_id&limit&offset` | Saved results, newest first (settings and timing) |
| GET | `/optimiser/runs/{id}` | One saved result: settings plus `answer`, exactly as the optimiser endpoint gave it |
| DELETE | `/optimiser/runs/{id}` | Delete a saved result |

The optimiser endpoints also take `service` (ambulance, fire, police: that service's speed and target unless given) and `distribution` (`uniform` or `population`).

**The optimiser tested in traffic** (a database and the scenario workers needed):

| Method | Path | Purpose |
|---|---|---|
| POST | `/optimiser/evaluations` | JSON settings (kind, stations, top, service, distribution, demand levels, seeds, emergencies per run, ...): runs the optimiser, then simulates its top answers as a background study (202 with `evaluation_id`) |
| GET | `/optimiser/evaluations` | Evaluations, newest first |
| GET | `/optimiser/evaluations/{id}` | Progress, then the report (see The optimiser tested in traffic) |

Runs: `/simulation/start` and `POST /simulations` take `traffic_model` (`simple`, `osm` or the experimental `ca`; default the server's `TRAFFIC_MODEL`). In a `ca` run, each step's summary adds `stopped` (cars standing still) and each `/simulation/congestion` road adds `stopped`, `occupancy` and `speed_factor` from the automaton. Stations: `station_type` is ambulance, fire or police (400 otherwise); `add-vehicle` defaults to the station's service and its speed. Emergencies: `emergency_type` names the service; in an osm run an emergency with nothing free answers 202 `emergency queued` with its place in the queue, and `/emergency/status` gives each one's `service` and `waited_seconds`.

**Scenario studies** (Stage 4; everything that stores or reads results needs a database, 503 without one):

| Method | Path | Purpose |
|---|---|---|
| POST | `/scenarios/studies` | JSON `{name, scenarios: [ScenarioSpec, ...], baseline, metric}`. Validates every scenario (400 listing the problems by position), makes repeated names unique, stores the study and answers **202** with its id; it runs in the background. At most `MAX_STUDY_SCENARIOS` (default 1,000) scenarios |
| POST | `/scenarios/studies/sweep` | A study generated from one scenario (`base`): `kind` = `seeds` (`count`, `first_seed`), `each_closure` (`roads`), `random_closures` (`count`, `closures_per_scenario`, optional `zone`, `sweep_seed`) or `station_layouts` (`layouts`). 202 |
| GET | `/scenarios/studies?limit&offset` | Studies, newest first |
| GET | `/scenarios/studies/{id}?metric` | Progress, each scenario's status and headline metrics, and once results are in, the ranking (by the study's metric or `metric`) and the explanation of it |
| POST | `/scenarios/studies/{id}/cancel` | Stop handing out the study's remaining scenarios (409 if it has finished) |
| POST | `/scenarios/studies/{id}/resume` | Run an interrupted or cancelled study's unfinished scenarios again (`requeued`: how many); 409 for any other status |
| DELETE | `/scenarios/studies/{id}` | Cancel and delete a study with all its results |
| GET | `/scenarios/{id}` | One scenario: its spec, status, full result (summary, emergencies, top roads and areas, time series, execution, trip counts), its closures and accidents with coordinates, and, if its study has a completed baseline, the difference from it trip by trip and road by road |
| GET | `/scenarios/compare?ids&metric&baseline` | Compare 2 to 20 completed scenarios from any studies: ranking, explanation, and each one against `baseline` |
| GET | `/scenarios/metrics` | Each ranking metric's label, unit, direction and definition (no database needed) |
| GET | `/scenarios/presets` | Starting points for the builder (no database needed) |
| GET | `/scenarios/nearest-road?latitude&longitude` | The road (two adjacent junctions) nearest a point, for picking closures and accidents on the map (no database needed) |

## Frontend

The frontend is mostly one React component file, `frontend/src/App.tsx`, using `react-leaflet` for the map. The station optimiser panel and its map markers live in `frontend/src/OptimiserPanel.tsx`; the result tables are in `OptimiserResults.tsx` and the requests and types in `optimiser.ts`. Logic that does not need React (status messages, traffic summary, route conversion) lives in `simulationHelpers.ts`. It calls the backend at the address in `config.ts` (the `VITE_API_URL` build setting, defaulting to `http://127.0.0.1:8000` for local development), converts chosen map locations (latitude/longitude) into junction ids with `/nearest-node`, and runs the simulation by stepping the backend and re-drawing vehicles and congestion. It also polls `/emergency/status`. Once `/simulation/start` has returned a `simulation_id`, all of those requests go to `/simulations/{id}/...` (`runUrl` in `App.tsx`, `runPath` in `simulationHelpers.ts`); with a backend that gives no ID they use the original paths. The optimiser panel does not pass the run's ID, so it plans around the default run's closed roads, which is the user's own run when one person uses the app. By default the backend's CORS setting only allows the origin `http://localhost:5173`; `CORS_ORIGINS` changes that (see `backend/api/settings.py`). In Docker the frontend is built with `VITE_API_URL=/api` and nginx forwards those requests, so CORS is not involved.

The route panel has a **traffic model** choice (realistic, the default, simple, or the experimental cellular automaton) sent with `/simulation/start`, and each scenario in the study builder can choose one too (otherwise the server's default); a cellular-automaton run adds a "Stopped in queues" live chart and the queue on an inspected road; the emergency panel a **service** choice (each emergency creates a station of that service, at the next station index, and one of its vehicles; a 202 shows the queue message). The optimiser panel can plan for any service and for emergencies where people live. **Test the optimiser in traffic** (`EvaluationSection.tsx`, logic in `services.ts`) starts an evaluation, follows it every 3 s, and shows the ranking in traffic, the estimate for the same emergencies, the gap, the per-demand means with 95% intervals, the delay added to other traffic, Kendall's tau and the explanation; it asks nothing until its button is pressed.

The **Scenarios** section (`ScenarioWorkspace.tsx`, logic in `scenarios.ts`) sits in the main column under the optimiser results. The builder edits scenarios (vehicles, seed, rerouting, a city-centre surge) and picks closures and accidents on the map: "Close a road" makes the next map click call `/scenarios/nearest-road` instead of setting a route point (`App.tsx` holds the pending pick, and the map toolbar says what the next click will do). Running posts a study and checks it every 2 s (`STUDY_POLL_MS`) until it finishes; "Large study" posts a random-closure sweep. Results show progress, the explanation, a bar chart of the ranked metric, an SVG chart of delay per simulated minute, a spread of network delay for large studies, and the ranking table; a scenario's name opens its details, and its most delayed roads (amber) and closures (dashed) are drawn on the map. The page makes no request until the section is used: the metric list is kept in `scenarios.ts`, and a backend test checks each key exists in `compare.METRICS` with the same direction.

The **History** section (`HistoryPanel.tsx`, logic in `history.ts`) sits under it and loads only when asked: the 20 most recent saved runs and studies. **Details** shows a run's saved history in figures; **Restart** calls `/simulations/{id}/restart` and hands the answer to `watchRestartedRun` in `App.tsx`, which does what Calculate Route does after its search (clear the old overlays, draw the route, name the new run's ID in every request, start stepping). **Open** shows a study in the Scenarios results (an `openStudy` prop on `ScenarioWorkspace`), and **Resume** calls `/scenarios/studies/{id}/resume` and then opens it.

## Interface design

The screen is one workspace: a fixed navy header, a controls column on the left, and on the right a five-metric strip, the Norwich map (the largest element), and an analysis area for optimiser results. On narrow screens it becomes a single column in the order metrics, map, controls, results.

- **Design system.** `index.css` defines colour, type-scale, control-height, radius and elevation tokens as CSS variables (navy and slate for structure, a muted teal for positive/optimal results, amber and red only for warnings and emergencies) and the components built from them (panels, buttons, selects, the metrics strip, badges, notices, tables, bar charts). Hierarchy comes from surface contrast and spacing rather than outlines: the controls column is one surface split by hairlines, the metrics are one strip, and borders are kept for places that need them (secondary buttons, table rules). Buttons come in four variants (primary, secondary, destructive, quiet) with one shared disabled style. There are no UI or icon libraries: icons are inline SVG (`components.tsx`) and the charts are plain HTML bars.
- **Map.** The OpenStreetMap tiles are desaturated with a CSS filter so routes, congestion and markers stand out. Map colours, marker sizes and line weights live in `mapStyles.ts`: point markers share a white keyline, congestion is drawn as a wide translucent band under the thinner route line, and closures are the widest line. The toolbar shows what the next map click will do, the legend lists congestion plus only the layers currently on the map, and the latest action's message floats over the map instead of moving the layout.
- **Meaning of colour.** Route = blue, reroute (diversion) = violet so it cannot be confused with amber congestion, ambulance and emergency = red, closed road = near-black, best/optimal = green, chosen station set = navy. Congestion uses green, amber and red.
- **Accessibility.** Every group of controls is a named region, the header navigation and a skip link work with the keyboard, the simulation state is announced as a status, charts are written out as text beside each bar, the metric cards have a plain-sentence version for screen readers, and animations are switched off for people who ask for reduced motion.
- **What did not change.** All logic, requests and button labels are as before; the redesign only changed markup and styling (plus a loading spinner and disabled state on the button that was just clicked).

## Testing

**Backend.** Tests live in `backend/tests/` and use tiny hand-built graphs from `helpers.py`, so they run in a few seconds without the Norwich data. Files by area: `test_routing`, `test_road_closures`, `test_rerouting`, `test_emergency`, `test_emergency_response`, `test_edge_cases`, `test_station_optimiser`, `test_multi_station`, `test_congestion_movement`, `test_benchmark_congestion`, `test_settings` (`CORS_ORIGINS` and `LOG_LEVEL` parsing), `test_deployment_files` (`render.yaml` and the deploy requirements; the `render.yaml` checks need `pyyaml` and are skipped without it), `test_travel_time` (fastest-route A\* against a reference Dijkstra with random slowdowns, landmark bounds, main connected area), `test_accidents` (speed and capacity, expiry, ETA, real road lengths), `test_traffic_manager` (event-driven and routine rerouting, thresholds, cooldown, damping against an undamped policy, planned vs driven time, fleets, surges, demand) `test_api` (including every Stage 2 endpoint), `test_sessions` (the run registry; runs kept apart through the API; per-run endpoints answering exactly like the originals; the live-run limit; 40 overlapping step requests all applied once) and `test_docker_files` (Compose start-up order and database URLs, one worker, non-root images, nginx and the `/api` prefix, what `.dockerignore` keeps, and the CI workflow's database and Docker jobs). `helpers.make_grid_graph` builds a seeded grid map for the larger scenarios.

`test_api.py` drives the real FastAPI app through `TestClient`. `main.py` reads the `NORWICH_SIM_GRAPH` environment variable (default: the Norwich pickle), and the test file points it at a small 4x4 grid map with a one-way dead end and an unconnected junction, so the endpoints are tested without the Norwich data. The file is skipped if `fastapi` or `httpx` is not installed. A fixture empties the run registry before every test; `api.simulation` and `api.traffic` still read the default run's simulation and traffic manager (a module `__getattr__` in `main.py`), so the tests that inspect them did not change. `test_sessions.py` and the database tests build apps with `create_app` directly, so each test gets a fresh app, and a second app on the same database is what a restart looks like.

**Database tests.** `backend/tests/db/` (125 tests) runs against a real PostgreSQL named by `TEST_DATABASE_URL`, for example `postgresql://norwich:norwich@localhost:5433/norwich_sim_test`. SQLite is deliberately not used as a stand-in: the schema relies on PostgreSQL enums, arrays, UUIDs, partial unique indexes and check constraints. The database is wiped and migrated at the start, so its name must contain `test`. Without `TEST_DATABASE_URL` the tests are skipped (124 of them; one needs no database), unless `REQUIRE_DATABASE_TESTS=1` (set in CI), which turns that into a failure. Each repository test runs inside a transaction that is rolled back; tests through the API commit for real and empty the tables afterwards.

- `test_migrations`: every table and enum type created; upgrade, downgrade to empty (types included) and upgrade again; the models and migrations agree.
- `test_run_repository`, `test_optimiser_repository`: what the repositories write, every database rule refusing bad data, cascades and `SET NULL`, data surviving a brand new engine, optimiser answers rebuilt to identical JSON, and request keys.
- `test_api_persistence`, `test_optimiser_api`: through HTTP, a run's whole history (closures, accidents that expired or were cleared, emergencies from dispatch to arrival, refused emergencies, the block-ahead link, fleets, surges, metrics, the timeline), zero SQL on quiet steps, replace/stop/purge, a restart (live runs become `interrupted` with history intact; 409 when driven), a clean shutdown, refusing an un-migrated database, the database failing at start and mid-run, and saved optimiser results (identical round trip, reuse only for identical requests, links to runs, restart, delete).

**Scenario engine tests.** `test_scenarios.py` (30, on a seeded 8 x 8 grid): the same spec gives the same result; separate random streams; trips keep their order under closures; a lone vehicle loses exactly 0 s; road delay sums exactly to trip delay; the time series accounts for every vehicle-second; cutting the grid in two lengthens journeys; an accident after departure slows its road; unroutable trips, surges, spread departures, rerouting off, the time limit, emergencies, more stations answering faster, and spec validation. `test_scenario_compare.py` (16): ranking, ties, missing values, trip-by-trip pairing, road changes and explanations. `test_scenario_sweeps_and_api.py` (10): sweeps, the endpoints that work without a database, and the frontend's metric list. In `backend/tests/db/`: `test_scenario_repository.py` (12: exact round trip, lifecycle, cancel, interrupt, cascade, database rules, stored vs fresh comparison) and `test_scenario_api.py` (17: a study end to end, details against the baseline, another ranking metric, comparison across studies, a failing scenario, validation, limits, sweeps, cancel, delete, restart, and two real worker processes giving the same results as in-process).

**Frontend logic tests.** `frontend/tests/` holds tests run by `npm run test:unit`. They use Node's built-in test runner: `tsconfig.test.json` compiles the tests and the few source files they import (`simulationHelpers.ts`, `optimiser.ts`, `OptimiserResults.tsx`) into `.test-build/`, then `node --test` runs them. Components are rendered to HTML with `react-dom/server`; `fetch` is replaced by a fake. Source files that tests import must write their relative imports with the extension (`./optimiser.ts`).

**Frontend interaction tests.** `frontend/ui-tests/` holds tests run by `npm run test:ui` (Vitest, configured in `vitest.config.ts`, with jsdom and React Testing Library). They render the whole `App` and click through it. Three things are replaced: `fetch` (`helpers.ts` provides a fake backend where each test says what each endpoint answers, and records every request), `react-leaflet` (`fakeLeaflet.tsx` turns map objects into plain elements with `data-*` attributes, and `mapState.ts` lets a test "click the map"), and the app's one-second polling timer (`controlPolling` lets a test deliver exactly one simulation update at a time; other timers, such as Testing Library's own, run normally). `setup.ts` unmounts the app and undoes every fake after each test. The marker sizes the tests look for (`POINT`, `VEHICLE`, `EMERGENCY_SPOT`, ...) are read from `mapStyles.ts`, so a design change to sizes does not break them. `npm run test:ui` type-checks the tests with `tsconfig.ui-tests.json` first, because Vitest does not check types. Files: `simulation.test.tsx` (route, simulation, start over, failures, road closure), `emergency.test.tsx` (ambulance dispatch, progress, close road ahead, failures), `optimiser.test.tsx` (ranking, station set, loading, clearing, failures), `layout.test.tsx` (page structure and landmarks, navigation, empty states, route steps, metric cards, the busy state of Calculate Route), `scenarios.test.tsx` (nothing fetched on load, picking a road on the map without starting a route, adding and removing scenarios, running a study: the request body, progress, ranking, explanation and charts, polling stopping, details and the map overlay, cancel, the random-closure sweep, refused and no-database studies; the fake backend records request bodies), `runIds.test.tsx` (with a backend that returns a `simulation_id`, every run request goes to `/simulations/{id}/...`: `withRunIds` in `helpers.ts` removes the original paths from the fake backend, so a request to one fails the test; the other files' fake backend returns no ID, so they cover the fallback). Real Leaflet and real browser layout are covered by the end-to-end tests instead.

**End-to-end tests.** `frontend/e2e/` (Playwright, configured in `playwright.config.ts`) starts the real backend (uvicorn, port 8002, rate limit off, scenario workers off) and a production build of the website (`vite preview`, port 5174), then drives Chromium through them on the real Norwich map: `simulation.spec.ts` (route, live controls, incidents picked on the map, closures, the three emergency services, two visitors at once, an unreachable destination, a 2,000-vehicle rush hour) and `analysis.spec.ts` (optimiser, a scenario study, history, the no-database message, health and API docs). Tests run one at a time because they share one backend. Map clicks are fractions of the map's size at a fixed 1440 x 1000 window, so they land in the same places every run. `screenshots.spec.ts` is not a test: with `SCREENSHOTS=1` it takes the README's screenshots.

`npm test` runs both kinds (`test:unit`, then `test:ui`).

GitHub Actions (`.github/workflows/tests.yml`) has five jobs:

- `test` and `test (Python 3.12)`: Python 3.11 and 3.12, each with a `postgres:16` service container. They run `alembic upgrade head` and `alembic check`, then `python -m pytest backend/tests -v -rs` with `REQUIRE_DATABASE_TESTS=1`, so every database test must run.
- `frontend (lint, build and test)`: `npm run lint`, `npm run build`, `npm run test:unit` and `npm run test:ui` as separate steps.
- `end-to-end (Playwright)`: installs the backend from `requirements-deploy.txt` (as deployed), migrates a `postgres:16` service container, installs Chromium, then `npm run test:e2e`; on failure it keeps the Playwright report with traces and screenshots.
- `docker (build and run with Compose)`: `docker compose config`, then `scripts/compose_smoke_test.py` (see [Containers](#containers)).

A separate workflow, `backup.yml`, backs up the deployed database weekly (see [Backups and an expiring database](#backups-and-an-expiring-database)).

## Known design trade-offs

- **Live runs are in one process's memory.** Runs have IDs and are kept apart, but the backend must run as a single worker, and a live run cannot survive a restart: only its history does (it comes back `interrupted`). Making runs resumable would mean saving or replaying every vehicle, which the [Restarts](#restarts) section explains was not worth it. Saved history is in PostgreSQL and is not tied to a process.
- **Counters can lag after a crash.** A run's simulated time, vehicle counts and a running surge's counts are saved with other writes (at least every 60 simulated seconds), not every step, because a write costs about 2 ms and most steps change nothing else. Events, incidents and emergencies are saved as they happen.
- **The default run is shared.** The original endpoints (and the optimiser panel, which does not pass an ID) act on the run most recently started with `/simulation/start`. The frontend uses its run's ID for everything else, so two visitors no longer step each other's vehicles, but a second visitor pressing Calculate Route still replaces the first visitor's run as the default (recorded as `replaced`), and the first visitor's next request answers 409 (404 without a database, which has no record of the old run).
- **Scenario studies are not resumed after a restart.** Like live runs, they are marked `interrupted`; completed scenarios keep their results, the rest would have to be run again in a new study.
- **Parallel efficiency depends on the machine.** On this 4 performance + 4 efficiency core Mac, 2 workers are 1.97 times as fast as one but 8 only 3.52 times, because each scenario slows as more run at once ([benchmark](scenario_benchmark.md)). The job manager keeps every worker busy and is not the limit; more performance cores should scale further, which was not measured.
- **Road and area impacts keep only the top 25 roads and 10 areas per scenario**, so road-by-road comparisons are exact for roads in both lists and a lower bound otherwise. The network totals and per-trip figures are complete.
- **Free-flow reference.** Journey-time increase is measured against the fastest route with no traffic and no closures, so in a closure scenario it includes the unavoidable detour as well as congestion; the trip-by-trip comparison with a baseline separates the closure's own effect.
- **The interface shows the study you ran.** Past studies are listed and compared through the API (`/scenarios/studies`, `/scenarios/compare`) but have no screen yet.
- **No history in the interface yet.** Saved runs and optimiser results are available through the API (`/simulations`, `/simulations/{id}/history/...`, `/optimiser/runs`) but the frontend has no screen for them.
- The pickle format is quick to load but couples the data file to the `Graph` class and is not safe to load from untrusted sources.
- `frontend/src/App.tsx` holds all the UI logic in one file. Splitting it into components and a small API client would be a natural clean-up.
- The frontend has no pause or reset button: a simulation starts when a route is calculated and a new map click starts over.
- Two routers exist side by side: `a_star` (shortest distance plus 50 m per vehicle), used by `/route`, emergency dispatch, the closure rerouting default and the older benchmarks; and `RoadNetwork.fastest_path` (travel time), used by everything the traffic manager does. Moving the remaining callers to travel time would change their tested results, so it was left for a later stage.
- `Simulation` stays independent of the road graph (its tests build it from a list of points); everything that needs the graph lives in `TrafficManager`. The API creates both together.
- Vehicles are never removed from the list, because the API uses list positions as vehicle ids. Rerouting and stepping skip finished vehicles, but the `/simulation/step` response grows with every vehicle ever added.
- A vehicle rerouted part-way along a road gets a route that starts at its current position (`rerouting.find_restart_point`, pinned by `test_rerouting.py`). Until it reaches the next junction its road segment is keyed by that point, so for those seconds it is not counted in the real road's traffic and an accident on the real road does not slow it. Fixing this means keeping the real road's identity on the vehicle (or changing that test's expectation), left for a later stage.
- Urgent re-plans are done all in one step, so an event affecting a few hundred vehicles makes that step slow (about 1 to 1.6 s with 2,000 vehicles on Norwich). Spreading them over a few steps would trade that for a slower reaction.
- The speed rule is a step function of the vehicle count, so costs jump when one vehicle joins a road. It is kept from Stage 1 (existing tests pin its values); a smooth, length-aware function would make re-planning steadier.
- **Deployment (Render).** `render.yaml` describes a free Python web service (the backend, started with `uvicorn ... --port $PORT`) and a static site (the frontend, built with `npm run build`). The frontend learns the backend's address from `VITE_API_URL` at build time (`frontend/src/config.ts`), and the backend allows only the origins listed in `CORS_ORIGINS` (`backend/api/settings.py`). The Norwich graph is committed to Git so the backend can start without downloading anything. `render.yaml` also creates a free PostgreSQL database and sets the backend's `DATABASE_URL` from it (`fromDatabase`, so no password is in Git); the start command runs `alembic upgrade head` before `uvicorn` (free services have no separate pre-deploy step), and `SCENARIO_WORKERS=0` runs studies inside the API on the 512 MB service. Render's free database expires after 30 days; see the README.
