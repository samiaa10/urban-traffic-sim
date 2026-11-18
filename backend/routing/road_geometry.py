"""The real shape of every road, from OpenStreetMap, for drawing vehicles
on the road rather than on the straight line between its two junctions.

    python -m backend.routing.road_geometry   rebuild data/osm/norwich_road_geometry.json.gz

The road graph keeps only junctions: OSMnx joined the OpenStreetMap nodes
between two junctions into one road. This walks the same chains in the
committed raw download (data/osm/source/): from each junction along OSM's
node-to-node links, through nodes that are not junctions, until the next
junction. Each road gets the points it really passes through.

Only drawing uses it: the simulation still measures each road by its real
length (which OSMnx took along these same points) and moves vehicles by
distance along it, so a vehicle's distance along a road is turned into the
point that far along the road's shape (point_at).
"""

import gzip
import json
import math
import pickle
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GRAPH_FILE = PROJECT_ROOT / "data" / "osm" / "norwich_graph.pkl"
SOURCE = PROJECT_ROOT / "data" / "osm" / "source" / "81c6165849a925b01d5170b0b41d392afd6d2ade.json.gz"
GEOMETRY_FILE = PROJECT_ROOT / "data" / "osm" / "norwich_road_geometry.json.gz"


def _metres(a, b):
    x = (b[1] - a[1]) * 111_320 * math.cos(math.radians((a[0] + b[0]) / 2))
    y = (b[0] - a[0]) * 111_320
    return math.hypot(x, y)


def bearing(a, b):
    """Compass bearing (degrees) from point a to point b, both (lat, lon)."""
    x = (b[1] - a[1]) * math.cos(math.radians(a[0]))
    y = b[0] - a[0]
    return math.degrees(math.atan2(x, y)) % 360


def build(graph, source=SOURCE):
    """{(junction a, junction b): [(lat, lon), ...]} for the graph's roads."""

    data = json.loads(gzip.decompress(source.read_bytes()))
    elements = data["elements"] if isinstance(data, dict) else data[0]["elements"]
    position = {e["id"]: (e["lat"], e["lon"]) for e in elements if e["type"] == "node"}

    links = {}
    for element in elements:
        if element["type"] != "way":
            continue
        chain = [n for n in element["nodes"] if n in position]
        for a, b in zip(chain, chain[1:]):
            links.setdefault(a, set()).add(b)
            links.setdefault(b, set()).add(a)

    junctions = set(graph.nodes)
    roads = {(a, b) for a, near in graph.edges.items() for b, _ in near}
    found = {}
    for start in junctions:
        for first in links.get(start, ()):
            path = [start, first]
            while path[-1] not in junctions:
                onward = [n for n in links.get(path[-1], ()) if n != path[-2]]
                if len(onward) != 1 or onward[0] in path:
                    break
                path.append(onward[0])
            end = path[-1]
            if end in junctions and end != start:
                for key, nodes in (((start, end), path), ((end, start), path[::-1])):
                    if key in roads and key not in found:
                        found[key] = [position[n] for n in nodes]
    return found


class RoadGeometry:
    """Road shapes keyed by their two junctions' (lat, lon), as vehicles'
    routes hold them."""

    def __init__(self, shapes):
        # (point a, point b) -> (points, cumulative metres)
        self.shapes = {}
        for points in shapes:
            ends = (tuple(points[0]), tuple(points[-1]))
            lengths = [0.0]
            for a, b in zip(points, points[1:]):
                lengths.append(lengths[-1] + _metres(a, b))
            self.shapes[ends] = ([tuple(p) for p in points], lengths)

    def __len__(self):
        return len(self.shapes)

    def shape(self, a, b):
        """The road's points from a to b (either way round), or None."""
        found = self.shapes.get((a, b))
        if found is not None:
            return found[0]
        found = self.shapes.get((b, a))
        return None if found is None else found[0][::-1]

    def point_at(self, a, b, fraction):
        """(lat, lon, heading) a fraction of the way along the road from a to
        b; along the straight line when the shape is not known."""
        found = self.shapes.get((a, b))
        reverse = False
        if found is None:
            found = self.shapes.get((b, a))
            reverse = found is not None
        fraction = min(1.0, max(0.0, fraction))
        if found is None:
            return (
                a[0] + (b[0] - a[0]) * fraction,
                a[1] + (b[1] - a[1]) * fraction,
                bearing(a, b) if a != b else 0.0,
            )
        points, lengths = found
        total = lengths[-1]
        target = (1 - fraction if reverse else fraction) * total
        for i in range(1, len(points)):
            if lengths[i] >= target or i == len(points) - 1:
                p, q = points[i - 1], points[i]
                span = lengths[i] - lengths[i - 1]
                t = 0.0 if span <= 0 else (target - lengths[i - 1]) / span
                lat = p[0] + (q[0] - p[0]) * t
                lon = p[1] + (q[1] - p[1]) * t
                heading = bearing(q, p) if reverse else bearing(p, q)
                return lat, lon, heading
        return a[0], a[1], 0.0


def load(path=GEOMETRY_FILE):
    """The committed road shapes, or None if the file is missing."""
    if not Path(path).exists():
        return None
    with gzip.open(path, "rt") as file:
        return RoadGeometry(json.load(file)["roads"])


def main():
    with open(GRAPH_FILE, "rb") as file:
        graph = pickle.load(file)
    found = build(graph)
    roads = {(a, b) for a, near in graph.edges.items() for b, _ in near}
    # One direction per road is enough (the other is the same points reversed)
    shapes, seen = [], set()
    for (a, b), points in sorted(found.items(), key=lambda item: str(item[0])):
        if (b, a) in seen:
            continue
        seen.add((a, b))
        shapes.append([[round(lat, 7), round(lon, 7)] for lat, lon in points])
    body = {
        "source": "OpenStreetMap (ODbL), node chains between the graph's junctions, download of 8 September 2026",
        "roads": shapes,
    }
    with gzip.open(GEOMETRY_FILE, "wt") as file:
        json.dump(body, file, separators=(",", ":"))
    print(f"{len(found)} of {len(roads)} directed roads ({100 * len(found) / len(roads):.1f}%) have their shape")


if __name__ == "__main__":
    main()
