# Disruption and resilience

How Norwich's road network, its traffic and its emergency services cope with major disruptions: floods, bridge failures, road closures, accidents and extra demand, singly or together, with gradual recovery. The feature is built into the existing road network, traffic models, routing, emergency dispatch, station optimiser and scenario engine.

Code:
- [`simulation/disruptions.py`](../backend/simulation/disruptions.py): floods, and combining incidents.
- [`simulation/infrastructure.py`](../backend/simulation/infrastructure.py): bridges.
- [`scenarios/resilience.py`](../backend/scenarios/resilience.py): the analytics.

Tests: [`backend/tests/test_resilience.py`](../backend/tests/test_resilience.py). Experiments: [`simulation/benchmark_resilience.py`](../backend/simulation/benchmark_resilience.py), with results in [`resilience_benchmark.md`](resilience_benchmark.md).

## Data, and what it can't tell us

The project's map is OpenStreetMap's road network for Norwich. The raw download, committed in `data/osm/source/`, was checked for anything flood-related:

| Looked for | Found |
|---|---|
| Elevation (`ele`), terrain | none |
| Waterways (`waterway`), the River Wensum or Yare | none: the download contains roads only |
| `flood_prone`, fords, embankments | none, 0 and 4 |
| Bridges (`bridge=*`) | 66 road ways, matched to 21 graph roads (`data/osm/norwich_bridges.json`) |

There is no elevation, rainfall, river or flood-risk data, so **this is not a hydrological flood model**. Which roads flood, how badly and for how long are inputs chosen by the user, not predictions. The experiments use an illustrative area of 350 m around a point on the Wensum by Riverside, chosen by hand, and say so. Using real flood extents, such as the Environment Agency's flood-zone maps or a hydraulic model's output, would only need those roads as input.

Bridges can't be told apart either: with no waterways in the data, a river bridge looks the same as a flyover. The bridge list keeps every bridge with its road name (Whitefriars, Fye Bridge Street, Duke Street, Prince of Wales Road and others).

## What was reused, and what is new

| Existing mechanism | Reused for |
|---|---|
| Closures (`Simulation.block_segment`) | Closing flooded and failed roads. Rerouting, the recorder and the map react exactly as they do to any closure. |
| Accidents' speed and capacity factors | A partly flooded road is slowed the same way, and every traffic model (`simple`, `osm`, `ca`) applies it with no change. |
| Event-driven rerouting (`TrafficManager.process_events`) | Vehicles whose route crosses a newly flooded road re-plan at once. When a flood clears, the vehicles that avoided it are re-checked. |
| Scenario engine, seeds, studies, Compare | Floods, failures and resilience measures are scenario fields. Specs without them keep their fingerprints and results. |
| Surges | Extra demand during a disruption. |
| Emergency dispatch and stations | Emergencies during disruptions are dispatched around closed roads by the existing realistic rules. |
| Station optimiser (`optimise_station_set`, which already takes closed roads) | Choosing alternative stations for normal and flooded roads. |
| Traffic signals with emergency priority | Testing whether priority helps during a disruption. |
| Area cells (`impact.area_cell`) | Locating the worst-hit areas. |

What's new:
- **Floods.** A flood affects a set of roads, either chosen or every road inside a circle, at a severity:

  | Severity | Speed | Capacity |
  |---|---|---|
  | minor | 60% | 70% |
  | moderate | 35% | 50% |
  | severe | 15% | 25% |
  | closed | road closed | road closed |

  The flood lasts `duration_seconds`, then recovers in a straight line over `recovery_seconds`. A closed road reopens at the start of recovery, still at 30% speed and 40% capacity. Like the accidents', these figures are modelling choices.
- **Infrastructure failures.** Bridges by name, in an area, or all of them, out of use for a while.
- **Overlapping disruptions combine instead of overwriting:**
  - Each closure remembers who closed it (a manual closure, a flood, a failure, each scenario closure separately). A road reopens only when nobody still has it closed. Before this, two overlapping closures of one road ended together.
  - An accident and any floods on one road multiply their speed and capacity factors.
  - A flood's events (`flood` and `flood_cleared`) drive rerouting like accidents do, and the run recorder ignores them, so saved runs stay consistent.
- **Resilience analytics**, described next.

