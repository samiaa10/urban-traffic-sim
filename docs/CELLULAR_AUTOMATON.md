# Cellular-automaton traffic (experimental)

An experimental third traffic model, `ca`, that moves cars with the Nagel-Schreckenberg (NaSch) cellular automaton on Norwich's real road network. It sits next to the two existing models, `simple` and `osm`, which are unchanged; any run or scenario can choose it.

Code: [`backend/simulation/cellular.py`](../backend/simulation/cellular.py). Tests: [`backend/tests/test_cellular.py`](../backend/tests/test_cellular.py). Experiments: [`backend/simulation/benchmark_cellular.py`](../backend/simulation/benchmark_cellular.py), results in [`cellular_benchmark.md`](cellular_benchmark.md).

## How the existing models move cars

The existing engine (`simulation/simulation.py`, `vehicle.py`) is **macroscopic per road**. A vehicle is a point that moves continuously along its route, and its speed on a road is the road's speed limit times a speed factor. That factor depends only on how many vehicles are on the road (`road_model.py`):
- `simple`: fixed steps of 1, 0.6 and 0.3.
- `osm`: Greenshields' straight line, falling from 1 when the road is empty to 0.05 when it is full, where full is lanes × length / 7.5 m.

Vehicles on the same road do not interact individually. There is no car ahead, no gap, no braking, and no queue in the physical sense, just a slower road.

## The NaSch model

Each lane of each directed road is a row of cells. A car occupies `vehicle_cells` consecutive cells and has a whole-number speed `v` in cells per time step. Every time step, every car applies four rules. It's a parallel update: every car decides from where everyone was at the start of the step.

1. **Acceleration:** `v = min(v + 1, vmax)`.
2. **Braking:** `v = min(v, gap)`, where `gap` is the empty cells before the car ahead.
3. **Randomisation:** with probability `p`, `v = max(v - 1, 0)`.
4. **Movement:** the car moves `v` cells.

Rule 2 makes collisions impossible: no car can move further than the empty space in front of it. Rule 3 is "imperfect driving". Without it, light traffic flows perfectly forever. With it, jams appear on their own and travel backwards as stop-and-go waves.

### Parameters and units

| Parameter | Default | Meaning |
|---|---|---|
| `cell_metres` | 2.5 m | Length of a cell. Speeds come in steps of one cell per time step (2.5 m/s, 9 km/h). Acceleration is one cell per step per step (2.5 m/s²). |
| `vehicle_cells` | 3 | A car's length in cells: 7.5 m, a car plus the gap in a standing queue (the osm model's jam spacing). |
| `brake_probability` | 0.2 | Chance per car per step of slowing by one cell (rule 3). |
| `time_step` | 1 s | Length of one update. 0.5 or 0.25 s run 2 or 4 updates per simulated second. 1 / `time_step` must be a whole number, because the rest of the simulation steps once a second. |
| `vmax` | per road | The road's speed limit in cells per time step, rounded to the nearest cell. At the defaults: 20 mph is 4 cells (10 m/s), 30 mph is 5 (12.5 m/s), 40 mph is 7 (17.5 m/s), 60 mph is 11 (27.5 m/s). |
| Vehicle density | input | On the ring road, cars per cell. On Norwich, set by the number of trips (the demand model), as in the other models. |

**Why not the classic cells?** The classic NaSch setup is 7.5 m cells, one cell per car and vmax 5. It's what the textbook results are stated for, and the ring-road experiments use it. On the network it is too coarse: its speed steps are 7.5 m/s (27 km/h), so a 20 mph street and a 30 mph street would both round to 2 cells (15 m/s), and acceleration would be 7.5 m/s², which no car can do. The finer 2.5 m grid with 3-cell cars is a standard refinement of the model, and keeps the same 7.5 m jam spacing.

## How it fits the road network (hybrid)

The road graph, junctions and route searches are unchanged. The CA only replaces how ordinary cars move along a road.

