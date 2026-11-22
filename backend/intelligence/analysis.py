"""Calculated analyses of Norwich and of live runs, for the AI City Analyst,
the scenario builder and the recommendations panel. Every number here is
computed from the road graph, the census weights, the optimiser or a live
simulation; nothing is predicted.

    coverage_gaps        where response from the existing stations is
                         slowest, and how many residents are beyond target
                         (planning.py's hexagons)
    recommend_station    the single site that would help most if added to
                         the existing stations (every candidate tried)
    critical_roads       which roads carry the most shortest-path trips, and
                         what closing each one costs those trips
    closure_impact       what closing named roads (or the central bridges)
                         does to trip times and to emergency coverage
    bottlenecks          a live run's most delayed roads now
    CongestionMonitor    roads whose traffic is far above their own recent
                         level in a live run (anomalies)
    run_report           a live run's figures, with an explanation built
                         only from those figures
    scenario_results     the latest saved scenario study's ranking

Place names come from PLACES: approximate centres of Norwich districts and
landmarks, used only to say roughly where something is.
"""

import math
import random
import threading
from functools import lru_cache

from backend.optimiser.planning import (
    CENTRAL_BRIDGES,
    _searches,
    existing_layout,
    junction_seconds,
    plan,
    summarise,
)
from backend.simulation.services import SERVICES
from backend.simulation.vehicle import straight_line_metres


# Approximate centres (latitude, longitude), for naming places only
PLACES = {
    "the city centre": (52.6286, 1.2925),
    "Norwich Castle": (52.6287, 1.2963),
    "Norwich Cathedral": (52.6320, 1.3010),
    "Norwich station": (52.6271, 1.3068),
    "Riverside": (52.6245, 1.3080),
    "Thorpe Hamlet": (52.6300, 1.3170),
    "Thorpe St Andrew": (52.6340, 1.3420),
    "Heartsease": (52.6400, 1.3300),
    "Mousehold Heath": (52.6390, 1.3150),
    "Sprowston": (52.6520, 1.3250),
    "Old Catton": (52.6550, 1.3050),
    "New Catton": (52.6430, 1.2950),
    "Mile Cross": (52.6450, 1.2780),
    "Hellesdon": (52.6500, 1.2550),
    "Heigham": (52.6300, 1.2780),
    "the Golden Triangle": (52.6230, 1.2750),
    "Earlham": (52.6280, 1.2500),
    "West Earlham": (52.6300, 1.2420),
    "Bowthorpe": (52.6370, 1.2180),
    "Costessey": (52.6500, 1.2150),
    "the University of East Anglia": (52.6219, 1.2392),
    "the Norfolk and Norwich University Hospital": (52.6178, 1.2197),
    "Eaton": (52.6100, 1.2650),
    "Cringleford": (52.6040, 1.2470),
    "Lakenham": (52.6130, 1.2950),
    "Tuckswood": (52.6060, 1.2950),
    "Bracondale": (52.6190, 1.3030),
    "Trowse": (52.6130, 1.3150),
}

CALCULATED = "calculated"


def plain(value):
    """numpy numbers (from the map and the optimiser) as plain Python ones,
    all the way down, so every answer encodes as JSON."""
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        return value.item()
    return value


def nearest_place(latitude, longitude):
    """(name, metres): the nearest place in PLACES."""
    name, point = min(PLACES.items(), key=lambda item: straight_line_metres((latitude, longitude), item[1]))
    return name, round(straight_line_metres((latitude, longitude), point))


def describe_point(latitude, longitude):
    name, metres = nearest_place(latitude, longitude)
    return name if metres < 700 else f"about {metres / 1000:.1f} km from {name}"


def _centre(polygon):
    corners = polygon[:-1]
    return (sum(p[1] for p in corners) / len(corners), sum(p[0] for p in corners) / len(corners))


def _service(name):
    if name not in SERVICES:
        raise ValueError(f"service must be one of {', '.join(SERVICES)}")
    return SERVICES[name]


# ------------------------------------------------------------
# Emergency coverage
# ------------------------------------------------------------

