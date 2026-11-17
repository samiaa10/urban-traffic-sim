# Profiling

Run on 2026-11-17 with Python 3.9.6 (macOS-26.3.1-arm64-arm-64bit). Reproduce with `python -m backend.perf.profile --save`.
### 1. Dynamic traffic (1,000 cars, 120 simulated s, osm model)

19.51 s wall-clock with profiling on (profiling slows Python code, so compare shares, not seconds).

| Function | Calls | Time inside | Share |
|---|---|---|---|
| `travel_time.py:494 fastest_path` | 5,008 | 5.42 s | 28% |
| `built-in:0 <method 'get' of 'dict' objects>` | 26,590,095 | 3.46 s | 18% |
| `travel_time.py:326 estimate` | 2,642,772 | 3.28 s | 17% |
| `traffic_manager.py:326 factor` | 5,437,942 | 0.87 s | 4% |
| `built-in:0 <built-in method _heapq.heappop>` | 2,265,654 | 0.77 s | 4% |
| `travel_time.py:620 path_seconds` | 11,757 | 0.59 s | 3% |
| `built-in:0 <built-in method _heapq.heappush>` | 2,637,764 | 0.57 s | 3% |
| `simulation.py:570 get_traffic_density` | 360 | 0.40 s | 2% |

### 2. Route searches (500 under that traffic)

1.26 s wall-clock with profiling on (profiling slows Python code, so compare shares, not seconds).

| Function | Calls | Time inside | Share |
|---|---|---|---|
| `travel_time.py:494 fastest_path` | 500 | 0.50 s | 39% |
| `travel_time.py:326 estimate` | 252,133 | 0.30 s | 24% |
| `built-in:0 <method 'get' of 'dict' objects>` | 2,188,007 | 0.25 s | 20% |
| `built-in:0 <built-in method _heapq.heappop>` | 216,451 | 0.07 s | 6% |
| `built-in:0 <built-in method _heapq.heappush>` | 251,633 | 0.05 s | 4% |
| `traffic_manager.py:320 <lambda>` | 472,708 | 0.05 s | 4% |
| `built-in:0 <built-in method math.hypot>` | 252,133 | 0.02 s | 1% |
| `traffic_manager.py:350 plan` | 500 | 0.01 s | 0% |

### 3. Station optimiser (ranking + 3-station set)

1.53 s wall-clock with profiling on (profiling slows Python code, so compare shares, not seconds).

| Function | Calls | Time inside | Share |
|---|---|---|---|
| `station_optimiser.py:64 shortest_distances` | 76 | 0.56 s | 37% |
| `station_optimiser.py:102 reach_count` | 659 | 0.20 s | 13% |
| `built-in:0 <method 'get' of 'dict' objects>` | 1,852,269 | 0.15 s | 10% |
| `graph.py:27 get_neighbours` | 651,609 | 0.13 s | 8% |
| `built-in:0 <built-in method _heapq.heappop>` | 378,838 | 0.13 s | 8% |
| `built-in:0 <built-in method _heapq.heappush>` | 378,762 | 0.05 s | 3% |
| `built-in:0 <built-in method builtins.min>` | 96,198 | 0.04 s | 3% |
| `station_optimiser.py:700 exhaustive_best_set` | 1 | 0.04 s | 3% |

### 4. One scenario (500 vehicles, to the end)

15.87 s wall-clock with profiling on (profiling slows Python code, so compare shares, not seconds).

| Function | Calls | Time inside | Share |
|---|---|---|---|
| `travel_time.py:494 fastest_path` | 3,580 | 3.17 s | 20% |
| `built-in:0 <method 'get' of 'dict' objects>` | 18,522,193 | 2.56 s | 16% |
| `travel_time.py:326 estimate` | 1,642,772 | 2.00 s | 13% |
| `travel_time.py:620 path_seconds` | 16,710 | 0.58 s | 4% |
| `vehicle.py:142 move` | 170,836 | 0.48 s | 3% |
| `built-in:0 <built-in method _heapq.heappop>` | 1,379,115 | 0.45 s | 3% |
| `simulation.py:570 get_traffic_density` | 2,659 | 0.45 s | 3% |
| `traffic_manager.py:326 factor` | 2,747,790 | 0.43 s | 3% |

<!-- profile:end -->

## What the profiles led to

The first profiles (before the Stage 5 performance work) pointed at two things:

- **The optimiser spent 55% of its time in `shortest_distances`**, 735 calls for one ranking and one set: snapping each candidate site ran a full Dijkstra from up to 50 nearby junctions just to check they can reach 90% of the map. That check is a fixed property of the map, so it is now a cached breadth-first search (`reach_count`); the same workload now makes 76 `shortest_distances` calls (one per candidate, for the ranking and again for the set), and finding the 50 nearest junctions uses the spatial index instead of sorting all 5,058.
- **Route searches dominate dynamic traffic and scenarios** (about 60%: `fastest_path`, its estimate and dictionary lookups). Each search was made cheaper (travel-time landmark bounds for speed-limited searches, which explore about half the junctions), checks that cannot save enough are skipped with the tighter bound, and vehicles heading to the same junction can share one search (batched rerouting). What is left is the search itself in pure Python; the next step would be a compiled or vectorised search, which is outside this project's pure-Python design.

Before and after numbers for each change are in `docs/ARCHITECTURE.md` (Performance engineering) and `docs/traffic_benchmark.md`.
