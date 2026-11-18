# Traffic signals

Signalised junctions with fixed-time and adaptive control, and emergency vehicle priority, working with all three traffic models (`simple`, `osm` and the experimental `ca`). The signals genuinely stop vehicles, and the time they wait is in every journey time, delay and response time the simulation reports.

Code:
- [`backend/simulation/signals.py`](../backend/simulation/signals.py): the junctions, phases and controllers.
- [`signal_sites.py`](../backend/simulation/signal_sites.py): where the lights are.

Tests: [`backend/tests/test_signals.py`](../backend/tests/test_signals.py). Experiments: [`backend/simulation/benchmark_signals.py`](../backend/simulation/benchmark_signals.py), with results in [`signals_benchmark.md`](signals_benchmark.md).

## Where the lights are

Not every junction has lights. OpenStreetMap tags traffic lights as `highway=traffic_signals` nodes, and the committed raw download has 218 of them that aren't pedestrian crossings. Mappers often tag the light on each approach a few metres before the junction, and the road graph keeps only junctions. So each light is snapped to the nearest junction with three or more roads, within 30 m.

That gives **121 junctions with lights** (71 three-way, 44 four-way and 6 five-way, counting roads either way). **108 of them get signals.** The other 13 have only one road coming in, because of one-way streets, so there is nothing to take turns. Of the 108, 67 have two approaches, 29 three and 12 four. They're listed in [`data/osm/norwich_signals.json`](../data/osm/norwich_signals.json), rebuilt exactly by `python -m backend.simulation.signal_sites`. A scenario can use these (`"junctions": "osm"`, the default) or any list of junction ids.

## How it fits the existing simulation

Signals are a layer on the existing simulation, not a separate one. The road graph, routing, rerouting, closures, accidents, dispatch and the traffic models are unchanged. A `Simulation` with `signals` set does two things each simulated second:

1. **Before anything moves**, the signals look at where every vehicle is (`SignalSystem.step`):
   - who is waiting at each stop line;
   - who is approaching within 60 m;
   - which emergency vehicles are on their way.

   Then every junction's controller advances one second.
2. **While vehicles move**, the movement code asks the signals whether a vehicle may go through the junction ahead, from its road onto the next one (`SignalSystem.may_cross`):
   - **`simple` and `osm`** (`Vehicle.move`): a vehicle that may not cross drives to the stop line, 0.1 m before the junction, and waits there. A vehicle is a point in these models, so the queue is the vehicles waiting at the line, and it counts towards the road's traffic as before.
   - **`ca`** (`cellular.py`): a red light ahead is a wall at the end of the approach lane, like a closed road. A real queue of stopped cars forms behind the first, cell by cell.

This is the same point where the models already stop vehicles for a closed road ahead, so no movement logic is duplicated.

## Junctions, movements and phases

A signalised junction has **approaches** (roads arriving) and **exits** (roads leaving). A **movement** is approach → exit; U-turns are not movements.

**Conflicts.** Movements are compared by the compass bearing of their roads, with traffic on the left (UK). Seen from above, a movement is a chord across the junction, from its entry lane (just clockwise of its approach) to its exit lane (just anticlockwise of its exit). Two movements from different approaches conflict if their chords cross or they use the same exit. So:
- a left turn and the opposite straight-ahead don't conflict;
- a right turn and the oncoming straight-ahead do;
- two straight-aheads at right angles do.

**Phases** group approaches whose movements are all compatible with each other. Only one phase is ever green, so two conflicting movements are never allowed at once; the tests check every phase of all 108 signalised Norwich junctions. Because most junctions allow right turns, most end up with one approach per phase.

**Sequence.** Each phase change goes green → amber (3 s) → all-red (2 s) → next green, never straight from one green to another. UK red-and-amber is treated as red. Amber is a stop signal here: no new vehicle enters on amber.

**Full junctions.** A vehicle may not enter if its exit road is already full (the yellow-box rule): in `osm`, at the road's capacity; in `ca`, no free cells.

**Queue discharge.** In `simple` and `osm`, a queue leaves at the standard saturation flow of 1,800 vehicles an hour per lane: one vehicle per lane every 2 s, the first 2 s after the green starts. In `ca` the cars accelerate away one after another by the automaton's own rules, so no limit is added.

## Controllers