def coverage_gaps(graph, network, service="fire", target_minutes=None, disruption="none", top=5):
    """Where the existing stations respond slowest (by residents beyond the
    target), from the planning calculation. For ambulances, which have no
    mapped station, the optimiser's three standby sites are used instead
    (and the answer says so)."""

    settings = _service(service)
    target = (target_minutes * 60) if target_minutes else settings.target_seconds
    result = plan(graph, network, service, 3, target, disruption)
    existing = next((s for s in result["stages"] if s["label"] == "existing"), None)
    layout = "existing" if existing else "optimised standby sites"
    stage = existing or result["stages"][-1]
    cells = []
    for i, cell in enumerate(result["cells"]):
        beyond = stage["cells"]["beyond_people"][i]
        seconds = stage["cells"]["seconds"][i]
        if beyond <= 0 and seconds is not None:
            continue
        lat, lon = _centre(cell["polygon"])
        cells.append({
            "where": describe_point(lat, lon), "latitude": round(lat, 5), "longitude": round(lon, 5),
            "residents_beyond_target": round(beyond), "median_seconds": seconds,
        })
    cells.sort(key=lambda c: (-c["residents_beyond_target"], -(c["median_seconds"] or 1e9)))
    return {
        "provenance": CALCULATED,
        "method": result["model"],
        "service": service, "target_seconds": target, "disruption": disruption,
        "layout": layout,
        "stations": [s["name"] for s in result["existing"]] if existing else
                    [describe_point(s["latitude"], s["longitude"]) for s in result["optimised"]],
        "summary": stage["summary"],
        "worst_areas": cells[:top],
        "hexagons": len(result["cells"]),
    }


def recommend_station(graph, network, service="fire", target_minutes=None, disruption="none", top=3):
    """Every candidate site tried as one extra station beside the existing
    ones: the best by residents within the target, then mean response."""

    settings = _service(service)
    target = (target_minutes * 60) if target_minutes else settings.target_seconds
    blocked, candidates = _searches(graph, network, disruption)
    existing, _ = existing_layout(graph, blocked, service)
    _, undisrupted = _searches(graph, network, "none")
    nodes = sorted(set().union(*(s["distances"].keys() for s in undisrupted)), key=str)
    from backend.simulation.population import graph_junction_weights

    people = graph_junction_weights(graph, nodes)
    weights = [people.get(node, 0.0) for node in nodes]
    base_maps = [s["distances"] for s in existing]
    before = summarise(junction_seconds(base_maps, nodes, settings.speed), weights, target) if base_maps else None

    options = []
    for candidate in candidates:
        after = summarise(junction_seconds(base_maps + [candidate["distances"]], nodes, settings.speed), weights, target)
        data = graph.nodes[candidate["node"]]
        options.append({
            "where": describe_point(data["latitude"], data["longitude"]),
            "latitude": data["latitude"], "longitude": data["longitude"],
            "population_within_target_percent": after["population_within_target_percent"],
            "residents_beyond_target": after["population_beyond_target"],
            "mean_seconds": after["mean_seconds"],
        })
    options.sort(key=lambda o: (-o["population_within_target_percent"], o["mean_seconds"] or 1e9))
    for option in options:
        if before:
            option["gain_percent_points"] = round(option["population_within_target_percent"] - before["population_within_target_percent"], 2)
            option["residents_brought_within_target"] = round(before["population_beyond_target"] - option["residents_beyond_target"])
    return {
        "provenance": CALCULATED,
        "method": "each of the optimiser's candidate sites added in turn to the existing stations; "
                  "response = road distance from the nearest station / service speed, no traffic; "
                  "residents from Census 2021",
        "service": service, "target_seconds": target, "disruption": disruption,
        "existing_stations": [s["name"] for s in existing],
        "before": before,
        "candidates_tried": len(candidates),
        "best": options[:top],
    }


# ------------------------------------------------------------
# Road network vulnerability
# ------------------------------------------------------------

def _road_name(graph, a, b):
    try:
        return graph.road_name(a, b) or None
    except Exception:
        return None


def _sample_trips(graph, network, count, seed):
    """Trips between junctions drawn in proportion to where people live."""
    from backend.simulation.population import graph_junction_weights

    nodes = sorted(network.position, key=str)
    people = graph_junction_weights(graph, nodes)
    weights = [people.get(n, 0.0) + 1e-6 for n in nodes]
    rng = random.Random(seed)
    trips = []
    while len(trips) < count:
        a, b = rng.choices(nodes, weights, k=2)
        if a != b and straight_line_metres(network.position[a], network.position[b]) > 800:
            trips.append((a, b))
    return trips


