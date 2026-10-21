# Congestion-aware routing benchmark

Run on 2026-10-21 with Python 3.9.6 (macOS-26.3.1-arm64-arm-64bit). Reproduce with `python -m backend.routing.benchmark_congestion --vehicles 2000 --journeys 100 --warmup 60 --seed 42`.

```
NORWICH//SIM: shortest-path vs congestion-aware routing

Map:      5,058 junctions, 10,980 road segments
Traffic:  2,000 vehicles heading for the city centre, snapshot after 60 simulated seconds
          vehicles still driving: 1,951 | roads with traffic: 487 | busiest road: 25 vehicles | heavy roads (3+ vehicles): 245
Journeys: 85 measured, from 100 random start/destination pairs (seed 42); 15 had no route and were skipped
Routing:  same A* search both times. Congestion-aware adds 50 m of cost per vehicle on a road.

Average per journey                       Shortest  Congestion-aware
Route distance (m)                           4,153             4,835   +16.4%
Free-flow travel time (s)                      299               348   +16.4%
Vehicles passed on the route                  83.2              16.0   -80.8%
Heavy roads on the route (3+)                10.01              1.73
Search work (nodes explored)                   690             1,143   +65.5%
Search time (ms)                              4.82              7.64   +58.7%

Free-flow time = distance / 13.9 m/s. The simulation does not slow vehicles in traffic, so this contains no congestion delay.
Vehicles on a road are counted per road, with both directions together.

Journeys where congestion-awareness chose a different route: 69 of 85 (81%)
  For those 69 journeys: +840 m on average (worst +2,708 m), about +60 s free-flow
  Vehicles passed: 100.1 -> 17.4 on average
  Trade-off: about 10 m of extra driving for each vehicle avoided

Sanity check passed: the shortest route was never longer, and the congestion-aware route never cost more (as both are optimal).
```