| Part | What happens |
|---|---|
| **Road graph and routing** | Unchanged. A* and the travel-time search plan every route. Rerouting, closures, accidents, surges, dispatch and scenario studies all work as before. |
| **Roads** | One lattice per directed road the cars use, created when the first car enters it. Its length is the real OSM road length, split into `max(1, round(length / cell_metres))` cells. Direction comes from the graph: a one-way street has one lattice, a two-way street two. |
| **Lanes** | OpenStreetMap's lane count gives that many parallel lanes (one per direction where lanes aren't tagged, as in osm). A car takes the lane with the most free space when it enters a road and keeps it. |
| **Speed limits** | Each road's vmax comes from its OSM speed limit (or the UK default for its road type), as in osm. |
| **Junction transitions** | The first car in a lane looks past the end of its road into the next road of its route, and on into the one after if that is empty. Two cars entering the same lane in the same step are placed one at a time in a seeded random order, each into the space actually left, so they cannot collide. A car that has just crossed still takes up room on the road it came from until its rear has crossed. |
| **Road ends** | A car arrives when its front reaches the last cell of the last road of its route. The fraction of the second it used is recorded, as the other models do. |
| **Closures** | A car on a closed road does not move, as in the other models. A closed road next on its route is a wall at the end of its own road. |
| **Accidents** | The road's vmax is multiplied by the accident's speed factor (at least one cell per step), and only lanes × capacity factor of its lanes (at least one) take new cars. |
| **Rerouting mid-road** | The router gives a vehicle in the middle of a road the route "here, next junction, …". The car keeps its place on the lattice of the road it was driving, which ends at that junction, rather than jumping. It is also counted on that road for traffic and congestion (`CaRoads.traffic_density`). The other models count it on the made-up stretch, as before. |
| **Cars that cannot enter** | A new trip whose first road has no room yet waits at its start; its journey clock runs. |
| **Emergency vehicles** | Not on the lattice. They move as in osm, with right of way, at the speed the CA measures on each road. Ordinary cars do not pull over for them. |
| **Feedback to routing** | After each second, the mean speed of the cars on each road, as a fraction of the road's vmax, is smoothed over about 3 s and becomes the road's speed factor (`CaRoads.speed_factor`). Routing, arrival-time estimates, rerouting decisions, dispatch and the congestion map all read it, so they see the traffic the CA produced. A road with no measurement yet uses osm's estimate. |

`Simulation.step` hands ordinary cars to the CA only when the model is cellular (`road_model.cellular`, which is false for `simple` and `osm`). So the existing models take exactly the same code path as before. The full backend test suite, unchanged, still passes.

### Repeatability

Everything random comes from one generator, seeded from the scenario's seed through its own named stream ("ca"), the way the runner already seeds trips and emergencies. Cars are always processed in the same order, and every car draws its random number every step, so one car's state never shifts another's. The same scenario gives the same result, and the tests check this.

## Selecting it

- **Dashboard:** the route panel's "Traffic model" dropdown has "Cellular automaton (experimental)". Each scenario in the study builder has a "Traffic model" choice; with none chosen, the server's default applies, as before.
- **API:** `traffic_model=ca` on `/simulation/start` and `POST /simulations`, and `"traffic_model": "ca"` in a scenario spec.
- **Server default:** `TRAFFIC_MODEL=ca`. The default stays `osm`.
- **Saved runs:** migration `0007` lets them record `ca`.

### What the dashboard shows for a CA run

Everything shown comes from the automaton's state:
- **Congestion layer** (2D and 3D): colours each road by the speed the CA measured on it, not by a formula.
- **Live charts:** "Average speed" and "Held up" use each car's CA speed. A "Stopped in queues" chart appears, showing the share of cars at speed 0.
- **3D Inspect mode:** an inspected road shows its queue (cars stopped) and how full its cells are.
- **Comparing models:** run the same scenario twice in the study builder, once with each model, and use Compare. It already notes that the two used different traffic models.

## Results

All runs use fixed seeds (1, 2 and 3) and repeat exactly. The full tables and figures are in [`cellular_benchmark.md`](cellular_benchmark.md), from `python -m backend.simulation.benchmark_cellular --save` (Apple M1, Python 3.9.6, Low Power Mode off, about 12 minutes).

### Does it behave like NaSch should? (controlled tests)

| Test | Expected (published NaSch behaviour) | Measured |
|---|---|---|
| Ring road without randomness (p = 0) | Flow = min(density × vmax, 1 − density): free flow at vmax up to density 1/6, then the jammed branch | 2,880 cars/h at both 21 and 27 cars/km (densities 0.16 and 0.20), which is exactly the formula. 0% stopped below density 0.6. |
| Randomness lowers capacity | Maximum flow about 0.32 cars per step for p = 0.5 | 1,865 cars/h (p = 0.25) and 1,200 cars/h, or 0.33 per step (p = 0.5) |
| A lone car's speed | vmax − p | 4.75 cells per step at p = 0.25 (unit test) |
| Spontaneous jams | None without randomness; with it, jams form on their own and travel backwards | At density 0.2: 0% stopped with p = 0; 25% (p = 0.25) and 43% (p = 0.5) with it. Jams move backwards at 18.9 and 10.8 km/h. Observed jams on real roads travel back at roughly 15 to 20 km/h. |
| Queue behind a blockage | Grows at the arrival rate, clears after reopening | Grew 13.7 cars/min against 15 arriving; 70 cars at its longest; cleared in 170 s (157 to 178) |
| Queue discharge | Real traffic leaves a queue at 10 to 20% below the road's best flow (the "capacity drop") | 1,940 cars/h out of the queue, against a maximum flow of 1,865 at the same p. **No capacity drop**: plain NaSch is known not to produce one without a "slow-to-start" rule. |
| No collisions | No two cars ever overlap | Checked every second in the tests: ring roads, lines of 25 queued cars, and grids of 150 cars with junctions, closures and seeds |
| Repeatable | The same seed gives the same result | Identical journey times for the same Norwich scenario run twice; different seeds differ |

