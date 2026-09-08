
import pickle

import osmnx as ox

from graph import Graph


PLACE = "Norwich, Norfolk, England, United Kingdom"

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_FILE = PROJECT_ROOT / "data" / "osm" / "norwich_graph.pkl"


def download_norwich_network():
    print("Downloading Norwich road network from OpenStreetMap...")

    osm_graph = ox.graph.graph_from_place(
        PLACE,
        network_type="drive",
        simplify=True,
        retain_all=False
    )

    print(f"OSM nodes: {len(osm_graph.nodes):,}")
    print(f"OSM edges: {len(osm_graph.edges):,}")

    return osm_graph


def convert_to_custom_graph(osm_graph):
    graph = Graph()

    for node_id, data in osm_graph.nodes(data=True):
        graph.add_node(
            node_id,
            data["y"],
            data["x"]
        )

    for from_node, to_node, data in osm_graph.edges(data=True):
        distance = data.get("length")

        if distance is None:
            continue

        graph.add_edge(
            from_node,
            to_node,
            distance
        )

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
    osm_graph = download_norwich_network()

    graph = convert_to_custom_graph(osm_graph)

    print("\nCustom graph:")
    print(f"Nodes: {len(graph.nodes):,}")
    print(f"Nodes with outgoing edges: {len(graph.edges):,}")

    save_graph(graph)


if __name__ == "__main__":
    main()