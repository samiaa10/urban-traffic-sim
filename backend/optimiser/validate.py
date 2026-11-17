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
from backend.routing.travel_time import RoadNetwork
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


def simulated_response_time(graph, path, road_lengths=None):
    """Drive an ambulance along `path` in the full simulation.

    With road_lengths (RoadNetwork.length_between_points) the ambulance
    drives the real road lengths, as it does in the API; without, it
    measures each road as a straight line between its junctions.
    """

    start, end = graph.nodes[path[0]], graph.nodes[path[-1]]

    # The Simulation needs a first vehicle, so use a car parked at the
    # station (a route of length zero). It must NOT share the ambulance's
    # route: two vehicles on one road count as traffic and slow each other
    # down, and this check is about an ambulance with the road to itself.
    parked = {"latitude": start["latitude"], "longitude": start["longitude"]}
    simulation = Simulation([parked, parked])

    if road_lengths is not None:
        simulation.use_road_lengths(road_lengths)

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

    road_lengths = RoadNetwork(graph).length_between_points

    generator = random.Random(SEED)
    nodes = sorted(graph.nodes)

    straight = []      # ambulance measures roads as straight lines (old)
    real = []          # ambulance drives the real road lengths (as in the API)

    while len(real) < PAIRS:

        station, emergency = generator.sample(nodes, 2)
        path, distance = a_star(graph, station, emergency)

        if not path or distance < 500:
            continue

        optimiser_time = shortest_distances(graph, station)[emergency] / SPEED

        for results, lengths in ((straight, None), (real, road_lengths)):
            simulation_time = simulated_response_time(graph, path, lengths)
            results.append(
                100 * (simulation_time - optimiser_time) / optimiser_time
            )

    for label, differences in (
        ("straight-line roads", straight),
        ("real road lengths  ", real),
    ):
        print(
            f"{PAIRS} random station-to-emergency pairs, {label}: simulation "
            f"vs optimiser response time, mean difference "
            f"{statistics.mean(differences):+.1f}%, range "
            f"{min(differences):+.1f}% to {max(differences):+.1f}%"
        )


    in_traffic(graph)


def in_traffic(graph, vehicles=1000, pairs=60, warmup=120):
    """The same check with traffic (osm model): 1,000 cars on the road and
    60 ambulances, each sent at the same moment from its own station to its
    own emergency along the fastest route under that traffic, with right of
    way and no speed limits (the realistic rules). How much slower than the
    optimiser's distance / speed estimate does traffic make them?"""

    from backend.simulation import dispatch
    from backend.simulation.road_model import make_road_model
    from backend.simulation.traffic_manager import TrafficManager

    network = RoadNetwork(graph)
    network.use_landmarks(8)
    generator = random.Random(SEED)
    nodes = sorted(network.main_component())

    parked = network.point(nodes[0])
    simulation = Simulation([parked, parked], road_model=make_road_model("osm", network))
    simulation.vehicles[0].finished = True
    manager = TrafficManager(network, simulation)
    manager.spawn_vehicles(vehicles, random.Random(SEED))
    for _ in range(warmup):
        manager.step()

    traffic = simulation.get_traffic_density()
    calls = []
    while len(calls) < pairs:
        station_node, emergency_node = generator.sample(nodes, 2)
        free = shortest_distances(graph, station_node).get(emergency_node)
        if free is None or free < 500:
            continue
        here = network.point(station_node)
        station = simulation.add_station(here["latitude"], here["longitude"])
        ambulance = simulation.add_emergency_vehicle([here, here], station, speed=SPEED)
        station.available_vehicles.remove(ambulance)
        ambulance.available, ambulance.on_emergency = False, True
        latitude, longitude = network.position[emergency_node]
        emergency = simulation.create_emergency(latitude, longitude)
        path, _, _ = dispatch._plan(simulation, network, ambulance, emergency_node, traffic)
        ambulance.set_route(network.points(path), reset_distance=True)
        emergency.assigned_vehicle = ambulance
        emergency.dispatched_at = simulation.time
        calls.append((emergency, free / SPEED))

    for _ in range(3000):
        if all(emergency.resolved for emergency, _ in calls):
            break
        manager.step()

    gaps = [
        100 * (emergency.response_time - estimate) / estimate
        for emergency, estimate in calls if emergency.resolved
    ]
    print(
        f"{len(gaps)} ambulances with {vehicles} cars on the road (osm model, after "
        f"{warmup} s): simulation vs optimiser response time, mean difference "
        f"{statistics.mean(gaps):+.1f}%, median {statistics.median(gaps):+.1f}%, range "
        f"{min(gaps):+.1f}% to {max(gaps):+.1f}%"
    )


if __name__ == "__main__":
    main()
