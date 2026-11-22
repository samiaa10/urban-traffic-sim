"""Emergency infrastructure planning: where response is fast or slow, on
the map, for existing and optimised station layouts.

This adds geography to the existing station optimiser (station_optimiser.py)
without changing its model or its answers:

  * RESPONSE TIME is the optimiser's own: the shortest ROAD distance from
    the nearest station (Dijkstra over the Norwich road graph, around any
    closed roads) divided by the service's speed (services.py). Not the
    straight-line distance, and not traffic: the same simplification the
    optimiser ranks stations by, and the same caveats.
  * It is worked out for EVERY junction of the map (about 5,000), not only
    the optimiser's 200 sampled emergencies.
  * The optimised layout is the optimiser's answer for the same settings
    (best_station_set: every possible set where feasible), in the order the
    stations add value (order_by_contribution).
  * The existing layout is OpenStreetMap's real stations for the service
    (existing_stations.py); there are none for road ambulances in Norwich.

GEOGRAPHY: junctions are grouped into hexagons HEX_METRES across (centre to
corner), on a flat projection around the city centre. A hexagon's value is
the MEDIAN response time of the junctions inside it, plus the share of them
reached within the target and the residents living by them (Census 2021
output areas spread over junctions, simulation/population.py). Hexagons with
no junction are not drawn: nothing is interpolated into places with no road.

The optimisation trace is the algorithm's real steps: every candidate's own
score, how many sets were compared, and each station of the chosen set
added in order with the set's score after it.
"""

import math
import statistics
from backend.optimiser import station_optimiser as optimiser
from backend.simulation.services import service as service_of


HEX_METRES = 180.0

# Disruptions the planner can apply (the same ones as the resilience
# experiments: scenarios/resilience.py, benchmark_resilience.py)
FLOOD_AREA = {"latitude": 52.6305, "longitude": 1.3045, "radius_metres": 350}
CENTRAL_BRIDGES = ["Prince of Wales Road", "Whitefriars", "Fye Bridge Street", "Duke Street"]
DISRUPTIONS = ("none", "flood", "bridges")

# An existing station further than this from any junction is outside the
# map and left out (Norwich's snap within 130 m)
MAX_SNAP_METRES = 500.0


# ----------------------------------------------------------------------
# Hexagons
# ----------------------------------------------------------------------

class HexGrid:
    """Pointy-top hexagons on a flat projection around (lat0, lon0)."""

    def __init__(self, lat0, lon0, size=HEX_METRES):
        self.lat0, self.lon0, self.size = lat0, lon0, size
        self.kx = 111_320 * math.cos(math.radians(lat0))
        self.ky = 111_320

    def to_xy(self, lat, lon):
        return (lon - self.lon0) * self.kx, (lat - self.lat0) * self.ky

    def to_latlon(self, x, y):
        return self.lat0 + y / self.ky, self.lon0 + x / self.kx

    def cell(self, lat, lon):
        """Axial (q, r) of the hexagon a point falls in."""
        x, y = self.to_xy(lat, lon)
        q = (math.sqrt(3) / 3 * x - y / 3) / self.size
        r = (2 / 3 * y) / self.size
        # Round in cube coordinates
        cx, cz = q, r
        cy = -cx - cz
        rx, ry, rz = round(cx), round(cy), round(cz)
        dx, dy, dz = abs(rx - cx), abs(ry - cy), abs(rz - cz)
        if dx > dy and dx > dz:
            rx = -ry - rz
        elif dy > dz:
            ry = -rx - rz
        else:
            rz = -rx - ry
        return int(rx), int(rz)

    def centre(self, q, r):
        x = self.size * math.sqrt(3) * (q + r / 2)
        y = self.size * 1.5 * r
        return self.to_latlon(x, y)

    def polygon(self, q, r):
        """[lon, lat] corners, closed (GeoJSON order)."""
        cx = self.size * math.sqrt(3) * (q + r / 2)
        cy = self.size * 1.5 * r
        ring = []
        for i in range(6):
            angle = math.radians(60 * i - 30)
            lat, lon = self.to_latlon(cx + self.size * math.cos(angle), cy + self.size * math.sin(angle))
            ring.append([round(lon, 6), round(lat, 6)])
        return ring + [ring[0]]


def build_cells(graph, nodes, size=HEX_METRES):
    """(grid, cells): cells is a list of {"key", "nodes"} in a fixed order,
    every node in exactly one cell."""
    lat0 = statistics.fmean(graph.nodes[n]["latitude"] for n in nodes)
    lon0 = statistics.fmean(graph.nodes[n]["longitude"] for n in nodes)
    grid = HexGrid(lat0, lon0, size)
    groups = {}
    for node in nodes:
        data = graph.nodes[node]
        groups.setdefault(grid.cell(data["latitude"], data["longitude"]), []).append(node)
    cells = [{"key": key, "nodes": groups[key]} for key in sorted(groups)]
    return grid, cells