def _trip_times(network, trips, blocked=None):
    out = []
    for a, b in trips:
        path, seconds, _ = network.fastest_path(a, b, 13.9, blocked_segments=blocked)
        out.append((path, seconds if path else None))
    return out


@lru_cache(maxsize=4)
def _critical(graph_id, graph, network, trips_count, seed, top):
    trips = _sample_trips(graph, network, trips_count, seed)
    base = _trip_times(network, trips)
    usage = {}
    for (path, _), trip in zip(base, trips):
        for a, b in zip(path, path[1:]):
            key = network.segment[(a, b)]
            usage.setdefault(key, {"trips": set(), "nodes": (a, b)})["trips"].add(trip)
    # One entry per named road (its busiest segment), so one long road does
    # not fill the list; unnamed roads count separately
    by_road = {}
    for segment, info in sorted(usage.items(), key=lambda kv: -len(kv[1]["trips"])):
        name = _road_name(graph, *info["nodes"]) or f"unnamed {segment}"
        by_road.setdefault(name, (segment, info))
    busiest = list(by_road.values())[:top]
    roads = []
    for segment, info in busiest:
        affected = [i for i, t in enumerate(trips) if t in info["trips"]]
        after = _trip_times(network, [trips[i] for i in affected], blocked={segment})
        extra = [s - base[i][1] for i, (_, s) in zip(affected, after) if s is not None]
        cut = sum(1 for _, s in after if s is None)
        a, b = info["nodes"]
        lat = (network.position[a][0] + network.position[b][0]) / 2
        lon = (network.position[a][1] + network.position[b][1]) / 2
        roads.append({
            "road": _road_name(graph, a, b) or "an unnamed road",
            "where": describe_point(lat, lon), "latitude": round(lat, 5), "longitude": round(lon, 5),
            "trips_using_it_percent": round(100 * len(affected) / len(trips), 1),
            "mean_extra_seconds_if_closed": round(sum(extra) / len(extra), 1) if extra else 0.0,
            "worst_extra_seconds_if_closed": round(max(extra), 1) if extra else 0.0,
            "trips_cut_off_if_closed": cut,
        })
    return trips_count, roads


def critical_roads(graph, network, trips=300, seed=7, top=8):
    """The roads most shortest-path trips use (trips drawn where people
    live, free-flow speeds) and what closing each one costs those trips."""
    count, roads = _critical(id(graph), graph, network, trips, seed, top)
    return {
        "provenance": CALCULATED,
        "method": f"{count} trips between junctions, drawn in proportion to Census 2021 residents; "
                  "fastest routes at free-flow speed; each road closed in turn and its trips re-planned",
        "trips_sampled": count,
        "roads": roads,
    }


def segments_named(network, graph, names):
    """Every road segment whose name contains one of `names` (case-insensitive)."""
    wanted = [n.lower() for n in names if n.strip()]
    found = set()
    for (a, b), segment in network.segment.items():
        name = (_road_name(graph, a, b) or "").lower()
        if name and any(w in name for w in wanted):
            found.add(segment)
    return found


def closure_impact(graph, network, roads=None, bridges=False, trips=300, seed=7):
    """What closing roads does: the change in trip times (the same sampled
    trips as critical_roads) and in fire coverage."""
    names = list(CENTRAL_BRIDGES) if bridges else list(roads or [])
    if not names:
        raise ValueError("name at least one road, or the central bridges")
    blocked = segments_named(network, graph, names)
    if not blocked:
        raise ValueError(f"no road on the map is called {', '.join(names)}")
    sample = _sample_trips(graph, network, trips, seed)
    before = _trip_times(network, sample)
    after = _trip_times(network, sample, blocked=blocked)
    changed = [(b[1], a[1]) for b, a in zip(before, after) if b[1] is not None]
    longer = [(a - b) for b, a in changed if a is not None and a - b > 0.5]
    cut = sum(1 for _, a in changed if a is None)
    result = {
        "provenance": CALCULATED,
        "method": f"{len(sample)} trips drawn in proportion to Census 2021 residents, fastest routes at "
                  "free-flow speed, before and after the closure",
        "roads_closed": sorted(names), "segments_closed": len(blocked),
        "trips_sampled": len(sample),
        "trips_longer_percent": round(100 * len(longer) / max(1, len(changed)), 1),
        "mean_extra_seconds_for_affected_trips": round(sum(longer) / len(longer), 1) if longer else 0.0,
        "worst_extra_seconds": round(max(longer), 1) if longer else 0.0,
        "trips_cut_off": cut,
    }
    if bridges:
        # At a 4-minute target: at fire's own 8 minutes every layout covers
        # every resident on this uncongested model, so nothing would show
        normal = plan(graph, network, "fire", 3, 240)
        closed = plan(graph, network, "fire", 3, 240, disruption="bridges")
        pick = lambda p: next(s for s in p["stages"] if s["label"] == "existing")["summary"]  # noqa: E731
        result["fire_coverage"] = {"target_seconds": 240, "before": pick(normal), "after": pick(closed)}
    return plain(result)