### On Norwich, against the existing model

The same trips, from the same seeds, in both models, using the scenario engine:

| Trips | Model | Mean delay (s) | 95th percentile delay (s) | Mean speed (of free flow) | Route changes | Runtime per simulated second |
|---|---|---|---|---|---|---|
| 500 | osm | 5.5 | 16.8 | 99% | 0 | 6.5 ms |
| 500 | ca | 18.7 | 30.6 | 95% | 12 | 6.5 ms |
| 1,000 | osm | 11.4 | 31.0 | 97% | 12 | 13.8 ms |
| 1,000 | ca | 20.3 | 35.4 | 94% | 44 | 14.2 ms |
| 2,000 | osm | 29.0 | 88.1 | 92% | 494 | 35.3 ms |
| 2,000 | ca | 31.4 | 72.5 | 91% | 465 | 30.9 ms |

All trips arrived in every run.

- **Light traffic:** the CA adds about 13 s per trip that `osm` doesn't. That is random braking (each car loses about p cells per step) plus accelerating from a standstill at the start of each trip. It is the cost of "imperfect driving", not congestion.
- **Heavy traffic (2,000 trips):** the two agree on mean delay (31 s against 29 s). The CA has a shorter worst tail (95th percentile 73 s against 88 s), because its queues keep moving instead of every car on a full road being slowed to the same crawl.
- **Speed against density** on Norwich's roads of at least 30 m:
  - **Light:** the CA is a little slower when roads are nearly empty (93% against 99%).
  - **Middle:** the two agree between 20% and 40% full.
  - **Heavy:** the CA slows much more sharply above half full (41% against 58% at 50 to 60% full, and 13% against 28% at 80 to 90%).

  `osm` assumes its curve. The CA's curve comes from cars braking for the car in front.
- **Closing the three busiest roads** (from the `osm` baseline) added little delay in either model at 1,000 trips (1.6 s and 0.5 s), because rerouting finds ways round. With three seeds, the difference between the models is within run-to-run noise.
- **Runtime:** the same as `osm`, within a few percent, and slightly faster at 2,000 trips, where `osm` reroutes more.

### Is it more realistic?

**The evidence doesn't show that.** It shows the CA reproduces the textbook NaSch behaviour, including jams that form on their own and travel backwards at a realistic speed. It also gives queues a physical length and order, which `osm` lacks. But nothing here is compared with measured Norwich traffic: no loop-detector counts, speeds or journey times. Without that, neither model can be called more accurate. The CA's extra light-traffic delay depends on p, which hasn't been calibrated.

## Limitations

1. **Not calibrated.** The braking probability p = 0.2 and the 2.5 m cells are choices, not values fitted to Norwich data. Results, especially light-traffic delay, depend on p.
2. **No lane changing or overtaking.** Multi-lane roads are parallel lanes; a car keeps the lane it entered.
3. **Simple junctions.** There are no signals, give-way rules, turning slowdowns or right-of-way at junctions. Conflicts are settled in a seeded random order. Real junctions are where most urban delay comes from.
4. **No capacity drop.** Plain NaSch discharges queues at full capacity. A "slow-to-start" variant (VDR) would add it.
5. **Speed rounding.** Speeds are whole cells per second (2.5 m/s steps), so limits are rounded. For example, 30 mph runs at 12.5 m/s, 7% under, and 20 mph at 10 m/s, 12% over.
6. **Emergency vehicles are not on the lattice.** They use the measured road speeds with right of way, but cars don't pull over and they don't take up cells.
7. **Very short roads.** A quarter of Norwich's roads are under 20 m, which is 1 to 8 cells. A car can be longer than the road, and briefly hang over the junction behind it.
8. **One CA per run, in Python.** The model is about as fast as `osm` up to 2,000 cars, but it hasn't been profiled beyond that.
9. **Network closures:** the closure experiment shows only small effects at this demand. A stronger test would close a bridge or ring-road section at rush hour.

## Ready to be a selectable mode?

**Yes, as an experimental option, and that's how it ships.**
- **Safe for existing behaviour:** it is stable, with no collisions, every trip arriving in every test, and repeatable results. It's tested at rule, road, network, API and dashboard level. It leaves `simple` and `osm` untouched, and every existing test passes.
- **Not the default:** `osm` stays the default until the CA has been calibrated against real Norwich data and has proper junction rules.
- **Next steps:** add slow-to-start, add junction priority, then calibrate p.
