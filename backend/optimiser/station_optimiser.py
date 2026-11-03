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
    5. Rank by average response time. An emergency a station cannot
       reach at all counts as the slowest response seen in the run, so a
       station cut off from most of the map can never rank well.

Limitations (worth stating in your README):
    - Emergencies are spread evenly over road junctions. Real
      emergencies cluster where more people live.
    - Response time ignores traffic; it only uses road length and
      any road closures you pass in.
"""

import heapq
import random
import statistics

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


def snap_with_check(graph, latitude, longitude, min_reach=0.9, tries=50):
    """Like snap_to_road, but also says whether the junction is well connected.

    Returns (junction, well_connected). well_connected is False when none
    of the nearest junctions could reach at least `min_reach` of the map,
    in which case the closest junction is returned anyway.
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
            return node, True

    return closest[0], False


def snap_to_road(graph, latitude, longitude, min_reach=0.9, tries=50):
    """Find the junction nearest to a point.

    Some junctions sit in small one-way pockets that can only reach a
    tiny part of the map. A station there would look terrible for the
    wrong reason, so we skip those and take the nearest well-connected
    junction instead.
    """

    return snap_with_check(graph, latitude, longitude, min_reach, tries)[0]


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


def dense_candidates(graph, rows=7, columns=7, min_reach=0.9):
    """A denser set of candidate stations: a rows x columns grid over the map.

    Each grid point is snapped to the nearest well-connected junction.
    Points that land on the same junction are only kept once, and points
    in the countryside with no well-connected junction nearby are dropped,
    so every candidate returned is a sensible place for a station.
    """

    chosen = []
    seen = set()

    for latitude, longitude in grid_candidates(graph, rows, columns):

        node, well_connected = snap_with_check(
            graph, latitude, longitude, min_reach
        )

        if node in seen or not well_connected:
            continue

        seen.add(node)

        data = graph.nodes[node]
        chosen.append((data["latitude"], data["longitude"]))

    return chosen


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

    # Step 1: snap each candidate to a junction and search from it once.
    # Candidates that snap to the same junction are only evaluated once.
    stations = []
    seen_nodes = set()

    for latitude, longitude in candidates:

        node = snap_to_road(graph, latitude, longitude)

        if node in seen_nodes:
            continue

        seen_nodes.add(node)

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
    measured = []

    for station in stations:

        times = []
        unreachable = 0

        for emergency in emergencies:

            distance = station["distances"].get(emergency)

            if distance is None:
                unreachable += 1
            else:
                times.append(distance / speed)

        measured.append((station, times, unreachable))

    # An emergency a station cannot reach counts as the slowest response
    # seen in this run (by any candidate). Without this, a station cut
    # off from most of the map would average only the few emergencies it
    # CAN reach and look brilliant.
    penalty = max((max(times) for _, times, _ in measured if times), default=0)

    results = []

    for station, times, unreachable in measured:

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
            # What the ranking uses: the average over ALL emergencies,
            # with unreached ones counted at the penalty above.
            "ranking_seconds": (
                (sum(times) + unreachable * penalty) / scenarios
            ),
        })

    # Step 4: best first = lowest ranking time (same as the plain average
    # whenever a station reaches every emergency)
    results.sort(key=lambda result: result["ranking_seconds"])

    for rank, result in enumerate(results, start=1):
        result["rank"] = rank

    return results


def minutes(seconds):
    return f"{seconds / 60:.1f} min"


def explain_best(results, scenarios, target_seconds):
    """Say, in plain English, why the top-ranked station won.

    `results` is the list returned by optimise_station_locations.
    Returns a short list of sentences for the frontend to display.
    """

    measured = [r for r in results if r["average_seconds"] is not None]

    if not measured:
        return ["No candidate could reach any of the emergencies."]

    best = results[0]

    if best["average_seconds"] is None:
        return ["The top-ranked station could not reach any emergency."]

    sentences = []

    if len(results) == 1:
        sentences.append(
            f"Candidate #1 averages {minutes(best['average_seconds'])} "
            f"(only one candidate was compared)."
        )
    else:
        runner_up = results[1]
        typical = statistics.median(r["average_seconds"] for r in measured)

        sentence = (
            f"Candidate #1 has the lowest average response time: "
            f"{minutes(best['average_seconds'])}"
        )

        if runner_up["average_seconds"] is not None:
            gap = runner_up["average_seconds"] - best["average_seconds"]
            sentence += (
                f", {minutes(max(gap, 0))} faster than #2 "
                f"({minutes(runner_up['average_seconds'])})"
            )

        if typical > 0:
            percent = 100 * (typical - best["average_seconds"]) / typical
            sentence += (
                f" and {percent:.0f}% faster than the typical candidate "
                f"({minutes(typical)})"
            )

        sentences.append(sentence + ".")

    # Worst case: be honest if another candidate does better here
    shorter = sum(
        1 for r in measured
        if r["worst_seconds"] < best["worst_seconds"]
    )

    if shorter == 0:
        sentences.append(
            f"It also has the best worst case: "
            f"{minutes(best['worst_seconds'])}."
        )
    else:
        sentences.append(
            f"Its worst case is {minutes(best['worst_seconds'])}; "
            f"{shorter} other candidate(s) have a shorter worst case, "
            f"so the winner was chosen on the average."
        )

    sentences.append(
        f"{best['within_target_percent']:.0f}% of emergencies were reached "
        f"within the {minutes(target_seconds)} target."
    )

    if best["unreachable"]:
        sentences.append(
            f"It could not reach {best['unreachable']} of the {scenarios} "
            f"emergencies; those count as the slowest response seen."
        )

    return sentences