# ----------------------------------------------------------------------
# Response times
# ----------------------------------------------------------------------

def junction_seconds(distance_maps, nodes, speed):
    """Each node's response time (s) from the nearest of the stations whose
    distance maps are given; inf where none can reach it."""
    times = []
    for node in nodes:
        best = min((m.get(node, math.inf) for m in distance_maps), default=math.inf)
        times.append(best / speed if best != math.inf else math.inf)
    return times


def summarise(times, weights, target):
    """Coverage over junctions and residents."""
    reached = [t for t in times if t != math.inf]
    total_people = sum(weights) or 1.0
    within_people = sum(w for t, w in zip(times, weights) if t <= target)
    beyond_people = sum(w for t, w in zip(times, weights) if t > target)
    return {
        "junctions": len(times),
        "unreachable_junctions": len(times) - len(reached),
        "mean_seconds": statistics.fmean(reached) if reached else None,
        "median_seconds": statistics.median(reached) if reached else None,
        "p90_seconds": sorted(reached)[int(0.9 * (len(reached) - 1))] if reached else None,
        "worst_seconds": max(reached) if reached else None,
        "junctions_within_target_percent": 100 * sum(1 for t in times if t <= target) / len(times),
        "population_within_target_percent": 100 * within_people / total_people,
        "population_beyond_target": round(beyond_people),
    }


def cell_values(cells, index, times, weights, target):
    """Per cell: median seconds (None: nothing reachable), share within the
    target, residents beyond it."""
    medians, within, beyond = [], [], []
    for cell in cells:
        values = [times[index[n]] for n in cell["nodes"]]
        reached = [t for t in values if t != math.inf]
        medians.append(round(statistics.median(reached), 1) if reached else None)
        within.append(round(sum(1 for t in values if t <= target) / len(values), 3))
        beyond.append(round(sum(weights[index[n]] for n in cell["nodes"] if times[index[n]] > target), 1))
    return {"seconds": medians, "within": within, "beyond_people": beyond}


# ----------------------------------------------------------------------
# Disruptions, candidates and searches (cached per map and disruption)
# ----------------------------------------------------------------------

def disruption_segments(network, disruption):
    if disruption == "none":
        return frozenset()
    if disruption == "flood":
        from backend.simulation.disruptions import segments_in_area

        return frozenset(segments_in_area(network, **FLOOD_AREA))
    if disruption == "bridges":
        from backend.simulation.infrastructure import load_bridges

        position = network.position
        return frozenset(
            tuple(sorted((position[a], position[b])))
            for a, b, name in load_bridges()
            if a in position and b in position and name in CENTRAL_BRIDGES
        )
    raise ValueError(f"disruption must be one of {', '.join(DISRUPTIONS)}")


_SEARCHES = {}


def _searches(graph, network, disruption):
    """Candidate stations and their road-distance maps for a disruption.
    Distances do not depend on the service (speed divides later), so one
    search per candidate serves every service and target."""
    key = (id(graph), disruption)
    found = _SEARCHES.get(key)
    if found is None or found[0] is not graph:
        blocked = set(disruption_segments(network, disruption))
        candidates = optimiser.dense_candidates(graph)
        stations = optimiser.prepare_stations(graph, candidates, blocked or None, workers=1)
        found = _SEARCHES[key] = (graph, blocked, stations)
        if len(_SEARCHES) > 8:
            _SEARCHES.pop(next(iter(_SEARCHES)))
    return found[1], found[2]


def existing_layout(graph, blocked, service, stations=None):
    """The service's existing stations (default: OpenStreetMap's), snapped
    to the road graph as the optimiser snaps candidates, with their
    road-distance maps. Returns (kept, left_out)."""
    from backend.simulation.vehicle import straight_line_metres

    if stations is None:
        from backend.optimiser.existing_stations import load

        stations = load()
    layout, left_out = [], []
    for station in stations:
        if station["service"] != service:
            continue
        node = optimiser.snap_to_road(graph, station["latitude"], station["longitude"])
        data = graph.nodes[node]
        snapped = (data["latitude"], data["longitude"])
        gap = straight_line_metres((station["latitude"], station["longitude"]), snapped)
        if gap > MAX_SNAP_METRES:
            left_out.append({"name": station["name"], "reason": f"{gap:.0f} m from the nearest junction"})
            continue
        layout.append({
            **station,
            "node": node,
            "snap_metres": round(gap),
            "snapped": {"latitude": snapped[0], "longitude": snapped[1]},
            "distances": optimiser.shortest_distances(graph, node, blocked or None),
        })
    return layout, left_out