# ------------------------------------------------------------
# Live runs
# ------------------------------------------------------------

def _road_of(context, key):
    """Name and position of a traffic key (a pair of points)."""
    a, b = key[0], key[1]
    na, nb = context.node_by_point.get(tuple(a)), context.node_by_point.get(tuple(b))
    name = _road_name(context.graph, na, nb) if na is not None and nb is not None else None
    lat, lon = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    return name or "an unnamed road", describe_point(lat, lon), round(lat, 5), round(lon, 5)


def bottlenecks(run, context, top=8):
    """A live run's most delayed roads now: vehicles on the road times the
    speed they lose to traffic and incidents (the simulation's own factors)."""
    simulation = run.simulation
    traffic = simulation.get_traffic_density()
    rows = []
    for key, count in traffic.items():
        if count < 2:
            continue
        factor = simulation.road_speed_factor(key, count)
        loss = count * (1 - factor)
        if loss <= 0:
            continue
        name, where, lat, lon = _road_of(context, key)
        rows.append({"road": name, "where": where, "latitude": lat, "longitude": lon,
                     "vehicles": count, "speed_percent": round(100 * factor), "delay_score": round(loss, 2)})
    rows.sort(key=lambda r: -r["delay_score"])
    monitor = getattr(run, "monitor", None)
    return {
        "provenance": CALCULATED,
        "method": "vehicles on each road now x the share of free-flow speed they lose (the simulation's own speed factors)",
        "simulation_time": simulation.time,
        "roads_with_traffic": len(traffic),
        "bottlenecks": rows[:top],
        "anomalies": monitor.current(context) if monitor else [],
    }


class CongestionMonitor:
    """Roads whose traffic is far above their own recent level.

    Every `every` simulated seconds each road's vehicle count updates an
    exponentially weighted mean and variance (alpha); after `warmup`
    seconds a road is anomalous when its count is at least `minimum` and
    `z` standard deviations above its mean. A road never seen before counts
    as having had no traffic. Cheap: only roads with traffic are touched.
    """

    def __init__(self, alpha=0.05, z=3.0, minimum=6, warmup=120, every=5):
        self.alpha, self.z, self.minimum, self.warmup, self.every = alpha, z, minimum, warmup, every
        self.stats = {}          # key -> [mean, variance]
        self.flagged = {}        # key -> (time, count, expected)
        self.events = []         # [{time, key, count, expected}]
        self.started = None
        self.lock = threading.Lock()

    def observe(self, time, counts):
        if time % self.every:
            return
        with self.lock:
            if self.started is None:
                self.started = time
            ready = time - self.started >= self.warmup
            keys = set(counts) | set(self.stats)
            for key in keys:
                count = counts.get(key, 0)
                mean, variance = self.stats.get(key, (0.0, 0.0))
                spread = math.sqrt(variance) if variance > 0 else 1.0
                anomalous = ready and count >= self.minimum and (count - mean) / max(spread, 1.0) >= self.z
                if anomalous and key not in self.flagged:
                    self.flagged[key] = (time, count, mean)
                    self.events.append({"time": time, "key": key, "count": count, "expected": round(mean, 1)})
                    self.events = self.events[-200:]
                elif not anomalous and key in self.flagged and count < mean + spread:
                    del self.flagged[key]
                delta = count - mean
                mean += self.alpha * delta
                variance = (1 - self.alpha) * (variance + self.alpha * delta * delta)
                if count == 0 and mean < 0.05:
                    self.stats.pop(key, None)
                else:
                    self.stats[key] = (mean, variance)

    def current(self, context=None):
        with self.lock:
            rows = []
            for key, (time, count, expected) in self.flagged.items():
                row = {"since": time, "vehicles": count, "usual": round(expected, 1)}
                if context is not None:
                    name, where, lat, lon = _road_of(context, key)
                    row.update(road=name, where=where, latitude=lat, longitude=lon)
                rows.append(row)
            return sorted(rows, key=lambda r: -(r["vehicles"] - r["usual"]))


