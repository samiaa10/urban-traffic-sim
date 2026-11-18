"""
Experiments: the cellular-automaton traffic model (cellular.py)
----------------------------------------------------------------

Run from the project root:

    python -m backend.simulation.benchmark_cellular
    python -m backend.simulation.benchmark_cellular --save

--save writes docs/cellular_benchmark.md and its figures (docs/figures/).
Every experiment uses fixed seeds, so the numbers repeat exactly.

  1. Fundamental diagram: flow and speed against density on a ring road,
     the classic NaSch set-up (7.5 m cells, vmax 5), for three braking
     probabilities, against the known results.
  2. Stop-and-go waves: a space-time diagram, and how fast jams travel.
  3. A queue behind a blockage on an open road: how it grows, how long it
     takes to clear, and the flow out of it.
  4. Norwich: the osm model and the ca model on the same trips, three
     fleet sizes and three seeds, and the busiest roads closed.
  5. Speed against density on Norwich's roads: what the CA produces,
     next to the linear rule the osm model assumes.

Timings depend on the machine; the report records which one.
"""

import argparse
import datetime
import pickle
import platform
import statistics
import time
from pathlib import Path

from backend.routing.travel_time import RoadNetwork
from backend.scenarios.runner import World, run_scenario
from backend.simulation.cellular import CaParameters, OpenRoad, RingRoad
from backend.simulation.road_model import JAM_SPACING, MIN_ROAD_LENGTH, make_road_model
from backend.simulation.simulation import Simulation
from backend.simulation.traffic_manager import TrafficManager, car_speed


PROJECT_ROOT = Path(__file__).resolve().parents[2]
GRAPH_FILE = PROJECT_ROOT / "data" / "osm" / "norwich_graph.pkl"
REPORT = PROJECT_ROOT / "docs" / "cellular_benchmark.md"
FIGURES = PROJECT_ROOT / "docs" / "figures"

CLASSIC_CELL = 7.5   # metres: the classic NaSch cell (one car)
SEEDS = (1, 2, 3)


def per_hour(per_step):
    return per_step * 3600


def per_km(per_cell):
    return per_cell * 1000 / CLASSIC_CELL


def mean(values):
    values = [v for v in values if v is not None]
    return statistics.fmean(values) if values else None


def spread(values):
    values = [v for v in values if v is not None]
    return (min(values), max(values)) if values else (None, None)


# ----------------------------------------------------------------------
# 1. Fundamental diagram
# ----------------------------------------------------------------------

DENSITIES = (0.03, 0.06, 0.1, 0.13, 0.16, 0.2, 0.25, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8)


def ring_measure(cells, density, p, seed, warm=500, measure=500):
    cars = max(1, round(cells * density))
    ring = RingRoad(cells, cars, 5, p, seed)
    for _ in range(warm):
        ring.step()
    passed = ring.passed
    speed_sum = stopped = 0
    for _ in range(measure):
        ring.step()
        speed_sum += sum(ring.speed)
        stopped += sum(1 for v in ring.speed if v == 0)
    return {
        "flow": (ring.passed - passed) / measure,
        "speed": speed_sum / (measure * cars),
        "stopped": stopped / (measure * cars),
    }


def part1_fundamental(cells=500):
    rows = []
    for p in (0.0, 0.25, 0.5):
        for density in DENSITIES:
            runs = [ring_measure(cells, density, p, seed) for seed in SEEDS]
            rows.append({
                "p": p,
                "density": density,
                "flow": mean(r["flow"] for r in runs),
                "flow_range": spread([r["flow"] for r in runs]),
                "speed": mean(r["speed"] for r in runs),
                "stopped": mean(r["stopped"] for r in runs),
            })
    return rows


# ----------------------------------------------------------------------
# 2. Stop-and-go waves
# ----------------------------------------------------------------------

def stopped_profile(ring):
    profile = [0] * ring.cells
    for x, v in zip(ring.position, ring.speed):
        if v == 0:
            profile[x] = 1
    return profile


def wave_speed(history, lag=10, max_shift=20):
    """Cells per step a pattern of stopped cars moves: the shift that best
    lines up each row of stopped cells with the row `lag` steps later
    (on a ring, so shifts wrap round)."""

    cells = len(history[0])
    best_shift, best_score = 0, -1
    for shift in range(-max_shift, max_shift + 1):
        score = 0
        for t in range(len(history) - lag):
            a, b = history[t], history[t + lag]
            score += sum(a[x] * b[(x + shift) % cells] for x in range(cells) if a[x])
        if score > best_score:
            best_shift, best_score = shift, score
    return best_shift / lag


