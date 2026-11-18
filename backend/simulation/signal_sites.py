"""Where Norwich's traffic lights are, from OpenStreetMap.

    python -m backend.simulation.signal_sites     rebuild data/osm/norwich_signals.json

OpenStreetMap tags traffic lights as nodes with highway=traffic_signals.
Mappers often put them on each approach a few metres before the junction
rather than on the junction itself, and the road graph keeps only
junctions (OSMnx simplifies everything else away). So each signal node is
snapped to the nearest graph junction with three or more roads, within
SNAP_METRES. Signals tagged as pedestrian crossings are left out.

The result is the set of junctions the "osm" signal layout makes
signalised (signals.py). It is OpenStreetMap data, under the ODbL like the
graph (data/osm/README.md), and it is derived from the committed raw
download (data/osm/source/), so it can be rebuilt exactly.
"""

import gzip
import json
import math
import pickle
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GRAPH_FILE = PROJECT_ROOT / "data" / "osm" / "norwich_graph.pkl"
SOURCE = PROJECT_ROOT / "data" / "osm" / "source" / "81c6165849a925b01d5170b0b41d392afd6d2ade.json.gz"
SIGNALS_FILE = PROJECT_ROOT / "data" / "osm" / "norwich_signals.json"

SNAP_METRES = 30.0


def _metres(a, b):
    x = (b[1] - a[1]) * 111_320 * math.cos(math.radians(a[0]))
    y = (b[0] - a[0]) * 111_320
    return math.hypot(x, y)


def neighbours(graph):
    """Each junction's neighbouring junctions, either direction."""
    around = {}
    for a, roads in graph.edges.items():
        for b, _ in roads:
            around.setdefault(a, set()).add(b)
            around.setdefault(b, set()).add(a)
    return around


def signal_junctions(graph, signal_points, snap_metres=SNAP_METRES):
    """Junctions (3+ roads) within snap_metres of a signal, nearest first:
    {junction: number of signal nodes snapped to it}."""

    around = neighbours(graph)
    junctions = [node for node, near in around.items() if len(near) >= 3]
    position = {node: (graph.nodes[node]["latitude"], graph.nodes[node]["longitude"]) for node in junctions}
    found = {}
    for point in signal_points:
        best = min(junctions, key=lambda node: _metres(point, position[node]))
        if _metres(point, position[best]) <= snap_metres:
            found[best] = found.get(best, 0) + 1
    return found


def osm_signal_points(source=SOURCE):
    data = json.loads(gzip.decompress(source.read_bytes()))
    elements = data["elements"] if isinstance(data, dict) else data[0]["elements"]
    points = []
    for element in elements:
        tags = element.get("tags", {})
        if element["type"] != "node" or tags.get("highway") != "traffic_signals":
            continue
        if tags.get("crossing") or tags.get("traffic_signals") == "crossing":
            continue
        points.append((element["lat"], element["lon"]))
    return points


def load_signal_junctions(path=SIGNALS_FILE):
    """The signalised junctions of the Norwich map (node ids)."""
    with open(path) as file:
        return [entry["node"] for entry in json.load(file)["junctions"]]


def main():
    with open(GRAPH_FILE, "rb") as file:
        graph = pickle.load(file)
    points = osm_signal_points()
    found = signal_junctions(graph, points)
    body = {
        "source": "OpenStreetMap (ODbL), highway=traffic_signals nodes, download of 8 September 2026",
        "snap_metres": SNAP_METRES,
        "signal_nodes": len(points),
        "junctions": [
            {"node": node, "signal_nodes": count,
             "latitude": graph.nodes[node]["latitude"], "longitude": graph.nodes[node]["longitude"]}
            for node, count in sorted(found.items())
        ],
    }
    SIGNALS_FILE.write_text(json.dumps(body, indent=1) + "\n")
    print(f"{len(found)} signalised junctions from {len(points)} signal nodes")


if __name__ == "__main__":
    main()
