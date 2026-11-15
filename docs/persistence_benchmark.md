# Persistence benchmark

What saving runs to PostgreSQL adds to `POST /simulation/step` on the
real Norwich map. Produced by `python -m backend.db.benchmark_persistence --save`.

- Date: 2026-11-15
- Machine: macOS-26.3.1-arm64-arm-64bit, Python 3.9.6
- Database: PostgreSQL at localhost:5433
- 120 steps per row; the two apps take turns stepping

| Scenario | Vehicles at start | Step, no database (median / p95 ms) | Step, saving (median / p95 ms) | Median difference | Steps that wrote | SQL statements |
|---|---:|---:|---:|---:|---:|---:|
| quiet | 2 | 1.9 / 5.8 | 2.0 / 5.4 | +0.05 ms (+2.3%) | 2 of 120 | 4 |
| quiet | 502 | 63.4 / 116.2 | 63.1 / 115.9 | -0.28 ms (-0.4%) | 2 of 120 | 4 |
| quiet | 2,002 | 277.4 / 553.0 | 277.2 / 555.0 | -0.20 ms (-0.1%) | 2 of 120 | 4 |
| surge | 2 | 17.2 / 39.0 | 17.8 / 41.8 | +0.51 ms (+3.0%) | 2 of 120 | 6 |
| surge | 502 | 94.7 / 127.1 | 93.9 / 127.2 | -0.78 ms (-0.8%) | 2 of 120 | 6 |
| surge | 2,002 | 309.0 / 558.5 | 308.9 / 558.2 | -0.07 ms (-0.0%) | 2 of 120 | 6 |

## Reading the results

- The step itself (moving and rerouting vehicles) dominates; the
  database adds no measurable time. Differences of a few tenths of a
  millisecond either way are noise.
- Only steps where something happened write: a road closed or
  reopened, an accident started or cleared, an ambulance arriving, a
  surge ending, or a metrics snapshot (every 60 simulated seconds).
  Here that is 2 of 120 steps (the snapshots at 60 and 120 s).
- One write (a transaction with a few statements) costs about 2 ms
  against a local PostgreSQL. A first version saved a running
  surge's counts on every step: +2.2 ms per step, +13.5% for a small
  simulation. Those counts are now saved with the other writes and
  when the surge ends, which is why the surge rows match the quiet
  ones. The trade-off: after a crash, counters such as simulated time
  can be up to one snapshot interval behind; events, incidents and
  emergencies are never behind, because they are saved as they happen.

## Station optimiser

Default candidates (about 40 junctions), 200 scenarios. Median of 3 computations and 20 fetches.

| Request | Compute, no database (ms) | Compute + save (ms) | Saved answer via `reuse=true` (ms) | `GET /optimiser/runs/{id}` (ms) |
|---|---:|---:|---:|---:|
| ranking | 727 | 730 | 6.8 | 6.3 |
| station set (3) | 775 | 780 | 5.7 | 5.0 |

Saving an answer costs a few milliseconds next to computing it. A
saved answer comes back in a few milliseconds: that is what
`reuse=true` buys when the same question is asked again (the same
map, settings, candidates, closed roads and optimiser version; see
`OPTIMISER_VERSION` in `backend/optimiser/station_optimiser.py`).
