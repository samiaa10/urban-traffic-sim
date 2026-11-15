# Dynamic traffic benchmark

Run on 2026-11-15 with Python 3.9.6 (macOS-26.3.1-arm64-arm-64bit). Seed 42. Reproduce with `python -m backend.simulation.benchmark_traffic --sizes 100 500 1000 2000 --steps 300 --accident-vehicles 1000 --accident-steps 1200 --save`. Timings depend on the machine.

Norwich map: 5,058 junctions, 10,881 directed roads, 4,143 junctions in the main connected area (trips are made there). Building the road network took 28 ms; 8 landmarks took 182 ms.

## 1. Route search (300 random trips, a random quarter of roads congested)

| Search | Mean | Median | Junctions explored |
|---|---|---|---|
| A* shortest distance (a_star.py, no traffic) | 4.16 ms | 3.29 ms | 509 |
| A* fastest time, straight-line estimate (default) | 2.02 ms | 1.50 ms | 820 |
| A* fastest time, landmark estimate | 4.12 ms | 2.90 ms | 561 |

Lower bound as a share of the true free-flow road distance (100 trips): straight line 73.8%, landmarks 89.0%. The tighter landmark bound is what lets the traffic manager skip route searches that could not save enough time.

## 2. Scaling with fleet size (300 simulated seconds after spawning)

All vehicles set off together, so these seconds are the busiest of the run. Movement only: rerouting off (vehicles still drive in congestion and accidents). Dynamic: the default rerouting policy. Per simulated second: mean wall-clock time of one `TrafficManager.step()` (worst second in brackets), measured with memory tracing off. Memory: Python allocations (tracemalloc) for spawning the fleet and 60 s of dynamic traffic, measured in a separate run; the road network is shared and not included.

| Vehicles | Spawn (route every vehicle) | Movement only, per simulated s | Dynamic, per simulated s | Route searches per simulated s | Reroutes | Still driving at end | Memory held (peak) |
|---|---|---|---|---|---|---|---|
| 100 | 0.13 s | 0.8 ms (1) | 5.5 ms (55) | 2.7 | 44 | 45 | 1.4 MB (1.6 MB) |
| 500 | 0.70 s | 3.8 ms (5) | 39.7 ms (118) | 20.1 | 1,085 | 332 | 6.7 MB (7.2 MB) |
| 1,000 | 1.25 s | 7.6 ms (9) | 81.8 ms (267) | 39.2 | 2,949 | 758 | 13.3 MB (13.8 MB) |
| 2,000 | 2.44 s | 15.2 ms (18) | 218.5 ms (590) | 85.7 | 6,198 | 1,679 | 26.5 MB (26.9 MB) |

## 3. Severe accident on the busiest road (1,000 vehicles, 1200 simulated seconds)

The accident starts as the fleet sets off, on the road the most planned routes use, and lasts the whole run.

| | Vehicles whose route used the road | Of those, arrived | Their mean journey | Whole fleet mean journey | Reroutes |
|---|---|---|---|---|---|
| Without dynamic rerouting | 127 | 125 | 779 s | 508 s | 0 |
| With dynamic rerouting | 127 | 127 | 588 s | 455 s | 4,121 |

## 4. Rerouting policies (600 vehicles, 1200 simulated seconds, seeds 1, 2)

Each cell lists one value per seed. Mean journey is over vehicles that arrived. Searches and skipped searches exclude the routes planned when spawning.

| Policy | Arrived | Mean journey | Route changes per vehicle | Most for one vehicle | Route searches | Searches skipped (no possible saving) | Per simulated s |
|---|---|---|---|---|---|---|---|
| No dynamic rerouting | 600 / 600 | 444 s / 432 s | 0.0 / 0.0 | 0 / 0 | 0 / 0 | 0 / 0 | 2 ms / 2 ms |
| Default: check every 10 s, 60 s cooldown, save >= 20 s and 10% | 600 / 600 | 407 s / 393 s | 3.0 / 2.8 | 10 / 10 | 9,048 / 9,021 | 5,970 / 5,806 | 15 ms / 14 ms |
| Default + traffic averaged over 30 s | 600 / 600 | 418 s / 409 s | 2.0 / 2.0 | 8 / 8 | 10,101 / 9,842 | 8,701 / 8,249 | 16 ms / 16 ms |
| Eager: 30 s cooldown, save >= 10 s and 5% | 600 / 600 | 384 s / 375 s | 6.0 / 5.8 | 18 / 18 | 10,644 / 10,439 | 4,781 / 4,698 | 16 ms / 15 ms |