def part2_waves(cells=300, density=0.2, steps=300):
    results = []
    diagram = None
    for p in (0.0, 0.25, 0.5):
        speeds, stopped = [], []
        for seed in SEEDS:
            ring = RingRoad(cells, round(cells * density), 5, p, seed)
            for _ in range(300):
                ring.step()
            history, rows = [], []
            for _ in range(steps):
                ring.step()
                history.append(stopped_profile(ring))
                rows.append(ring.occupancy())
            share = sum(map(sum, history)) / (steps * len(ring.position))
            stopped.append(share)
            speeds.append(wave_speed(history) if share > 0 else None)
            if p == 0.5 and seed == SEEDS[0]:
                diagram = rows
        results.append({
            "p": p,
            "stopped": mean(stopped),
            "wave_cells_per_step": mean(speeds),
            "wave_range": spread(speeds),
        })
    return results, diagram


# ----------------------------------------------------------------------
# 3. A queue behind a blockage
# ----------------------------------------------------------------------

def part3_blockage(cells=400, inflow=0.25, p=0.25, block_at=300,
                   start=300, end=600, until=1500):
    runs = []
    for seed in SEEDS:
        road = OpenRoad(cells, inflow, 5, p, seed)
        queue, left = [], []
        for t in range(until):
            road.blocked_at = block_at if start <= t < end else None
            before = road.left
            road.step()
            queue.append(road.queue())
            left.append(road.left - before)
        runs.append((queue, left))

    def per_run(queue, left):
        peak = max(queue[start:end + 60])
        # Growth while blocked: cars joining per minute, over the last
        # five minutes of the blockage (after the first cars reach it)
        growth = (queue[end - 1] - queue[end - 301]) / 5
        # Cleared: back to the queue there was before the blockage
        before = max(queue[start - 60:start])
        cleared = next((t for t in range(end, len(queue)) if queue[t] <= before + 2), None)
        # Flow out of the queue for the first two minutes after reopening
        # (cars leaving the end of the road, once the first has got there)
        first = next((t for t in range(end, len(left)) if left[t]), None)
        discharge = sum(left[first:first + 120]) / 120 if first else None
        return peak, growth, None if cleared is None else cleared - end, discharge

    figures = [per_run(*run) for run in runs]
    return {
        "inflow_per_minute": inflow * 60,
        "peak_queue": mean(f[0] for f in figures),
        "growth_per_minute": mean(f[1] for f in figures),
        "clear_seconds": mean(f[2] for f in figures),
        "clear_range": spread([f[2] for f in figures]),
        "discharge_per_hour": per_hour(mean(f[3] for f in figures)),
        "series": runs[0][0],
        "start": start,
        "end": end,
    }


# ----------------------------------------------------------------------
# 4. Norwich: osm against ca
# ----------------------------------------------------------------------

def scenario_figures(result):
    summary = result["summary"]
    series = result["timeseries"]
    weighted = [
        (row["mean_speed_factor"], row["active_vehicles"]) for row in series
        if row["mean_speed_factor"] is not None and row["active_vehicles"]
    ]
    total = sum(w for _, w in weighted)
    return {
        "finished": summary["trips_finished"],
        "spawned": summary["trips_spawned"],
        "completion": summary["completion_percent"],
        "mean_delay": summary["mean_delay_seconds"],
        "p95_delay": summary["p95_delay_seconds"],
        "mean_increase": summary["mean_increase_seconds"],
        "travel_hours": summary["total_travel_vehicle_hours"],
        "reroutes": summary["reroutes"],
        "simulated": summary["simulated_seconds"],
        "speed_factor": sum(f * w for f, w in weighted) / total if total else None,
        "wall": result["execution"]["wall_seconds"],
        "per_second_ms": 1000 * result["execution"]["wall_seconds"] / max(1, summary["simulated_seconds"]),
    }


def spec(vehicles, seed, model, closures=()):
    return {
        "name": f"{model} {vehicles}", "seed": seed,
        "demand": {"vehicles": vehicles, "spread_seconds": 300},
        "traffic_model": model,
        "closures": [{"start": a, "destination": b} for a, b in closures],
    }


