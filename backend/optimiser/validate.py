"""
Check the optimiser against the full simulation.

The optimiser works out response time as  driving distance / speed.
The simulation actually drives an ambulance along the route, second by
second. If the optimiser is trustworthy, the two should agree.

Run from the project root:

    python -m backend.optimiser.validate
"""

import pickle
import random
import statistics
from pathlib import Path

from backend.optimiser.station_optimiser import shortest_distances
from backend.routing.a_star import a_star
from backend.simulation.simulation import Simulation

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GRAPH_FILE = PROJECT_ROOT / "data" / "osm" / "norwich_graph.pkl"

PAIRS = 150
SPEED = 20  # metres per second
SEED = 21


def to_points(graph, path):
    return [
        {
            "latitude": graph.nodes[node]["latitude"],
            "longitude": graph.nodes[node]["longitude"],
        }
        for node in path
    ]


def simulated_response_time(graph, path):
    """Drive an ambulance along `path` in the full simulation."""

    start, end = graph.nodes[path[0]], graph.nodes[path[-1]]

    simulation = Simulation(to_points(graph, path))

    station = simulation.add_station(start["latitude"], start["longitude"])
    simulation.add_emergency_vehicle(
        [station.get_location(), station.get_location()],
        station,
        speed=SPEED,
    )

    emergency = simulation.create_emergency(end["latitude"], end["longitude"])
    vehicle = simulation.dispatch_from_nearest_station(emergency)
    vehicle.set_route(to_points(graph, path), reset_distance=True)

    for _ in range(5000):
        if emergency.resolved:
            break
        simulation.step()

    return emergency.response_time


def main():
    with open(GRAPH_FILE, "rb") as file:
        graph = pickle.load(file)

    generator = random.Random(SEED)
    nodes = sorted(graph.nodes)

    differences = []

    while len(differences) < PAIRS:

        station, emergency = generator.sample(nodes, 2)
        path, distance = a_star(graph, station, emergency)

        if not path or distance < 500:
            continue

        optimiser_time = shortest_distances(graph, station)[emergency] / SPEED
        simulation_time = simulated_response_time(graph, path)

        differences.append(
            100 * (simulation_time - optimiser_time) / optimiser_time
        )

    print(
        f"{PAIRS} random station-to-emergency pairs: simulation vs optimiser "
        f"response time, mean difference {statistics.mean(differences):+.1f}%, "
        f"range {min(differences):+.1f}% to {max(differences):+.1f}%"
    )


if __name__ == "__main__":
    main()
