# Congestion-aware routing benchmark

Run on 2026-11-03 with Python 3.12.3 (Linux-6.18.44-fc-v230-x86_64-with-glibc2.39). Reproduce with `python -m backend.routing.benchmark_congestion --vehicles 2000 --journeys 100 --warmup 60 --seed 42`.

```
NORWICH//SIM: shortest-path vs congestion-aware routing

Map:      5,058 junctions, 10,980 road segments
Traffic:  2,000 vehicles heading for the city centre, snapshot after 60 simulated seconds
          vehicles still driving: 1,993 | roads with traffic: 533 | busiest road: 26 vehicles | heavy roads (3+ vehicles): 317
Journeys: 85 measured, from 100 random start/destination pairs (seed 42); 15 had no route and were skipped
Routing:  same A* search both times. Congestion-aware adds 50 m of cost per vehicle on a road.

Average per journey                       Shortest  Congestion-aware
Route distance (m)                           4,153             4,696   +13.1%
Free-flow travel time (s)                      299               338   +13.1%
Travel time with traffic (s)                   518               447   -13.6%
Vehicles passed on the route                  59.4              23.3   -60.7%
Heavy roads on the route (3+)                 9.01              3.59
Search work (nodes explored)                   690             1,161   +68.1%
Search time (ms)                              4.74              7.66   +61.6%

Free-flow time = distance / 13.9 m/s, as if the roads were empty. Time with traffic drives each road at the speed the simulation allows for the vehicles on it (estimated from the frozen snapshot).
Vehicles on a road are counted per road, with both directions together.

Journeys where congestion-awareness chose a different route: 68 of 85 (80%)
  For those 68 journeys: +679 m on average (worst +2,554 m), about +49 s free-flow
  Vehicles passed: 70.4 -> 25.3 on average
  Trade-off: about 15 m of extra driving for each vehicle avoided

Sanity check passed: the shortest route was never longer, and the congestion-aware route never cost more (as both are optimal).
```
