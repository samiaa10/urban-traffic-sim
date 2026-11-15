# Persistence benchmark

What saving runs to PostgreSQL adds to `POST /simulation/step` on the
real Norwich map. Produced by `python -m backend.db.benchmark_persistence --save`.

- Date: 2026-11-15
- Machine: macOS-26.3.1-arm64-arm-64bit, Python 3.9.6
- Database: PostgreSQL at localhost:5433
- 120 steps per row; the two apps take turns stepping

| Scenario | Vehicles at start | Step, no database (median / p95 ms) | Step, saving (median / p95 ms) | Median difference | Steps that wrote | SQL statements |
|---|---:|---:|---:|---:|---:|---:|
| quiet | 2 | 1.8 / 2.5 | 1.8 / 2.5 | +0.02 ms (+1.1%) | 2 of 120 | 4 |
| quiet | 502 | 62.3 / 110.7 | 62.4 / 111.4 | +0.11 ms (+0.2%) | 2 of 120 | 4 |
| quiet | 2,002 | 265.0 / 537.6 | 264.8 / 527.8 | -0.26 ms (-0.1%) | 2 of 120 | 4 |
| surge | 2 | 16.6 / 38.5 | 16.7 / 38.6 | +0.03 ms (+0.2%) | 2 of 120 | 6 |
| surge | 502 | 91.4 / 123.5 | 91.2 / 123.0 | -0.24 ms (-0.3%) | 2 of 120 | 6 |
| surge | 2,002 | 301.3 / 534.8 | 300.5 / 532.9 | -0.76 ms (-0.3%) | 2 of 120 | 6 |

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
