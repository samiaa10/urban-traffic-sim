# Dynamic traffic benchmark

Run on 2026-11-17 with Python 3.9.6 (macOS-26.3.1-arm64-arm-64bit), on mains power with Low Power Mode off, the Mac kept awake. Seed 42. Main (before Stage 5, commit `1ce655c`) and the Stage 5 branch were run one after the other in the same session. Timings depend on the machine; the counts (reroutes, arrivals, journeys) do not.

Reproduce:

```bash
python -m backend.simulation.benchmark_traffic --sizes 100 500 1000 2000 --steps 300 \
  --accident-vehicles 1000 --accident-steps 1200 --policy-seeds 1 --traffic-model osm   # or simple
python -m backend.simulation.benchmark_traffic --sizes 5000 10000 --steps 120 --traffic-model osm \
  --accident-vehicles 200 --accident-steps 60 --policy-vehicles 100 --policy-steps 60 --policy-seeds 1
python -m backend.simulation.benchmark_traffic --sizes 2000 --steps 300 --accident-vehicles 1000 \
  --accident-steps 600 --policy-vehicles 300 --policy-steps 600 --policy-seeds 1 \
  --traffic-model osm --min-road-length 15   # 30, 50
```

Norwich map: 5,058 junctions, 10,881 directed roads, 4,143 junctions in the main connected area (trips are made there).

Two traffic models (`backend/simulation/road_model.py`): **simple**, the original rules (1 vehicle free, 2 at 60%, 3+ at 30%, both directions counted together, every road at 50 km/h), and **osm**, OpenStreetMap speed limits, lane capacity and per-direction traffic (the default since Stage 5).

## 1. Route search (300 random trips, a random quarter of roads congested)

The same search code serves both models; this compares main with the branch.

| Search | Main: mean (median) | Stage 5: mean (median) | Junctions explored |
|---|---|---|---|
| A\* shortest distance (`a_star.py`, no traffic) | 2.46 ms (2.03) | **0.96 ms (0.69)** | 509 |
| A\* fastest time, straight-line estimate | 1.24 ms (0.93) | 1.24 ms (0.92) | 820 |
| A\* fastest time, distance landmarks | 2.58 ms (1.91) | 2.54 ms (1.86) | 561 |

`a_star.py` no longer fills a table over all 5,058 junctions on every call, and builds segment keys only when there are closures or traffic. The routing benchmark (`python -m backend.routing.benchmark`, 100 pairs) gives the same picture: A\* went from 0.83x Dijkstra's speed on main (3.25 vs 2.72 ms) to **1.46x** (1.42 vs 2.07 ms), with the same 759 and 2,139 junctions explored.

