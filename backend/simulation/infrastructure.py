"""Bridges on the Norwich map, from OpenStreetMap, for infrastructure
failures (a bridge out of use closes the roads it carries).

    python -m backend.simulation.infrastructure   rebuild data/osm/norwich_bridges.json

OpenStreetMap tags roads on bridges with bridge=yes. The road graph keeps
only junctions, so each bridge way is matched to the graph road it is part
of: the road that passes within MATCH_METRES of the bridge's middle and
points the same way (within MATCH_DEGREES). The data has no waterways, so
river bridges cannot be told from flyovers or railway bridges: all are kept,
with their road name.
"""

import gzip
import json
import math
import pickle

from backend.simulation.signal_sites import GRAPH_FILE, PROJECT_ROOT, SOURCE

BRIDGES_FILE = PROJECT_ROOT / "data" / "osm" / "norwich_bridges.json"
MATCH_METRES = 15.0
MATCH_DEGREES = 30.0


def _xy(origin, point):
    return (
        (point[1] - origin[1]) * 111_320 * math.cos(math.radians(origin[0])),
        (point[0] - origin[0]) * 111_320,
    )


def _distance_to_segment(p, a, b):
    ax, ay = _xy(p, a)
    bx, by = _xy(p, b)
    dx, dy = bx - ax, by - ay
    length = dx * dx + dy * dy
    t = 0 if length == 0 else max(0, min(1, -(ax * dx + ay * dy) / length))
    return math.hypot(ax + t * dx, ay + t * dy)


def _bearing(a, b):
    x, y = _xy(a, b)
    return math.degrees(math.atan2(x, y)) % 180   # a line, either way


def bridge_roads(graph, bridges):
    """bridges: [(name, [(lat, lon), ...])]. Returns {(a, b): name} for the
    graph roads (a < b by id) that carry a bridge."""

    position = {n: (d["latitude"], d["longitude"]) for n, d in graph.nodes.items()}
    roads = {tuple(sorted((a, b), key=str)) for a, near in graph.edges.items() for b, _ in near}
    found = {}
    for name, points in bridges:
        middle = points[len(points) // 2]
        direction = _bearing(points[0], points[-1])
        best, best_distance = None, MATCH_METRES
        for a, b in roads:
            distance = _distance_to_segment(middle, position[a], position[b])
            turn = abs(_bearing(position[a], position[b]) - direction)
            if distance <= best_distance and min(turn, 180 - turn) <= MATCH_DEGREES:
                best, best_distance = (a, b), distance
        if best is not None:
            found.setdefault(best, name)
    return found


def osm_bridges(source=SOURCE):
    data = json.loads(gzip.decompress(source.read_bytes()))
    elements = data["elements"] if isinstance(data, dict) else data[0]["elements"]
    nodes = {e["id"]: (e["lat"], e["lon"]) for e in elements if e["type"] == "node"}
    return [
        (e["tags"].get("name") or e["tags"].get("ref") or "unnamed bridge",
         [nodes[n] for n in e["nodes"] if n in nodes])
        for e in elements
        if e["type"] == "way" and e.get("tags", {}).get("bridge") and len(e["nodes"]) >= 2
    ]


def load_bridges(path=BRIDGES_FILE):
    """[(junction a, junction b, name)] of the roads that are bridges."""
    with open(path) as file:
        return [(b["start"], b["destination"], b["name"]) for b in json.load(file)["bridges"]]


def main():
    with open(GRAPH_FILE, "rb") as file:
        graph = pickle.load(file)
    bridges = osm_bridges()
    found = bridge_roads(graph, bridges)
    body = {
        "source": "OpenStreetMap (ODbL), ways tagged bridge=*, download of 8 September 2026",
        "match_metres": MATCH_METRES,
        "bridge_ways": len(bridges),
        "bridges": [
            {"start": a, "destination": b, "name": name,
             "latitude": (graph.nodes[a]["latitude"] + graph.nodes[b]["latitude"]) / 2,
             "longitude": (graph.nodes[a]["longitude"] + graph.nodes[b]["longitude"]) / 2}
            for (a, b), name in sorted(found.items(), key=lambda item: str(item[0]))
        ],
    }
    BRIDGES_FILE.write_text(json.dumps(body, indent=1) + "\n")
    print(f"{len(found)} bridge roads from {len(bridges)} bridge ways")


if __name__ == "__main__":
    main()