| Controller | How it decides |
|---|---|
| **Fixed time** (the baseline) | Each phase gets `green` seconds (default 20), then amber and all-red, in turn, whatever the traffic. Each junction starts at its own point in its cycle (from its node id), so they don't all change together. There is no coordination between junctions. |
| **Adaptive** (experimental) | Every second of green, once `min_green` (7 s) has passed, it ends the green if: the green phase has nobody queued or approaching while another phase has; or the green reaches `max_green` (45 s); or another phase with traffic has waited `max_wait` (120 s). The last rule means no approach can be starved. The next phase is the longest-waiting starved one if any, otherwise the one with the most demand (vehicles queued plus half of those approaching within 60 m), with ties broken in cycle order. With no traffic anywhere it stays on the current green. It is fully deterministic. |

These timings are experimental defaults, not Norwich's real ones, which aren't public. Fixed green and adaptive min/max green can be set per scenario and per live run.

## Emergency vehicle priority

Priority is a switch on either controller. An emergency vehicle on a call, within 250 m of a signalised junction on its route, requests the phase that serves its approach:
- **That phase is already green:** it is held for the vehicle, up to 30 s extra.
- **Otherwise:** the current green ends as soon as it has run 5 s, and the requested phase comes next, always through amber and all-red.

Signal priority is separate from the other emergency features:
- **Emergency routing** (`dispatch.py`): which vehicle goes, and by which route. Unchanged.
- **How traffic treats an emergency vehicle** (`services.py`): right of way, and its speed on congested roads. Unchanged. In `ca`, emergency vehicles aren't on the lattice.
- **Red lights without priority:** an emergency vehicle stops for 5 s at a red light and then crosses with caution, as UK law allows. Ordinary vehicles never cross on red.

## Using it

- **Live run (dashboard):** the live controls have a "Traffic signals" group: off, fixed time or adaptive; emergency priority; and the green times. Apply switches the running simulation. The map, 2D and 3D, then draws every approach's light in the colour its drivers see, read from `GET /traffic/signals` each tick, drawn larger and (in 3D) numbered when there's a queue behind it. The legend explains the colours.
- **Comparing:** in the study builder, each scenario can choose no signals, fixed time, adaptive, or adaptive with emergency priority. Run them in one study and use Compare. Journey times, delay and emergency response times are compared as for any study. The signal-specific totals (waits, queues, crossings) are in the experiment report but aren't stored with studies yet.
- **API:**
  - `POST /traffic/signals?controller=fixed|adaptive|none&emergency_priority=…&green=…&min_green=…&max_green=…` switches a live run's signals (also per run, under `/simulations/{id}/traffic/signals`).
  - `GET /traffic/signals` gives each junction's phase, stage, and each approach's light and queue.
  - Scenario specs take `"signals": {"controller": …, "emergency_priority": …, "junctions": "osm" or a list of ids, "green": …, "min_green": …, "max_green": …}`. A spec without signals keeps its old fingerprint and runs exactly as before.

## Results

Full tables are in [`signals_benchmark.md`](signals_benchmark.md), from `python -m backend.simulation.benchmark_signals --save` (Apple M1, Low Power Mode off, seeds 1 to 3, fixed so the results repeat exactly; checked).

### One crossroads, all three traffic models

200 m arms, one lane each, 30 mph. Each arm gets its own phase, because every right turn conflicts. Cars arrive for 5 minutes, then the simulation runs 5 more to clear.

| Demand | Control | Mean journey (s): simple / osm / ca | Mean wait at the lights (s): simple / osm / ca | Cars through in 10 min |
|---|---|---|---|---|
| Balanced (1,150 an hour) | No signals | 33 / 32 / 37 | 0 | all 97 |
| | Fixed time | 83 / 80 / 94 | 44 / 46 / 53 | all 97 |
| | Adaptive | 63 / 53 / 65 | 24 / 21 / 26 | all 97 |
| Unbalanced (busy N–S, quiet E–W) | Fixed time | 164 / 161 / 177 | 140 / 72 / 97 | 127 / 121 / 115 of 139 |
| | Adaptive | 86 / 83 / 96 | 46 / 37 / 47 | all 139 |

The three traffic models agree closely, which is good evidence that the signal layer behaves consistently whichever model moves the vehicles:
- **Adaptive against fixed time:** adaptive roughly halves the wait.
- **Lopsided demand:** fixed time can't cope. Twenty seconds of green in a four-phase cycle is less than the busy arms need, so queues grow and some cars were still queuing after 10 minutes.