def run_report(run, context):
    """A live run's figures, and what they show, from those figures only."""
    simulation = run.simulation
    cars = [v for v in simulation.vehicles if v.vehicle_type == "normal"]
    driving = [v for v in cars if not v.finished]
    arrived = [v for v in cars if v.finished and v.journey_seconds]
    held = [v for v in driving if v.speed_factor < 1]
    stopped = [v for v in driving if v.speed_factor == 0]
    mean_speed = sum(v.speed_factor for v in driving) / len(driving) if driving else None
    journeys = [v.journey_seconds for v in arrived]
    emergencies = simulation.get_emergencies()
    answered = [e for e in emergencies if e.resolved]
    figures = {
        "simulation_time": simulation.time,
        "vehicles": len(cars), "driving": len(driving), "arrived": len(arrived),
        "held_up_percent": round(100 * len(held) / len(driving), 1) if driving else 0.0,
        "stopped": len(stopped),
        "mean_speed_percent": round(100 * mean_speed, 1) if mean_speed is not None else None,
        "mean_journey_seconds": round(sum(journeys) / len(journeys), 1) if journeys else None,
        "reroutes": sum(v.reroutes for v in cars),
        "closed_roads": len(simulation.get_blocked_segments()),
        "accidents": len(simulation.accidents),
        "floods": len(simulation.floods),
        "emergencies": len(emergencies),
        "emergencies_arrived": len(answered),
        "mean_response_seconds": round(sum(e.response_time for e in answered) / len(answered), 1) if answered else None,
        "traffic_model": simulation.road_model.name,
    }
    findings = []
    if driving and figures["held_up_percent"] >= 40:
        findings.append(f"{figures['held_up_percent']}% of the vehicles driving are held up by traffic, and they "
                        f"move at {figures['mean_speed_percent']}% of free-flow speed on average.")
    elif driving:
        findings.append(f"Traffic is flowing: {figures['held_up_percent']}% of vehicles are held up.")
    if figures["closed_roads"] or figures["accidents"] or figures["floods"]:
        findings.append(f"Disruption in place: {figures['closed_roads']} closed road(s), {figures['accidents']} "
                        f"accident(s), {figures['floods']} flood(s); vehicles have changed route {figures['reroutes']} times.")
    if figures["stopped"]:
        findings.append(f"{figures['stopped']} vehicles are stopped in queues.")
    worst = bottlenecks(run, context, top=3)["bottlenecks"]
    if worst:
        findings.append("The most delayed roads now: " + "; ".join(
            f"{r['road']} near {r['where']} ({r['vehicles']} vehicles at {r['speed_percent']}% speed)" for r in worst) + ".")
    if answered:
        findings.append(f"{len(answered)} emergency response(s) arrived, in {figures['mean_response_seconds']} s on average.")
    return {"provenance": CALCULATED, "figures": figures, "findings": findings, "bottlenecks": worst}


# ------------------------------------------------------------
# Saved scenario studies
# ------------------------------------------------------------

def scenario_results(context, study_id=None):
    """The ranking of a saved study (the newest completed one by default)."""
    if context.database is None:
        return {"provenance": CALCULATED, "available": False,
                "reason": "Scenario studies are saved in the database, and none is configured here"}
    from backend.api.routes.scenarios import get_study, list_studies

    if study_id is None:
        studies = list_studies(limit=20, offset=0, context=context)["studies"]
        done = [s for s in studies if s.get("status") == "completed"]
        if not done:
            return {"provenance": CALCULATED, "available": False, "reason": "No completed scenario study is saved yet"}
        study_id = done[0]["id"]
    study = get_study(study_id, metric=None, context=context)
    ranking = study.get("ranking") or []
    return {
        "provenance": CALCULATED, "available": True,
        "study": {"id": study["id"], "name": study.get("name"), "metric": study["ranking_metric"]},
        "ranking": ranking[:10],
        "explanation": study.get("explanation") or [],
    }

