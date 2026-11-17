# The optimiser's answers tested in traffic

Run on 2026-11-17 with Python 3.9.6 (macOS-26.3.1-arm64-arm-64bit), 4 worker processes. Reproduce with `python -m backend.optimiser.run_evaluation --kind ranking --top 5 --save`.

The optimiser (single-site ranking or station set, 300 emergencies, 20 m/s, 7 min target, uniform emergency locations) picked the candidates below; each was then simulated with the osm traffic model at 500, 1500 background vehicles and seeds 1, 2, 3: 12 ambulance emergencies per run, one every 45 s from 120 s, 2 vehicles per station, realistic dispatch (fastest vehicle, queue, drive back, right of way). Every candidate faces the same trips and emergencies for a given demand level and seed. 36 scenarios in 6.5 min (1489 s of simulation in total).

| Rank in traffic | Candidate | Optimiser's own estimate | Estimate for the simulated emergencies | Simulated mean | Gap (same emergencies) | Rank by optimiser |
|---|---|---|---|---|---|---|
| 1 | Site #3 | 2.80 min | 2.09 min | 4.91 min | +135% | 3 |
| 2 | Site #4 | 2.86 min | 2.26 min | 5.08 min | +125% | 4 |
| 3 | Site #1 | 2.68 min | 2.18 min | 5.63 min | +158% | 1 |
| 4 | Site #2 | 2.73 min | 2.15 min | 5.66 min | +163% | 2 |
| 5 | Site #5 | 3.20 min | 2.96 min | 6.43 min | +117% | 5 |

Per demand level (mean ± 95% interval over the seeds; p90 and worst pooled over all runs):

| Candidate | Vehicles | Mean response | Of which waiting for a vehicle | p90 | Worst | Within target | Unreached | Network delay | Added by the response |
|---|---|---|---|---|---|---|---|---|---|
| Site #3 | 500 | 4.88 ± 3.52 min | 2.24 min | 10.00 min | 10.93 min | 69 ± 43 % | 0 | 0.8 ± 0.0 veh-h | 0.00 ± 0.00 veh-h |
| Site #3 | 1500 | 4.94 ± 3.56 min | 2.28 min | 10.03 min | 11.07 min | 69 ± 43 % | 0 | 8.6 ± 1.1 veh-h | -0.09 ± 0.75 veh-h |
| Site #4 | 500 | 5.03 ± 1.10 min | 2.32 min | 8.55 min | 10.35 min | 75 ± 21 % | 0 | 0.8 ± 0.0 veh-h | 0.00 ± 0.00 veh-h |
| Site #4 | 1500 | 5.12 ± 1.06 min | 2.40 min | 8.72 min | 10.47 min | 75 ± 21 % | 0 | 8.6 ± 1.0 veh-h | -0.07 ± 0.92 veh-h |
| Site #1 | 500 | 5.83 ± 2.28 min | 3.00 min | 9.50 min | 11.42 min | 58 ± 21 % | 0 | 0.8 ± 0.0 veh-h | 0.00 ± 0.00 veh-h |
| Site #1 | 1500 | 5.43 ± 2.46 min | 2.70 min | 8.92 min | 11.53 min | 67 ± 41 % | 0 | 8.6 ± 1.0 veh-h | -0.08 ± 0.81 veh-h |
| Site #2 | 500 | 5.60 ± 3.41 min | 2.78 min | 10.40 min | 11.82 min | 67 ± 36 % | 0 | 0.8 ± 0.0 veh-h | -0.00 ± 0.02 veh-h |
| Site #2 | 1500 | 5.73 ± 3.52 min | 2.89 min | 10.47 min | 11.98 min | 64 ± 43 % | 0 | 8.6 ± 1.0 veh-h | -0.07 ± 0.82 veh-h |
| Site #5 | 500 | 6.36 ± 2.15 min | 3.48 min | 9.95 min | 11.87 min | 61 ± 32 % | 0 | 0.8 ± 0.0 veh-h | 0.01 ± 0.02 veh-h |
| Site #5 | 1500 | 6.50 ± 2.32 min | 3.60 min | 10.08 min | 11.95 min | 61 ± 32 % | 0 | 8.4 ± 1.1 veh-h | -0.28 ± 0.92 veh-h |

Kendall's tau between the optimiser's order and the simulated order: 0.20.

What it says:

- Site #3 answers fastest in traffic: a mean response of 4.9 min over 2 demand level(s) and 3 seed(s), against 2.8 min estimated without traffic.
- Of its 4.9 min, 2.3 min was waiting for a free vehicle and 2.7 min driving; the drive was +27% on the optimiser's free-flow estimate for the same emergencies, so most of the difference is vehicles being busy, which the optimiser does not model.
- For the same emergencies, simulated responses differed from the optimiser's free-flow estimate by +117% to +163% (traffic, queues while vehicles were busy, and dispatch).
- The simulation ranked the candidates in a different order from the optimiser (Kendall's tau 0.20).
- At 1500 vehicles the 95% intervals of the top two overlap, so more seeds would be needed to be sure of the order.

## Reading these results

(Written by hand from the tables above; `--save` regenerates only the part above.)

- **This is a busy period on purpose:** 12 calls in about 8 minutes for one station with 2 ambulances, so vehicles are often all out. The optimiser assumes a vehicle is always free; here about half of every response (2.2 to 3.6 min of 4.9 to 6.5 min) was waiting for one. That, not traffic, is most of the gap to its estimate.
- **The drive itself took 27% longer than the free-flow estimate** for the top site. Traffic alone adds about 2% (`python -m backend.optimiser.validate`, 1,000 cars); the rest is that a busy ambulance is often sent from wherever it is, on its way back from the last call, rather than from its station, which is what the estimate assumes.
- **Traffic level barely mattered:** each site's mean response is within about 0.4 min between 500 and 1,500 background vehicles, while network delay grew tenfold.
- **The emergency response had no measurable effect on other traffic:** every "added by the response" interval includes zero (two ambulances among 500 to 1,500 cars).
- **The order changed, but not decisively.** The optimiser's top two sites (#1, #2) came 3rd and 4th in traffic, and sites #3 and #4 came 1st and 2nd (Kendall's tau 0.20), but the 95% intervals with three seeds overlap widely, so the honest conclusion is that under this load the first four sites are not clearly separated, and that the station's capacity (vehicles per station) matters more than the small differences in location the free-flow ranking measures. More seeds (`--seeds 1 2 3 4 5 6 7 8`) would narrow the intervals.
