"""Download the Norwich road network from OpenStreetMap and save it as a
Graph (data/osm/norwich_graph.pkl).

    python -m backend.routing.osm_loader               download (or use the cache)
    python -m backend.routing.osm_loader --cache-only  rebuild only from cache/

osmnx keeps the raw OpenStreetMap responses in cache/. Copies of the ones
the committed map was made from (8 September 2026: the Nominatim boundary
of Norwich and the Overpass road data) are committed, gzipped, in
data/osm/source/ (OpenStreetMap data, ODbL: see data/osm/README.md) and are
restored into cache/ when missing, so the map can be rebuilt from exactly
that data on any machine; --cache-only refuses to download, so a rebuild
can never silently pick up a different, newer snapshot.

Every road keeps its length (the edge weight, as before) and its
attributes: class, speed limit, lanes, one-way and name (road_attributes.py).
"""

import argparse
import pickle
from pathlib import Path

import osmnx as ox

from backend.routing.graph import Graph
from backend.routing.road_attributes import road_info_from_osm


PLACE = "Norwich, Norfolk, England, United Kingdom"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_FILE = PROJECT_ROOT / "data" / "osm" / "norwich_graph.pkl"
CACHE_FOLDER = PROJECT_ROOT / "cache"
SOURCE_FOLDER = PROJECT_ROOT / "data" / "osm" / "source"


def restore_source_responses():
    """Copy the committed OpenStreetMap responses into osmnx's cache (only
    those missing there). Returns how many were restored."""

    import gzip

    CACHE_FOLDER.mkdir(exist_ok=True)
    restored = 0

    for packed in sorted(SOURCE_FOLDER.glob("*.json.gz")):
        target = CACHE_FOLDER / packed.name[: -len(".gz")]
        if not target.exists():
            target.write_bytes(gzip.decompress(packed.read_bytes()))
            restored += 1

    return restored


def download_norwich_network(cache_only=False):
    ox.settings.use_cache = True
    ox.settings.cache_folder = str(CACHE_FOLDER)

    restored = restore_source_responses()
    if restored:
        print(f"Restored {restored} saved OpenStreetMap response(s) into {CACHE_FOLDER}")

    if cache_only:
        # osmnx's own cache_only_mode stops before building the graph, so
        # instead refuse any request that is not already in the cache
        _refuse_downloads()

    print("Loading the Norwich road network from OpenStreetMap...")

    osm_graph = ox.graph.graph_from_place(
        PLACE,
        network_type="drive",
        simplify=True,
        retain_all=True
    )

    print(f"OSM nodes: {len(osm_graph.nodes):,}")
    print(f"OSM edges: {len(osm_graph.edges):,}")

    return osm_graph


def _refuse_downloads():
    import requests

    def refuse(*args, **kwargs):
        raise RuntimeError(
            "--cache-only: this request is not in cache/, so the map would "
            "come from different data. Run without --cache-only to download."
        )

    requests.get = refuse
    requests.post = refuse
    requests.Session.request = refuse


def convert_to_custom_graph(osm_graph):
    graph = Graph()

    for node_id, data in osm_graph.nodes(data=True):
        graph.add_node(
            node_id,
            data["y"],
            data["x"]
        )

    # Shortest length seen so far for each directed road: where
    # OpenStreetMap has two parallel roads between the same junctions,
    # routing uses the shorter one, so its attributes are the ones kept
    shortest = {}

    for from_node, to_node, data in osm_graph.edges(data=True):
        distance = data.get("length")

        if distance is None:
            continue

        graph.add_edge(
            from_node,
            to_node,
            distance
        )

        key = (from_node, to_node)
        if key not in shortest or distance < shortest[key]:
            shortest[key] = distance
            graph.road_info[key] = road_info_from_osm(data)

    return graph


def save_graph(graph):
    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with open(OUTPUT_FILE, "wb") as file:
        pickle.dump(graph, file)

    print(f"\nSaved graph to: {OUTPUT_FILE}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--cache-only", action="store_true",
        help="rebuild only from the cached OpenStreetMap response",
    )
    args = parser.parse_args()

    osm_graph = download_norwich_network(cache_only=args.cache_only)

    graph = convert_to_custom_graph(osm_graph)

    print("\nCustom graph:")
    print(f"Nodes: {len(graph.nodes):,}")
    print(f"Nodes with outgoing edges: {len(graph.edges):,}")
    print(f"Roads with attributes: {len(graph.road_info):,}")

    save_graph(graph)


if __name__ == "__main__":
    main()
