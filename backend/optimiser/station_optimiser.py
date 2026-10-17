"""
Emergency Station Optimiser
---------------------------

Question it answers:
    "If we build a new ambulance station at one of these candidate
     locations, which one gets help to people fastest?"

How it works (simple version):
    1. Pick a number of random emergencies on the road network
       (the same random emergencies are used for every candidate,
       so the comparison is fair).
    2. For each candidate station, work out the driving distance to
       every road junction ONCE (one Dijkstra search per candidate,
       not one search per emergency - this is what keeps it fast).
    3. Response time = driving distance / ambulance speed.
    4. Compare candidates on average, worst case and the percentage
       of emergencies reached within the target time.

Limitations (worth stating in your README):
    - Emergencies are spread evenly over road junctions. Real
      emergencies cluster where more people live.
    - Response time ignores traffic; it only uses road length and
      any road closures you pass in.
"""

import heapq
import random

from backend.routing.a_star import get_segment


def shortest_distances(graph, source, blocked_segments=None):
    """Driving distance (metres) from `source` to every junction it can reach.

    This is Dijkstra's algorithm without a destination: it keeps going
    until it has found the shortest distance to everywhere.
    """

    blocked = blocked_segments or set()

    distances = {source: 0}
    queue = [(0, source)]

    while queue:

        current_distance, node = heapq.heappop(queue)

        # A shorter way to this node was already found
        if current_distance > distances.get(node, float("inf")):
            continue

        for neighbour, weight in graph.get_neighbours(node):

            if blocked and get_segment(graph, node, neighbour) in blocked:
                continue

            new_distance = current_distance + weight

            if new_distance < distances.get(neighbour, float("inf")):
                distances[neighbour] = new_distance
                heapq.heappush(queue, (new_distance, neighbour))

    return distances


def snap_to_road(graph, latitude, longitude, min_reach=0.9, tries=50):
    """Find the junction nearest to a point.

    Some junctions sit in small one-way pockets that can only reach a
    tiny part of the map. A station there would look terrible for the
    wrong reason, so we skip those and take the nearest well-connected
    junction instead.
    """

    def gap(node):
        data = graph.nodes[node]
        return (
            (data["latitude"] - latitude) ** 2
            + (data["longitude"] - longitude) ** 2
        )

    closest = sorted(graph.nodes, key=gap)[:tries]

    for node in closest:
        reach = len(shortest_distances(graph, node))
        if reach >= min_reach * len(graph.nodes):
            return node

    return closest[0]


def grid_candidates(graph, rows=3, columns=3):
    """Spread candidate stations evenly over the map (a rows x columns grid)."""

    latitudes = [data["latitude"] for data in graph.nodes.values()]
    longitudes = [data["longitude"] for data in graph.nodes.values()]

    candidates = []

    for row in range(rows):
        for column in range(columns):

            latitude = min(latitudes) + (
                (row + 0.5) / rows
            ) * (max(latitudes) - min(latitudes))

            longitude = min(longitudes) + (
                (column + 0.5) / columns
            ) * (max(longitudes) - min(longitudes))

            candidates.append((latitude, longitude))

    return candidates


def optimise_station_locations(
    graph,
    candidates,
    scenarios=200,
    seed=1,
    target_seconds=480,
    speed=20.0,
    blocked_segments=None
):
    """Rank candidate station locations.

    candidates      list of (latitude, longitude) points
    scenarios       how many random emergencies to simulate
    seed            same seed = same emergencies = repeatable results
    target_seconds  "good" response time (default 8 minutes)
    speed           ambulance speed in metres per second (20 = 72 km/h)
    blocked_segments  closed roads to route around (optional)

    Returns a list of results, best candidate first.
    """

    if not candidates:
        raise ValueError("Give at least one candidate location.")

    # Step 1: snap each candidate to a junction and search from it once
    stations = []

    for latitude, longitude in candidates:

        node = snap_to_road(graph, latitude, longitude)

        stations.append({
            "node": node,
            "distances": shortest_distances(graph, node, blocked_segments)
        })

    # Step 2: random emergencies, the same for every candidate.
    # We only use junctions at least one candidate can reach.
    reachable = set()

    for station in stations:
        reachable |= station["distances"].keys()

    pool = sorted(reachable)
    generator = random.Random(seed)
    emergencies = [generator.choice(pool) for _ in range(scenarios)]

    # Step 3: measure response times for each candidate
    results = []

    for station in stations:

        times = []
        unreachable = 0

        for emergency in emergencies:

            distance = station["distances"].get(emergency)

            if distance is None:
                unreachable += 1
            else:
                times.append(distance / speed)

        within_target = sum(1 for t in times if t <= target_seconds)

        node_data = graph.nodes[station["node"]]

        results.append({
            "node": station["node"],
            "latitude": node_data["latitude"],
            "longitude": node_data["longitude"],
            "average_seconds": (
                sum(times) / len(times) if times else None
            ),
            "worst_seconds": max(times) if times else None,
            "within_target_percent": 100 * within_target / scenarios,
            "unreachable": unreachable,
        })

    # Step 4: best first = lowest average response time
    results.sort(
        key=lambda result: (
            result["average_seconds"] is None,
            result["average_seconds"] or 0
        )
    )

    for rank, result in enumerate(results, start=1):
        result["rank"] = rank

    return results
