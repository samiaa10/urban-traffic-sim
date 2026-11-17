# Scenario engine benchmark

Full simulations on the real Norwich map (5,058 junctions, 10,980 road segments).
Produced by `python -m backend.scenarios.benchmark_scenarios --save`.

> Measured on 2026-11-16, before Stage 5, with the original (simple) traffic rules and scenario engine version 1. It was not re-run for Stage 5: scenarios now default to the osm model (`--traffic-model osm`), whose per-second cost is about the same as the simple model's on Norwich (see `docs/traffic_benchmark.md`, section 2), but these exact figures have not been re-measured.

- Date: 2026-11-16
- Machine: macOS-26.3.1-arm64-arm-64bit, 8 CPUs, Python 3.9.6

## 1. One scenario, by fleet size

Each run in a fresh process; trips leave at once and the run ends when the last arrives.

| Vehicles | Wall time (s) | CPU time (s) | Simulated (s) | Vehicle-seconds per second | Peak memory (MB) | Network delay (vehicle-hours) | Mean journey-time increase (s) |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 200 | 5.7 | 5.7 | 854 | 11,407 | 78 | 2.2 | 51 |
| 500 | 16.2 | 16.1 | 939 | 11,901 | 84 | 12.5 | 115 |
| 1,000 | 38.5 | 38.2 | 967 | 11,618 | 89 | 40.6 | 180 |
| 2,000 | 104.6 | 103.7 | 1,172 | 10,236 | 99 | 128.7 | 266 |

## 2. What measuring the impact costs

The same 500-vehicle scenario with and without the impact accounting, alternating, 4 times each (median): 14.17 s without, 14.48 s with, **+2.2%**.

## 3. Parallel speed-up

The same 16 scenarios of 500 vehicles (seeds 1 to 16) with a pool of worker processes.

| Workers | Wall time (s) | Speed-up | Efficiency | Scenarios per hour | Mean time per scenario (s) | Peak memory per worker (MB) |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 238.0 | 1.00x | 100% | 242 | 14.8 | 84 |
| 2 | 120.6 | 1.97x | 99% | 478 | 14.7 | 84 |
| 4 | 91.2 | 2.61x | 65% | 632 | 21.7 | 85 |
| 6 | 79.6 | 2.99x | 50% | 723 | 26.2 | 81 |
| 8 | 67.6 | 3.52x | 44% | 852 | 31.7 | 84 |

## 4. Saving a result

Storing one 500-vehicle result (500 trips in the per-trip arrays, 51 rows in all) in a local PostgreSQL, 10 times: median 17.0 ms, at most 27.2 ms.

## 5. A large study

400 scenarios run by the API's own JobManager with 4 workers and stored in PostgreSQL: a baseline of 300 trips leaving over 5 minutes, and 399 scenarios each closing 3 roads drawn at random from the 1576 roads within 1.5 km of the city centre.

- Wall time: **21.5 minutes** (1,119 scenarios per hour); 400 completed, 0 failed.
- Mean time per scenario in a worker: 12.8 s; peak memory per worker 79 MB.
- Baseline network delay: 3.2 vehicle-hours; with closures: median 3.2, from 2.9 to 3.9.
- Extra travel time against the baseline, trip by trip: median +0.18 vehicle-hours, 90th percentile +0.49, worst +1.23, best -0.21.
- 306 closure sets made total travel time longer and 77 shorter; in 116 some trips had no route at all.

## Reading the results

- **Measuring is cheap.** The impact accounting adds about 2% to a run
  (one pass over the vehicles on the road each second, with each
  vehicle's road cached until it moves on). A first version added 8.4%;
  caching the road and dropping finished vehicles from the pass fixed it.
- **A scenario costs more than its fleet size suggests.** Throughput
  stays near 10,000 to 12,000 vehicle-seconds per second, but bigger
  fleets also congest more, so trips take longer and there are more
  vehicle-seconds to simulate: 200 vehicles take about 6 s, 2,000 about
  105 s. A worker process peaks at 78 to 99 MB.
- **Parallel speed-up is real but limited by this machine.** Two workers
  are almost twice as fast; beyond that each scenario itself slows down
  (14.8 s alone, 21.7 s with four running, 31.7 s with eight), because
  this Mac has four performance and four efficiency cores that share
  power and memory bandwidth. The job manager is not the limit: it keeps
  every worker busy (one scenario per free worker), and the workers do
  not touch the database. On a machine with more performance cores the
  efficiency at 4 or more workers should be higher; that was not
  measured here.
- **Saving is negligible.** About 17 ms per result against 13 to 16 s of
  simulation.
- **The large study** ran 400 full simulations through the same job
  manager and database the API uses, with no failures, at about 1,100
  scenarios an hour on 4 workers. Random closures near the centre mostly
  cost time, but 77 of 399 closure sets made total travel shorter:
  closing a road can move traffic off a congested route (a Braess-like
  effect). Journey-time increase still counts the detours, which is why
  the comparison reports both.