# ----------------------------------------------------------------------
# The plan
# ----------------------------------------------------------------------

def plan(graph, network, service="fire", count=3, target_seconds=None, disruption="none",
         seed=1, scenarios=200, distribution="population", hex_metres=HEX_METRES,
         existing_stations=None):
    """Existing and optimised layouts for one service, their coverage on
    the map, and the optimisation's steps. See the module docstring."""

    settings = service_of(service)
    speed = settings.speed
    target = float(target_seconds or settings.target_seconds)
    if not 1 <= count <= 8:
        raise ValueError("count must be between 1 and 8 stations")
    if target <= 0:
        raise ValueError("target_seconds must be positive")
    if disruption not in DISRUPTIONS:
        raise ValueError(f"disruption must be one of {', '.join(DISRUPTIONS)}")

    blocked, stations = _searches(graph, network, disruption)
    if count > len(stations):
        raise ValueError(f"only {len(stations)} candidate sites")

    # The optimiser's own steps, on its own sampled emergencies
    emergencies = optimiser.sample_emergencies(stations, scenarios, seed, distribution, graph)
    table = optimiser.response_table(stations, emergencies, speed)
    penalty = optimiser.unreachable_penalty(table)
    individual = [optimiser.measure_set(table, [i], penalty, target) for i in range(len(stations))]
    chosen = optimiser.best_station_set(table, count, penalty, seed)
    ordered = optimiser.order_by_contribution(table, chosen, penalty)
    possible = math.comb(len(stations), count)
    exhaustive = possible <= optimiser.EXHAUSTIVE_SET_LIMIT

    # The map is every junction the candidates reach on the UNDISRUPTED
    # network, so places a disruption cuts off stay on it, as unreachable
    _, undisrupted = _searches(graph, network, "none")
    nodes = sorted(set().union(*(s["distances"].keys() for s in undisrupted)), key=str)
    index = {node: i for i, node in enumerate(nodes)}
    from backend.simulation.population import graph_junction_weights

    people = graph_junction_weights(graph, nodes)
    weights = [people.get(node, 0.0) for node in nodes]
    grid, cells = build_cells(graph, nodes, hex_metres)

    def layout_of(members):
        return [stations[i]["distances"] for i in members]

    def stage(label, maps):
        times = junction_seconds(maps, nodes, speed)
        return {
            "label": label,
            "summary": summarise(times, weights, target),
            "cells": cell_values(cells, index, times, weights, target),
        }

    existing, left_out = existing_layout(graph, blocked, service, existing_stations)
    stages = []
    if existing:
        stages.append(stage("existing", [s["distances"] for s in existing]))
    for order in range(1, count + 1):
        stages.append(stage(f"optimised:{order}", layout_of(ordered[:order])))

    def node_point(node):
        data = graph.nodes[node]
        return {"latitude": data["latitude"], "longitude": data["longitude"]}

    ranked = sorted(range(len(stations)), key=lambda i: individual[i]["ranking_seconds"])
    return {
        "service": settings.name,
        "speed": speed,
        "target_seconds": target,
        "disruption": disruption,
        "closed_roads": len(blocked),
        "seed": seed,
        "scenarios": scenarios,
        "distribution": distribution,
        "model": "road-network distance from the nearest station / service speed; no traffic",
        "hex_metres": hex_metres,
        "existing": [
            {"name": s["name"], "osm": s["osm"], "latitude": s["latitude"], "longitude": s["longitude"],
             "snapped": s["snapped"], "snap_metres": s["snap_metres"]}
            for s in existing
        ],
        "existing_left_out": left_out,
        "candidates": [
            {
                **node_point(s["node"]),
                "rank": ranked.index(i) + 1,
                "average_seconds": individual[i]["average_seconds"],
                "within_target_percent": individual[i]["within_target_percent"],
            }
            for i, s in enumerate(stations)
        ],
        "search": {
            "candidates": len(stations),
            "possible_sets": possible,
            "method": "every possible set" if exhaustive else "greedy, swaps and random restarts",
        },
        "optimised": [
            {
                **node_point(stations[i]["node"]),
                "order": order,
                "candidate": i,
                **{k: v for k, v in optimiser.measure_set(table, ordered[:order], penalty, target).items()
                   if k in ("average_seconds", "worst_seconds", "within_target_percent")},
            }
            for order, i in enumerate(ordered, start=1)
        ],
        "cells": [
            {"polygon": grid.polygon(*cell["key"]), "junctions": len(cell["nodes"]),
             "population": round(sum(weights[index[n]] for n in cell["nodes"]), 1)}
            for cell in cells
        ],
        "stages": stages,
    }