def part4_norwich(world, fleets):
    rows = []
    for vehicles in fleets:
        for model in ("osm", "ca"):
            runs = []
            for seed in SEEDS:
                runs.append(scenario_figures(run_scenario(world, spec(vehicles, seed, model))))
                print(f"   {vehicles} {model} seed {seed}: {runs[-1]['wall']:.1f} s", flush=True)
            rows.append({"vehicles": vehicles, "model": model, "runs": runs})
    return rows


def busiest_roads(world, count=3, vehicles=1000):
    result = run_scenario(world, spec(vehicles, SEEDS[0], "osm"))
    roads = []
    for road in result["roads"]:
        pair = (road["start_node"], road["end_node"])
        if None not in pair and pair not in roads and pair[::-1] not in roads:
            roads.append(pair)
        if len(roads) == count:
            break
    return roads


def part4_closures(world, vehicles=1000):
    roads = busiest_roads(world, vehicles=vehicles)
    rows = []
    for model in ("osm", "ca"):
        pairs = []
        for seed in SEEDS:
            base = scenario_figures(run_scenario(world, spec(vehicles, seed, model)))
            closed = scenario_figures(run_scenario(world, spec(vehicles, seed, model, roads)))
            pairs.append((base, closed))
            print(f"   closure {model} seed {seed}", flush=True)
        rows.append({"model": model, "pairs": pairs})
    names = [world.graph.road_name(a, b) or f"{a}-{b}" for a, b in roads]
    return rows, names


def repeatable(world):
    a = run_scenario(world, spec(500, 7, "ca"))
    b = run_scenario(world, spec(500, 7, "ca"))
    return a["trips"]["journey_seconds"] == b["trips"]["journey_seconds"]


# ----------------------------------------------------------------------
# 5. Speed against density on the network
# ----------------------------------------------------------------------

BINS = 10


def part5_speed_density(graph, vehicles=2000, seconds=900, every=10):
    network = RoadNetwork(graph)
    network.use_landmarks(8)
    curves = {}

    for model_name in ("osm", "ca"):
        model = make_road_model(model_name, network, seed=1)
        parked = network.point(next(iter(graph.nodes)))
        simulation = Simulation([parked, parked], road_model=model)
        simulation.vehicles[0].finished = True
        simulation.use_road_lengths(network.length_between_points)
        manager = TrafficManager(network, simulation, seed=1)
        manager.spawn_vehicles(vehicles, speed=car_speed(simulation))
        osm = network.osm_roads()

        sums = [[0.0, 0] for _ in range(BINS)]
        for second in range(seconds):
            manager.step()
            if second % every:
                continue
            # Roads shorter than MIN_ROAD_LENGTH are left out: one car on a
            # road shorter than itself would count as a full road
            if model_name == "ca":
                samples = [
                    (state["occupancy"], state["speed_factor"])
                    for key, state in model.road_state().items()
                    if model.engine.roads[key].length >= MIN_ROAD_LENGTH
                ]
            else:
                samples = []
                for key, count in simulation.get_traffic_density().items():
                    length = network.length_between_points.get(key)
                    if not length or length < MIN_ROAD_LENGTH:
                        continue
                    lanes = osm.lanes.get(key, 1)
                    occupancy = min(1.0, count * JAM_SPACING / (length * lanes))
                    samples.append((occupancy, simulation.road_speed_factor(key, count)))
            for occupancy, factor in samples:
                index = min(BINS - 1, int(occupancy * BINS))
                sums[index][0] += factor
                sums[index][1] += 1
        curves[model_name] = [
            (index, total / count if count else None, count)
            for index, (total, count) in enumerate(sums)
        ]
    return curves


# ----------------------------------------------------------------------
# Figures (plain SVG, no plotting library)
# ----------------------------------------------------------------------

STYLE = (
    "<style>text{font:12px system-ui,sans-serif;fill:#4a5b6e}"
    ".axis{stroke:#98a6b5;stroke-width:1}.grid{stroke:#e8ecf0;stroke-width:1}"
    ".t{font-weight:600;fill:#17283a}</style>"
)
SERIES = ("#2a5d8f", "#a86d17", "#b4423a")