## Resilience measures

**Network accessibility.** This is measured on the road network itself, not on whichever trips happened to be drawn. From every junction trips can start at to 60 sampled destinations (chosen by the scenario's seed), it uses the fastest drive at the speed limits, around closed roads and slowed by incidents (not by traffic). Each snapshot reports:
- the share of junction pairs still connected;
- mean and worst drive time;
- mean extra drive time against the undisrupted network;
- the worst-hit areas (about 500 m squares), ranked by pairs cut off, then by extra drive time.

**Emergency accessibility.** The same, from each service's stations at its vehicles' speed: the share of destinations reachable, and reachable within the service's target.

**Snapshots** are taken:
- **before** anything happens;
- **during**, at the moment of greatest disruption, re-measured whenever more roads are affected;
- **after**, once every disruption has ended.

**Dynamic figures** come from the scenario engine's own per-minute series, split into the same three windows:
- arrivals per minute (throughput);
- mean speed as a share of free flow (congestion);
- delay share;
- emergency responses by when the call came;
- **recovery time**: minutes after the disruption ends until mean speed is back within 2 points of its pre-disruption level.

On top of these are the usual scenario totals: trips completed and unroutable, mean and worst journey, delay, and emergency response.

The experiments compare each disrupted scenario against the **normal scenario with the same seed**, so it has the same trips: the same trips in normal and disrupted conditions give each trip's extra time directly. They report the mean and a 95% confidence interval over five seeds.

## Using it

- **Live (dashboard).** In Live controls, "Floods and failures":
  - Choose a radius, severity, duration and recovery time, click "Place a flood on the map", then click the map.
  - The flooded roads are drawn in water blue on the 2D and 3D maps, fading as they recover.
  - Each flood shows its progress ("12 roads recovering: 40%") with a Clear button.
  - "Bridges out of use" closes every mapped bridge.
  - Vehicles reroute around all of it, and the existing route and diversion layers show the alternative routes.
  - The **Network resilience** card measures accessibility now against the undisrupted network: connections, extra drive time, each service's reach within target, and the worst-hit areas.
- **Studies.** Each scenario can have "Flood by the river" and/or "Central bridges out". Both switch on the resilience measures. Run them alongside a normal scenario and use Compare for before-and-after comparisons.
- **API:**
  - `POST /incident/flood?latitude&longitude&radius_metres&severity&duration_seconds&recovery_seconds`
  - `GET /incident/floods`
  - `POST /incident/flood/clear?flood_id`
  - `POST /incident/bridges?fail=true|false[&names=…]`
  - `GET /resilience/accessibility`

  Each is also available per run, under `/simulations/{id}/…`. Scenario specs take `floods`, `failures` and `resilience`.

## Results

Full tables are in [`resilience_benchmark.md`](resilience_benchmark.md), from `python -m backend.simulation.benchmark_resilience --save` (Apple M1, Low Power Mode off, 27 minutes). The setup:
- **Model and demand:** `osm`, 1,500 trips over 30 minutes, dynamic rerouting.
- **Emergencies:** 20 ambulance calls; one central station with 3 ambulances unless stated.
- **Disruptions:** from 5 minutes for 20; the flood then recovers over 10.
- **Statistics:** five seeds, mean ± 95% confidence interval.

| Scenario | Junction pairs connected (worst moment) | Mean extra drive time on the network (s) | Extra time per trip vs normal (s) | Trips with no route | Worst journey (s) | Ambulance access within 7 min |
|---|---|---|---|---|---|---|
| Normal | 100% | 0 | 0 | 0 | 881 ± 43 | 100% |
| Single closure (Foundry Bridge) | 100% | 1.2 ± 0.3 | 1.1 ± 0.4 | 0 | 880 ± 45 | 100% |
| Multiple (4 bridges, 2 severe accidents, 1 closure) | 100% | 18.1 ± 1.4 | 17.7 ± 1.0 | 0 | 945 ± 87 | 100% |
| Flood (350 m by the Wensum, closed) | 96.0% ± 1.8 | 2.7 ± 0.5 | 8.9 ± 2.3 | 39 ± 11 | 1,637 ± 80 | 97.3% ± 1.9 |
| Flood + 60 extra trips a minute | 96.0% ± 1.8 | 2.7 ± 0.5 | 11.9 ± 2.4 | 122 ± 24 | 1,637 ± 80 | 97.3% ± 1.9 |

What the measurements show:
- **Closing one central bridge barely matters.** Rerouting finds parallel crossings, costing about 1 s per trip.
- **Losing several bridges and the busiest roads at once is felt everywhere.** Every trip loses about 18 s and nobody is cut off. The network has redundancy, but it costs time.
- **A flood is different in kind.** Its main effect isn't delay but **isolation**:
  - About 4% of junction pairs lose all connection.
  - 39 trips (and 122 with extra demand) can't be routed at all.
  - The worst journey nearly doubles, to 1,637 s, because vehicles caught inside the area wait for the water to recede.
  - The worst-hit areas are the 500 m squares around the flood. The first had 2,245 pairs cut off and 36 s of extra drive time from what could still get out.
- **Extra demand during the flood** adds about 3 s per trip and many more stranded trips (122 against 39), because more trips start or end inside the area.
- **Recovery is quick.** Traffic is back to its pre-disruption speed within the first minute after roads reopen (59 s for the closures; immediately for the flood, whose 10-minute recovery happens while demand is tailing off). Network-wide congestion stays light at this demand: mean speed stays above 97% of free flow. **The recovery measure is coarse:** it uses per-minute averages, and this demand doesn't stress the network enough to show a slow recovery.
- **Emergency response.** Ambulance access within 7 minutes falls only where places are cut off (97.3% during the flood). Measured response times differ by less than their uncertainty (149 ± 37 s normal, 152 ± 36 s flood). With 20 calls per run at random places, five seeds aren't enough to separate scenarios that close.
- **Alternative stations don't help with a cut-off area.**
  - **The sets chosen:** the optimiser's three-station sets differ by one station between normal and flooded roads.
  - **Effect on reach:** the 2.7% of places no station can reach is the same in all three setups. Nothing can reach inside a closed-off area.
  - **Response times:** they differ within the noise.
- **Emergency priority at signals made no measurable difference during the flood:** 159 ± 40 s against 157 ± 41 s. That's the same finding as the traffic-signal experiments, for the same reason: emergency vehicles already cross red lights after a short stop, and they aren't held up by queues in this model.
- **Runtime:** a disrupted run costs 1.1 to 3 times a normal one (21 s normal, up to 62 s with extra demand), mostly from rerouting and the resilience snapshots.

## Limitations

1. **No hydrology.** Floods are user-chosen areas, severities and durations. The project has no elevation, rainfall, river or flood-zone data, and the flood area in the experiments is illustrative.
2. **Bridges can't be told apart.** With no waterways in the data, river bridges, flyovers and railway bridges look the same, and only 21 of the 66 bridge ways match a road in the simplified graph.
3. **Accessibility ignores traffic.** It is measured at the speed limits, around closures and incident slowdowns. That isolates what the disruption did to the network; the dynamic figures cover traffic separately.
4. **Light demand.** At 1,500 trips over 30 minutes the network rarely congests, so recovery dynamics and throughput loss are small. Heavier demand (the existing surges) is supported but wasn't swept.
5. **Emergency statistics are thin.** Twenty calls per run and five seeds give wide intervals. More calls or seeds are needed to compare response times reliably.
6. **Vehicles inside a flooded area wait** for the water to recede rather than abandoning their trip or parking.
7. **Studies keep the standard metrics only.** The resilience results (accessibility, windows, areas) are part of a run's result and the experiment report, but aren't stored with studies in the database. The standard metrics already reflect the disruption.
8. **Hand-off between overlapping incidents of the same kind:** two accidents on the same road still replace each other, as before. Accidents and floods, closures and floods, and closures from different sources all combine correctly.

## Ready for integration?

**Yes.**
- **Tested and repeatable:**
  - Floods, failures and overlapping incidents behave correctly in every traffic model.
  - Rerouting, dispatch and unreachable destinations are tested.
  - It's repeatable with fixed seeds, and the resilience measures come from real simulation state.
- **Opt-in:** everything is off unless asked for, so existing runs, studies and scenario fingerprints are unchanged.
- **What not to claim:** that it forecasts floods, or that relocating stations or giving emergency priority improves response during these disruptions. The evidence doesn't show either.
