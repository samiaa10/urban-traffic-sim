# Rerouting benchmark

Run on 2026-10-18 with Python 3.9.6 (macOS-26.3.1-arm64-arm-64bit). Random seed 42. Each timing is the fastest of 3 runs.

The busiest road is closed. "Affected only" recalculates routes just for vehicles that use that road; "recalculate all" recalculates every vehicle.

| Vehicles | Use the closed road | A* searches (affected only) | A* searches (all) | Time (affected only) | Time (all) | Speed-up |
|---|---|---|---|---|---|---|
| 100 | 22 (22%) | 22 | 100 | 322 ms | 647 ms | 2.0x |
| 500 | 77 (15%) | 77 | 500 | 1055 ms | 3471 ms | 3.3x |
| 1000 | 143 (14%) | 143 | 1000 | 1766 ms | 6923 ms | 3.9x |