### Norwich: 108 signalised junctions, 1,000 trips, 15 ambulance calls

| Model | Control | Mean / worst journey (s) | Mean wait at lights per trip (s) | Crossings per junction-hour | Mean speed (of free flow) | Ambulance response, mean (s) | Runtime overhead |
|---|---|---|---|---|---|---|---|
| osm | No signals | 342 / 877 | 0 | – | 97% | 243 | – |
| osm | Fixed time | 446 / 1,105 | 91 | 118 | 90% | 290 | +35% |
| osm | Adaptive | 368 / 890 | 22 | 145 | 95% | 288 | +13% |
| osm | Adaptive + priority | 368 / 901 | 22 | 146 | 95% | 282 | +14% |
| ca | No signals | 350 / 872 | 0 | – | 94% | 242 | – |
| ca | Fixed time | 476 / 1,236 | 68 | 95 | 78% | 332 | +51% |
| ca | Adaptive | 388 / 918 | 17 | 133 | 89% | 289 | +28% |
| ca | Adaptive + priority | 389 / 939 | 16 | 134 | 89% | 291 | +27% |

All trips completed in every run.

- **Signals cost time, as they should.** No signals means every junction behaves like a free-for-all with no conflicts, which is the unrealistic baseline. Fixed time adds 30% (osm) to 36% (ca) to the mean journey; adaptive adds 8 to 11%.
- **Adaptive beats fixed time on every measure:** about a quarter of the waiting, 20 to 40% more vehicles through each junction per hour, a higher network speed and shorter worst journeys.
- **Ambulance response:** signals add about 45 s to the mean (243 s to 288 s in osm).
- **Emergency priority:** it activated 58 to 63 times per run, but its effect on response times is within the noise: −6 s in osm, +2 s in ca, and mixed from seed to seed. That's because an ambulance already crosses a red light after a 5 s caution stop, so priority can save only a few seconds per junction. The ambulance is also never held behind a queue: in `osm` and `simple` it moves at the road's speed, and in `ca` it isn't on the lattice. The extra 45 s comes mostly from the congestion the signals cause, which priority doesn't address. **The evidence does not show that priority helps in this model.** A model where emergency vehicles are blocked by queues would be needed to measure what priority really buys.
- **Runtime:** signals cost 13 to 51% more runtime. Fixed time costs more than adaptive because its longer queues mean more vehicles to check.

## Limitations

1. **Timings and layout are not Norwich's.** The real signal timings, phasing, detector placement and coordination aren't public. The locations come from OpenStreetMap; the phases are generated from road geometry.
2. **No turn-specific phases.** A phase serves whole approaches. Real junctions often run opposite approaches together with filtered right turns, or give right turns their own arrow. Generating approach-level phases is conservative (safe, but slower), so delays are probably overstated.
3. **No coordination ("green waves") between junctions.** Junctions closer together than a car length behave as separate signals, for example on dual carriageways that OSM maps as two junctions.
4. **Point vehicles in `simple` and `osm`.** The queue stands at the stop line, and its length in metres isn't modelled. It still slows the road through the existing congestion rules, and leaves at the saturation flow. Only `ca` has a physical queue.
5. **Amber is treated as red** for vehicles arriving, and there are no amber-gamblers or red-light runners.
6. **Emergency vehicles aren't blocked by queues** (see above), so the value of priority is understated.
7. **Pedestrians** (crossing phases, push buttons) aren't modelled.
8. **Studies keep only the standard metrics.** The signal totals for a study (waits, queues, crossings) aren't stored in the database; the standard metrics already include the signals' effect.
9. **Live runs:** switching signals on resets every junction to its first phase. The setting isn't saved with a run, so a restarted run starts without signals.

## Ready for integration?

**Yes, as an optional feature that is off by default.**
- **Tested:** signals stop vehicles and change journey times in all three traffic models; they never let conflicting movements go together; they're deterministic; and they're tested at rule, junction, model, scenario, API, dashboard and end-to-end level.
- **Off by default:** existing runs, studies and scenario fingerprints are unchanged.
- **What not to claim:** that fixed-time timings match Norwich, or that emergency priority shortens response times. The first would need real timing data; the second, emergency vehicles that can be held up by queues.