def line_chart(title, x_label, y_label, series, x_max, y_max, width=640, height=360):
    """series: [(label, colour, [(x, y), ...], dashed)]."""
    left, right, top, bottom = 56, 16, 34, 46
    w, h = width - left - right, height - top - bottom
    sx = lambda x: left + w * x / x_max
    sy = lambda y: top + h - h * y / y_max
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
           f'role="img" aria-label="{title}">{STYLE}',
           f'<rect width="{width}" height="{height}" fill="#fff"/>',
           f'<text class="t" x="{left}" y="20">{title}</text>']
    for i in range(6):
        y = y_max * i / 5
        out.append(f'<line class="grid" x1="{left}" x2="{left + w}" y1="{sy(y):.1f}" y2="{sy(y):.1f}"/>')
        out.append(f'<text x="{left - 6}" y="{sy(y) + 4:.1f}" text-anchor="end">{y:g}</text>')
        x = x_max * i / 5
        out.append(f'<text x="{sx(x):.1f}" y="{top + h + 16}" text-anchor="middle">{x:g}</text>')
    out.append(f'<line class="axis" x1="{left}" x2="{left + w}" y1="{top + h}" y2="{top + h}"/>')
    out.append(f'<line class="axis" x1="{left}" x2="{left}" y1="{top}" y2="{top + h}"/>')
    out.append(f'<text x="{left + w / 2}" y="{height - 8}" text-anchor="middle">{x_label}</text>')
    out.append(f'<text transform="translate(14 {top + h / 2}) rotate(-90)" text-anchor="middle">{y_label}</text>')
    for number, (label, colour, points, dashed) in enumerate(series):
        points = [(x, y) for x, y in points if y is not None]
        path = " ".join(f"{'M' if i == 0 else 'L'}{sx(x):.1f},{sy(y):.1f}" for i, (x, y) in enumerate(points))
        dash = ' stroke-dasharray="5 4"' if dashed else ""
        out.append(f'<path d="{path}" fill="none" stroke="{colour}" stroke-width="2"{dash}/>')
        lx = left + w - 190
        ly = top + 14 + 18 * number
        out.append(f'<line x1="{lx}" x2="{lx + 22}" y1="{ly - 4}" y2="{ly - 4}" stroke="{colour}" stroke-width="2"{dash}/>')
        out.append(f'<text x="{lx + 28}" y="{ly}">{label}</text>')
    out.append("</svg>")
    return "\n".join(out)


def space_time_svg(rows, vmax=5, scale=2):
    """Space across, time down; each car a dot coloured by its speed."""
    cells, steps = len(rows[0]), len(rows)
    width, height = cells * scale + 20, steps * scale + 50
    colours = {0: "#b4423a", 1: "#d0611f", 2: "#a86d17"}
    paths = {}
    for t, row in enumerate(rows):
        for x, v in enumerate(row):
            if v is None:
                continue
            colour = colours.get(v, "#2a5d8f")
            paths.setdefault(colour, []).append(f"M{10 + x * scale},{40 + t * scale}h{scale}v{scale}h-{scale}z")
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" '
           f'aria-label="Space-time diagram of a NaSch ring road">{STYLE}',
           f'<rect width="{width}" height="{height}" fill="#fff"/>',
           '<text class="t" x="10" y="16">Ring road, density 0.2, p = 0.5: space across, time down</text>',
           '<text x="10" y="32">red: stopped; orange/amber: slow; blue: moving freely. '
           'Jams drift left (backwards) as time goes on.</text>']
    for colour, parts in paths.items():
        out.append(f'<path fill="{colour}" d="{"".join(parts)}"/>')
    out.append("</svg>")
    return "\n".join(out)


# ----------------------------------------------------------------------
# Report
# ----------------------------------------------------------------------

def fmt(value, digits=1):
    return "n/a" if value is None else f"{value:,.{digits}f}"


