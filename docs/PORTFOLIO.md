# Presenting the Norwich Urban Traffic Simulator

Material for a demo video, a CV and LinkedIn. Every number here comes from a report in this repository (linked); re-check them if the code changes.

## Demo video (about 3 minutes)

Record at 1440 x 1000 or larger, with the backend and website running locally (or the deployed site, once it is awake). Before recording, run one route so the map tiles are cached. Talk over it or add captions; the bold line is roughly what to say.

| Time | On screen | Say |
|---|---|---|
| 0:00 | The website, empty map of Norwich | **"This is the Norwich Urban Traffic Simulator: a traffic simulation of Norwich's real road network, 5,000 junctions from OpenStreetMap, with a Python backend and a React map."** |
| 0:10 | Click two points across the city, *Calculate Route* | **"A\* finds the fastest route using each road's speed limit; the simulation starts straight away."** |
| 0:25 | *Live controls*: 2,000 vehicles, *Start a rush hour*, speed 5x | **"Now 2,000 cars and a rush hour into the centre. Roads turn amber and red as they fill up, and the charts below track how many cars are held up."** |
| 0:50 | *Add an accident*, click a busy road; scroll to *Live traffic* | **"A severe accident cuts the road's capacity. Cars whose routes used it re-plan around it; without rerouting, in the benchmark only 1 of 137 cars got through, with it all 137 did."** |
| 1:15 | *Service: Fire engine*, *Trigger Fire Emergency*; *Close Road Ahead* | **"Emergencies dispatch the vehicle that would arrive first by road. Closing the road ahead makes it reroute live, and its response time includes the detour."** |
| 1:40 | *Station optimiser*: 4 stations, *Find best set* | **"Where should stations go? The optimiser scores every possible set of stations with numpy, up to two million of them, and picks the best."** |
| 2:00 | *Scenarios*: the random-closure study results (prepared earlier) | **"Scenario studies simulate hundreds of situations in background worker processes, rank them and explain the ranking, trip by trip."** |
| 2:25 | GitHub: README, the Actions tab with five green jobs, `/docs` | **"It's tested at every level: over 800 backend tests including a real PostgreSQL, interface tests, and Playwright driving a real browser against the real backend, all in CI, and it deploys with Docker or to Render."** |
| 2:45 | Back to the map | **"Code, benchmarks and architecture notes are on GitHub."** |

Tips: zoom the browser to 110% for readability; hide the bookmarks bar; keep the mouse still while talking; trim waiting time (adding the 2,000-vehicle fleet takes 2 to 3 seconds).

## CV bullets

Pick three or four; each is backed by a report in `docs/`.

- Built a traffic simulation and emergency-response planning platform on Norwich's real road network (5,058 junctions, OpenStreetMap) with Python/FastAPI, PostgreSQL and a React/TypeScript/Leaflet front end.
- Implemented Dijkstra and A\* from scratch with travel-time and landmark (ALT) heuristics; profiling-led optimisation made A\* 1.46x faster than Dijkstra with identical routes, and simulations scale to 10,000 vehicles with dynamic rerouting.
- Designed an emergency station optimiser that searches up to 2 million station sets exhaustively with numpy; the best 3-station set reaches 99.6% of emergencies within 4 minutes against 84.6% for random sets, and its answers are validated in full traffic simulations.
- Built a scenario engine that runs hundreds of full simulations as background jobs in a worker-process pool (400 in 21.5 minutes on 4 workers), with deterministic, trip-by-trip comparisons and plain-English explanations.
- Engineered for production: run isolation per visitor, rate limiting, health and readiness checks, graceful degradation when the database is unreachable, encrypted automated backups, Alembic migrations, Docker Compose, and a 5-job CI pipeline including Playwright end-to-end tests against the real backend.

## LinkedIn project entry

**Norwich Urban Traffic Simulator: traffic, disruption and emergency response**

A traffic simulation of Norwich built on its real road network from OpenStreetMap. Thousands of vehicles drive real roads at their speed limits, congestion builds as roads fill, and vehicles reroute around accidents and closures. Ambulances, fire engines and police cars are dispatched by road, and an optimiser decides where their stations should go, then tests that answer in full traffic simulations. A scenario engine runs hundreds of what-if simulations in the background and explains which did best and why.

Stack: Python, FastAPI, PostgreSQL (SQLAlchemy, Alembic), numpy, React, TypeScript, Leaflet, Docker, GitHub Actions, Playwright.

Highlights: Dijkstra and A\* written from scratch; up to 10,000 simulated vehicles; exhaustive search over up to 2 million station sets; 400 full simulations in 21.5 minutes; over 1,000 automated tests, from unit tests to a real browser.

## Talking points for interviews

- **Why the frontend sends one update at a time.** End-to-end tests on the real map showed that with 2,000 vehicles a step can take longer than the one-second polling interval; overlapping requests queued behind the run's lock and the page froze. The fix is backpressure: skip a tick while one is in flight.
- **Why live runs stay in one process.** A run is thousands of in-memory vehicle objects; sharing them across processes would mean serialising them every step. Runs are isolated by ID and lock, the database keeps their history, and [the API benchmark](api_benchmark.md) shows what one process can serve.
- **Why the database is optional and failure-tolerant.** The free hosted database expires; the API starts without saving rather than not at all, `/ready` reports it, and backups make moving databases routine.
- **Testing a stochastic system.** Scenarios are seeded, so the same scenario gives the same result and two scenarios with the same seed have the same trips, which makes trip-by-trip comparisons and exact regression tests possible.