Speed-limited searches (osm model) use travel-time landmark bounds: see [the road network check](#5-the-osm-model-on-norwich).

## 2. Scaling with fleet size (300 simulated seconds after spawning)

All vehicles set off together, so these are the busiest seconds of a run. Movement only: rerouting off. Dynamic: the default rerouting policy. Per simulated second: mean wall-clock time of one `TrafficManager.step()` (worst second in brackets). Memory: Python allocations (tracemalloc) for spawning the fleet and 60 s of dynamic traffic, in a separate run; the road network is shared and not counted.

| Vehicles | Model | Spawn | Movement only, per simulated s | Dynamic, per simulated s | Route searches per s | Reroutes | Still driving at end | Memory held (peak) |
|---|---|---|---|---|---|---|---|---|
| 100 | main | 0.08 s | 0.5 ms | 3.2 ms (10) | 2.7 | 44 | 45 | 1.4 MB (1.6) |
| 100 | simple | 0.09 s | 0.6 ms | 4.6 ms (15) | 2.7 | 44 | 45 | 1.4 MB (1.6) |
| 100 | osm | 0.08 s | 0.6 ms | 2.9 ms (11) | 1.2 | 0 | 55 | 1.5 MB (1.7) |
| 500 | main | 0.42 s | 2.4 ms | 26.3 ms (83) | 20.1 | 1,085 | 332 | 6.7 MB (7.2) |
| 500 | simple | 0.69 s | 3.7 ms | 37.2 ms (337) | 20.1 | 1,085 | 332 | 6.8 MB (7.3) |
| 500 | osm | 0.47 s | 3.2 ms | 17.6 ms (124) | 8.4 | 1 | 284 | 6.9 MB (7.2) |
| 1,000 | main | 0.84 s | 5.0 ms | 55.5 ms (230) | 39.2 | 2,949 | 758 | 13.3 MB (13.8) |
| 1,000 | simple | 0.93 s | 8.6 ms | 60.9 ms (223) | 39.2 | 2,949 | 758 | 13.4 MB (13.8) |
| 1,000 | osm | 0.88 s | 6.4 ms | 44.6 ms (97) | 26.1 | 194 | 605 | 13.5 MB (13.8) |
| 2,000 | main | 1.62 s | 10.8 ms | 141.8 ms (396) | 85.7 | 6,198 | 1,679 | 26.5 MB (26.9) |
| 2,000 | simple | 1.72 s | 15.1 ms | 143.5 ms (398) | 85.7 | 6,198 | 1,679 | 26.5 MB (27.0) |
| 2,000 | osm | 1.73 s | 13.0 ms | 146.8 ms (906) | 73.9 | 1,750 | 1,401 | 26.8 MB (27.8) |
| 5,000 | osm | 7.50 s | 62.3 ms | 777.3 ms (1,306) | 244.0 | 5,805 | 4,979 | 64.8 MB (68.6) |
| 10,000 | osm | 13.90 s | 120.4 ms | 1,325.2 ms (2,829) | 377.6 | 16,370 | 9,973 | 128.2 MB (139.4) |

(5,000 and 10,000 vehicles: 120 simulated seconds.)

- **The simple model is unchanged.** Every count (reroutes, searches, vehicles still driving, and the accident and policy results below) is identical to main. Its timings differ by run-to-run noise in both directions (the busiest second at 500 vehicles was slower, at 2,000 the same).
- **The osm model costs about the same per second** (146.8 against 143.5 ms at 2,000 vehicles), but reroutes far less (1,750 against 6,198): congestion now builds up gradually with how full a road is, instead of jumping to 60% and 30% when a second and third car arrive, so routes rarely look worse enough to switch.
- **10,000 vehicles run.** Moving them takes 120 ms per simulated second (8x faster than real time); with dynamic rerouting a simulated second takes 1.33 s, so a 10,000-vehicle run is slower than real time, while 5,000 (0.78 s) is still faster. Memory grows linearly, about 13 MB per 1,000 vehicles.

## 3. Severe accident on the busiest road (1,000 vehicles, 1,200 simulated seconds)

The accident starts as the fleet sets off, on the road the most planned routes use, and lasts the whole run.

| Model | | Vehicles whose route used the road | Of those, arrived | Their mean journey | Whole fleet mean journey | Reroutes |
|---|---|---|---|---|---|---|
| simple (= main) | without rerouting | 127 | 125 | 779 s | 508 s | 0 |
| simple (= main) | with rerouting | 127 | 127 | 588 s | 455 s | 4,121 |
| osm | without rerouting | 137 | 1 | 1,010 s | 348 s | 0 |
| osm | with rerouting | 137 | 137 | 474 s | 362 s | 336 |

In the osm model a severe accident cuts the road's capacity to a fraction, so a queue forms behind it and, without rerouting, only 1 of the 137 vehicles heading through it got past in 20 minutes; with rerouting all 137 arrived, in 474 s on average, with 336 route changes instead of 4,121. (Journeys are shorter overall in the osm model because a lone vehicle is never slowed and most roads are far below capacity; the simple model slows any road with two cars on it to 60%.)

## 4. Rerouting policies (600 vehicles, 1,200 simulated seconds, seed 1)

| Model | Policy | Arrived | Mean journey | Route changes per vehicle | Most for one vehicle | Route searches | Searches skipped | Per simulated s |
|---|---|---|---|---|---|---|---|---|
| simple (= main) | No dynamic rerouting | 600 | 444 s | 0.0 | 0 | 0 | 0 | 1-2 ms |
| simple (= main) | Default: check every 10 s, 60 s cooldown, save >= 20 s and 10% | 600 | 407 s | 3.0 | 10 | 9,048 | 5,970 | 10 ms |
| simple (= main) | Default + traffic averaged over 30 s | 600 | 418 s | 2.0 | 8 | 10,101 | 8,701 | 11 ms |
| simple (= main) | Eager: 30 s cooldown, save >= 10 s and 5% | 600 | 384 s | 6.0 | 18 | 10,644 | 4,781 | 10 ms |
| osm | No dynamic rerouting | 600 | 362 s | 0.0 | 0 | 0 | 0 | 1 ms |
| osm | Default | 600 | 356 s | 0.0 | 1 | 3,434 | 17,437 | 6 ms |
| osm | Default + traffic averaged over 30 s | 600 | 359 s | 0.0 | 1 | 2,901 | 18,165 | 6 ms |
| osm | Eager | 600 | 357 s | 0.0 | 4 | 6,030 | 14,884 | 8 ms |

With 600 vehicles the osm model's roads rarely fill up, so the default policy saves only 6 s per journey (356 against 362 s) and almost never switches; most checks are skipped by the travel-time lower bound before any search (17,437 skipped against 3,434 searches), which is why rerouting costs 6 ms per second instead of 10.

## 5. The osm model on Norwich

From `python -m backend.simulation.check_road_model` (1,000 random routes, a random half of roads slowed per direction):

| Check | Result |
|---|---|
| A\* with speed limits (straight-line and travel-time landmark estimates) against plain Dijkstra, 1,000 routes | the same cost every time (0 mismatches) |
| Planned against driven time, 200 single-car trips | driven 0.012 s to 1.000 s longer (the journey clock counts whole seconds); arrival estimates equal to the plan |

| Search (1,000 routes, same traffic) | Mean | Median | vs simple model |
|---|---|---|---|
| simple model, A\* (straight-line estimate) | 2.57 ms | 2.07 ms | 1.00x |
| osm model, plain Dijkstra | 10.73 ms | 10.62 ms | 4.17x |
| osm model, A\*, straight-line estimate only | 3.74 ms | 3.14 ms | 1.45x |
| osm model, A\*, travel-time landmarks (default) | 3.87 ms | 2.90 ms | 1.50x |

A speed-limited search costs more than a simple one: its straight-line estimate has to assume the fastest road anywhere (40 mph) while most of Norwich is 20 or 30 mph, so A\* explores about twice as many junctions. This test slows half of all roads, far more than real traffic; there the travel-time landmarks are about level with the straight-line estimate (mean 3.87 against 3.74 ms, median 2.90 against 3.14). On real traffic from a 2,000-car simulation (811 congested roads), the same 1,000 routes took 2.18 ms with the best 2 travel-time landmarks against 3.02 ms with the straight-line estimate, exploring 525 junctions against 1,126 (measured during development with Low Power Mode on, so compare the two, not the absolute times). Whole simulations show the net effect: the osm model costs about the same per simulated second as the simple one (section 2).

## 6. How much the 30 m minimum road length matters

Capacity is lanes x max(length, minimum) / 7.5 m (a quarter of Norwich's roads are under 20 m). The same 2,000-vehicle runs with different minimums (300 simulated seconds; accident and policy runs shortened to 600 s and 300 vehicles):

| Minimum length | Dynamic, per simulated s | Reroutes | Still driving at end | Accident: arrived with rerouting (of 137) | Whole fleet mean journey (accident run) |
|---|---|---|---|---|---|
| 15 m | 128.2 ms | 1,707 | 1,415 | 111 | 341 s |
| 30 m (default) | 137.4 ms | 1,750 | 1,401 | 110 | 338 s |
| 50 m | 124.1 ms | 1,557 | 1,372 | 114 | 339 s |

The results move little: whole-fleet journeys within 1%, reroutes within 11%. The choice of 30 m is not driving the conclusions.

## 7. Batched rerouting

Measured on the branch (2,000 vehicles, osm model, a severe accident on the busiest road at 30 s, 300 simulated seconds):

| Trips | Batching | Per simulated s | Individual searches | Shared searches (vehicles served) | Reroutes |
|---|---|---|---|---|---|
| ordinary demand | off | 129.7 ms | 25,656 | 0 | 1,655 |
| ordinary demand | on (3+ vehicles) | 130.3 ms | 25,558 | 55 (98) | 1,655 |
| ordinary demand | on (2+ vehicles) | 152.7 ms | 24,239 | 930 (1,417) | 1,655 |
| all into a 250 m city-centre zone | off | 244.9 ms | 29,672 | 0 | 5,757 |
| all into a 250 m city-centre zone | on (3+ vehicles) | **83.9 ms** | 2,414 | 4,114 (27,258) | 5,757 |

Journeys were identical with and without batching. It pays when trips share destinations (2.9x per step with every trip going to one of 15 junctions); on ordinary Norwich demand, destinations are spread over 1,525 junctions, so it almost never applies (and a threshold of 2 is slower, since a shared search costs more than two ordinary ones). The threshold is 3.