def write_report(machine, fundamental, waves, blockage, norwich, closures, closure_names,
                 curves, same, total_seconds):
    FIGURES.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Cellular automaton: experiments",
        "",
        f"Generated by `python -m backend.simulation.benchmark_cellular --save` on {machine}"
        f" ({datetime.date.today().isoformat()}), {total_seconds / 60:.0f} minutes in all."
        " Every experiment uses seeds 1, 2 and 3; figures are their mean unless a range is given."
        " See [CELLULAR_AUTOMATON.md](CELLULAR_AUTOMATON.md) for the model and what these results mean.",
        "",
        "## 1. Fundamental diagram (ring road, classic NaSch)",
        "",
        "500 cells of 7.5 m (3.75 km), vmax 5 cells per second (37.5 m/s), cars evenly spaced at the"
        " start, 500 s to settle, then 500 s measured. Flow is cars passing a point; per lane.",
        "",
        "| p | Density (cars/km) | Flow (cars/h) | Mean speed (km/h) | Cars stopped |",
        "|---|---|---|---|---|",
    ]
    for row in fundamental:
        lines.append(
            f"| {row['p']:g} | {per_km(row['density']):.0f} | {per_hour(row['flow']):,.0f} "
            f"| {row['speed'] * CLASSIC_CELL * 3.6:.1f} | {100 * row['stopped']:.0f}% |"
        )
    lines.append("")
    for p in (0.0, 0.25, 0.5):
        best = max((r for r in fundamental if r["p"] == p), key=lambda r: r["flow"])
        lines.append(
            f"- p = {p:g}: maximum flow {per_hour(best['flow']):,.0f} cars/h at "
            f"{per_km(best['density']):.0f} cars/km."
        )
    lines += ["", "![Flow against density](figures/cellular_fundamental.svg)", ""]

    lines += [
        "## 2. Stop-and-go waves",
        "",
        "Ring road of 300 cells at density 0.2 (27 cars/km), 300 s settling, then 300 s recorded."
        " Wave speed: the shift that best lines up the stopped cars with where they are 10 s later.",
        "",
        "| p | Cars stopped | Jam movement (km/h) | Range over seeds (km/h) |",
        "|---|---|---|---|",
    ]
    for row in waves:
        speed = row["wave_cells_per_step"]
        low, high = row["wave_range"]
        to_kmh = lambda v: None if v is None else v * CLASSIC_CELL * 3.6
        lines.append(
            f"| {row['p']:g} | {100 * row['stopped']:.1f}% | {fmt(to_kmh(speed))} "
            f"| {fmt(to_kmh(low))} to {fmt(to_kmh(high))} |"
        )
    lines += ["", "![Space-time diagram](figures/cellular_spacetime.svg)", ""]

    lines += [
        "## 3. A queue behind a blockage",
        "",
        f"A 3 km single-lane road (400 cells), cars arriving at the start {blockage['inflow_per_minute']:.0f}"
        " a minute on average, p = 0.25. The road is blocked 750 m from its end from 300 s to 600 s.",
        "",
        "| Measure | Result |",
        "|---|---|",
        f"| Longest queue (cars stopped) | {blockage['peak_queue']:.0f} |",
        f"| Queue growth in the last 5 minutes of the blockage | {blockage['growth_per_minute']:.1f} cars/min"
        f" (arrivals: {blockage['inflow_per_minute']:.0f} cars/min) |",
        f"| Time to clear after reopening | {blockage['clear_seconds']:.0f} s"
        f" ({blockage['clear_range'][0]}-{blockage['clear_range'][1]} s) |",
        f"| Flow out of the queue (first 2 minutes) | {blockage['discharge_per_hour']:,.0f} cars/h |",
        "",
        "![Queue length over time](figures/cellular_queue.svg)",
        "",
    ]

    lines += [
        "## 4. Norwich: osm against ca",
        "",
        "The scenario engine (`backend/scenarios/runner.py`), trips leaving over the first 5 minutes,"
        " dynamic rerouting on. The same seed gives the same trips in both models. Delay is measured"
        " against each model's own free flow (osm: the speed limit; ca: the limit rounded to whole"
        " cells per second).",
        "",
        "| Trips | Model | Completed | Mean delay (s) | 95th pct delay (s) | Mean journey over"
        " free flow at the limits (s) | Mean speed (of free flow) | Route changes | Simulated (s)"
        " | Runtime (s) | ms per simulated s |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in norwich:
        runs = row["runs"]
        lines.append(
            f"| {row['vehicles']:,} | {row['model']} | {mean(r['completion'] for r in runs):.1f}% "
            f"| {fmt(mean(r['mean_delay'] for r in runs))} | {fmt(mean(r['p95_delay'] for r in runs))} "
            f"| {fmt(mean(r['mean_increase'] for r in runs))} "
            f"| {100 * mean(r['speed_factor'] for r in runs):.0f}% "
            f"| {mean(r['reroutes'] for r in runs):.0f} | {mean(r['simulated'] for r in runs):,.0f} "
            f"| {mean(r['wall'] for r in runs):.1f} | {mean(r['per_second_ms'] for r in runs):.1f} |"
        )
    lines += [
        "",
        f"### The busiest roads closed ({', '.join(closure_names)})",
        "",
        "1,000 trips; the three roads that lost the most time in the osm baseline (seed 1),"
        " closed for the whole run.",
        "",
        "| Model | Mean delay, open (s) | Mean delay, closed (s) | Change | Route changes, closed"
        " | Completed, closed |",
        "|---|---|---|---|---|---|",
    ]
    for row in closures:
        base = mean(b["mean_delay"] for b, _ in row["pairs"])
        closed = mean(c["mean_delay"] for _, c in row["pairs"])
        lines.append(
            f"| {row['model']} | {fmt(base)} | {fmt(closed)} | {fmt(closed - base)} "
            f"| {mean(c['reroutes'] for _, c in row['pairs']):.0f} "
            f"| {mean(c['completion'] for _, c in row['pairs']):.1f}% |"
        )
    lines += [
        "",
        f"Repeatable: the same ca scenario run twice gave {'identical' if same else 'DIFFERENT'} journey times.",
        "",
        "## 5. Speed against density on Norwich's roads",
        "",
        "2,000 trips, 15 simulated minutes, every road at least 30 m long with cars on it sampled"
        " every 10 s. Density is the share of the road's length (all lanes) its cars take at 7.5 m"
        " each. osm's speed is its rule (Greenshields' line over a road at least 30 m long, with a"
        " 5% floor); ca's is the cars' measured mean speed.",
        "",
        "| Density | osm: mean speed (samples) | ca: mean speed (samples) |",
        "|---|---|---|",
    ]
    for index in range(BINS):
        _, osm_speed, osm_count = curves["osm"][index]
        _, ca_speed, ca_count = curves["ca"][index]
        if not osm_count and not ca_count:
            continue
        cell = lambda speed, count: "-" if not count else f"{100 * speed:.0f}% ({count:,})"
        lines.append(
            f"| {index * 10}-{index * 10 + 10}% | {cell(osm_speed, osm_count)} | {cell(ca_speed, ca_count)} |"
        )
    lines += ["", "![Speed against density on Norwich's roads](figures/cellular_speed_density.svg)", ""]
    REPORT.write_text("\n".join(lines) + "\n")

    (FIGURES / "cellular_fundamental.svg").write_text(line_chart(
        "Flow against density (ring road, vmax 5)", "density (cars/km)", "flow (cars/h)",
        [(f"p = {p:g}", SERIES[i], [
            (per_km(r["density"]), per_hour(r["flow"])) for r in fundamental if r["p"] == p
        ], False) for i, p in enumerate((0.0, 0.25, 0.5))],
        x_max=120, y_max=3000,
    ))
    (FIGURES / "cellular_queue.svg").write_text(line_chart(
        f"Cars stopped behind a blockage ({blockage['start']}-{blockage['end']} s), seed 1",
        "time (s)", "cars stopped",
        [("queue", SERIES[2], list(enumerate(blockage["series"])), False)],
        x_max=len(blockage["series"]), y_max=max(10, 10 * (max(blockage["series"]) // 10 + 1)),
    ))
    (FIGURES / "cellular_speed_density.svg").write_text(line_chart(
        "Mean speed against density on Norwich's roads (2,000 trips)",
        "density (% of road length taken)", "speed (% of free flow)",
        [
            ("osm (its assumed rule)", SERIES[0], [
                (i * 10 + 5, 100 * s) for i, s, c in curves["osm"] if c
            ], True),
            ("ca (emerges from the rules)", SERIES[2], [
                (i * 10 + 5, 100 * s) for i, s, c in curves["ca"] if c
            ], False),
        ],
        x_max=100, y_max=100,
    ))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--save", action="store_true")
    parser.add_argument("--fleets", default="500,1000,2000")
    arguments = parser.parse_args()

    started = time.perf_counter()
    machine = f"{platform.machine()} / Python {platform.python_version()}"

    print("1. fundamental diagram", flush=True)
    fundamental = part1_fundamental()
    print("2. stop-and-go waves", flush=True)
    waves, diagram = part2_waves()
    print("3. blockage", flush=True)
    blockage = part3_blockage()

    with open(GRAPH_FILE, "rb") as file:
        graph = pickle.load(file)
    world = World(graph)

    print("4. Norwich", flush=True)
    norwich = part4_norwich(world, [int(n) for n in arguments.fleets.split(",")])
    closures, closure_names = part4_closures(world)
    same = repeatable(world)
    print("5. speed against density", flush=True)
    curves = part5_speed_density(graph)

    total = time.perf_counter() - started
    print(f"done in {total / 60:.1f} min", flush=True)

    if arguments.save:
        FIGURES.mkdir(parents=True, exist_ok=True)
        (FIGURES / "cellular_spacetime.svg").write_text(space_time_svg(diagram))
        write_report(machine, fundamental, waves, blockage, norwich, closures, closure_names,
                     curves, same, total)
        print(f"wrote {REPORT.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
