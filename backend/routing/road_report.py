"""What the saved map knows about its roads: how many have each attribute
from OpenStreetMap, and how many use a default (road_attributes.py).

    python -m backend.routing.road_report          print the tables
    python -m backend.routing.road_report --save   also write them into
                                                   docs/road_network.md
"""

import argparse
import collections
import pickle
from pathlib import Path

from backend.routing.road_attributes import MPH


PROJECT_ROOT = Path(__file__).resolve().parents[2]
GRAPH_FILE = PROJECT_ROOT / "data" / "osm" / "norwich_graph.pkl"
DOC_FILE = PROJECT_ROOT / "docs" / "road_network.md"

START = "<!-- road-report:start -->"
END = "<!-- road-report:end -->"


def percent(part, whole):
    return f"{100 * part / whole:.1f}%" if whole else "-"


def report(graph):
    roads = [(a, b, length) for a, edges in graph.edges.items() for b, length in edges]
    infos = list(graph.road_info.values())
    total = len(infos)

    lines = [
        f"{len(graph.nodes):,} junctions, {len(roads):,} directed roads "
        f"({total:,} junction pairs with attributes; where OpenStreetMap has two "
        "parallel roads between the same junctions, the shorter one's).",
        "",
        "| Attribute | From OpenStreetMap | Default used |",
        "|---|---|---|",
        f"| Speed limit | {sum(i.speed_limit_tagged for i in infos):,} "
        f"({percent(sum(i.speed_limit_tagged for i in infos), total)}) | "
        f"{sum(not i.speed_limit_tagged for i in infos):,} |",
        f"| Lanes | {sum(i.lanes_tagged for i in infos):,} "
        f"({percent(sum(i.lanes_tagged for i in infos), total)}) | "
        f"{sum(not i.lanes_tagged for i in infos):,} |",
        f"| Name (or road number) | {sum(i.name is not None for i in infos):,} "
        f"({percent(sum(i.name is not None for i in infos), total)}) | "
        f"{sum(i.name is None for i in infos):,} have none |",
        f"| One-way | {sum(i.oneway for i in infos):,} one-way, "
        f"{sum(not i.oneway for i in infos):,} two-way | - |",
        "",
        "| Speed limit | Roads | Share |",
        "|---|---|---|",
    ]

    limits = collections.Counter(round(i.speed_limit / MPH) for i in infos)
    for mph, count in sorted(limits.items()):
        lines.append(f"| {mph} mph | {count:,} | {percent(count, total)} |")

    lines += ["", "| Road class | Roads | Lanes per direction (mean) |", "|---|---|---|"]
    classes = collections.defaultdict(list)
    for info in infos:
        classes[info.highway].append(info.lanes)
    for highway, lanes in sorted(classes.items(), key=lambda item: -len(item[1])):
        lines.append(f"| {highway} | {len(lanes):,} | {sum(lanes) / len(lanes):.2f} |")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args()

    with open(GRAPH_FILE, "rb") as file:
        graph = pickle.load(file)

    text = report(graph)
    print(text)

    if args.save:
        doc = DOC_FILE.read_text()
        before, rest = doc.split(START)
        _, after = rest.split(END)
        DOC_FILE.write_text(f"{before}{START}\n{text}\n{END}{after}")
        print(f"\nSaved into {DOC_FILE}")


if __name__ == "__main__":
    main()
