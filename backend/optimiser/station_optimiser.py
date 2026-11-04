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

Choosing SEVERAL stations (optimise_station_set, further down):
    The same random emergencies are used, and each emergency is answered
    by whichever chosen station can reach it fastest. Stations are picked
    one at a time ("greedy"): first the best single station, then the one
    that improves the whole set the most, and so on. Then every chosen
    station is tried against every unchosen one, and swaps that help are
    kept. The result is compared with sensible baselines (the best single
    station, the k best individually ranked stations, and random stations).

Limitations (worth stating in your README):
    - Emergencies are spread evenly over road junctions. Real
      emergencies cluster where more people live.
    - Response time ignores traffic; it only uses road length and
      any road closures you pass in.
"""

import heapq
import itertools
import math
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


# How many random station sets the "random" baseline averages over
RANDOM_BASELINE_TRIALS = 100


def check_settings(scenarios, target_seconds, speed):
    """Reject settings that would give meaningless or crashing results.

    Regression: speed 0 crashed with ZeroDivisionError, and a speed of
    NaN or a target of 0 or less was accepted and produced nonsense.
    """

    if scenarios < 1:
        raise ValueError("scenarios must be at least 1.")

    if not math.isfinite(target_seconds) or target_seconds <= 0:
        raise ValueError("target_seconds must be a positive number.")

    if not math.isfinite(speed) or speed <= 0:
        raise ValueError("speed must be a positive number.")


def prepare_stations(graph, candidates, blocked_segments=None):
    """Snap each candidate to a junction and search from it once.

    Candidates that snap to the same junction are only kept once.
    """

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

    return stations


def sample_emergencies(stations, scenarios, seed):
    """Random emergencies, the same for every candidate (same seed = same list).

    We only use junctions at least one candidate can reach.
    """

    reachable = set()

    for station in stations:
        reachable |= station["distances"].keys()

    pool = sorted(reachable)
    generator = random.Random(seed)

    return [generator.choice(pool) for _ in range(scenarios)]


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

    check_settings(scenarios, target_seconds, speed)

    # Step 1: snap each candidate to a junction and search from it once.
    stations = prepare_stations(graph, candidates, blocked_segments)

    # Step 2: random emergencies, the same for every candidate.
    emergencies = sample_emergencies(stations, scenarios, seed)

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


# ============================================================
# SEVERAL STATIONS AT ONCE
# ============================================================
#
# Everything below works on a table of response times:
#
#     times[c][e] = seconds for candidate c to reach emergency e
#                   (math.inf if it cannot get there)
#
# A "set" of stations is just a list of candidate numbers. The nearest
# station (by road) answers each emergency, so an emergency's response
# time for a set is the smallest value in its column.
#
# Simplification: every emergency is treated on its own, so a station
# never runs out of ambulances and nobody queues for one.


def response_table(stations, emergencies, speed):
    """times[c][e], as described above."""

    return [
        [
            station["distances"][emergency] / speed
            if emergency in station["distances"] else math.inf
            for emergency in emergencies
        ]
        for station in stations
    ]


def unreachable_penalty(times):
    """What an unreached emergency counts as when ranking.

    The slowest response any candidate achieves anywhere in this run:
    the same rule the single-station ranking uses, so one-station and
    several-station results can be compared fairly.
    """

    return max(
        (t for row in times for t in row if t != math.inf),
        default=0
    )


def measure_set(times, members, penalty, target_seconds):
    """How well a set of stations (candidate numbers) answers every emergency.

    Returns the same measures as a single-station result:
    average and worst response (over the emergencies reached), the
    percentage reached within the target, how many were not reached
    at all, and the ranking value (unreached counted at the penalty).
    """

    scenarios = len(times[0])

    fastest = [
        min(times[member][emergency] for member in members)
        for emergency in range(scenarios)
    ]

    reached = [t for t in fastest if t != math.inf]
    unreachable = scenarios - len(reached)

    return {
        "average_seconds": sum(reached) / len(reached) if reached else None,
        "worst_seconds": max(reached) if reached else None,
        "within_target_percent": (
            100 * sum(1 for t in reached if t <= target_seconds) / scenarios
        ),
        "unreachable": unreachable,
        "ranking_seconds": (sum(reached) + unreachable * penalty) / scenarios,
    }


def greedy_selection(times, count, penalty):
    """Pick `count` candidates one at a time, each time adding the station
    that lowers the overall ranking value the most.

    Greedy is simple and fast but can get stuck: the best FIRST station
    is not always part of the best PAIR. refine_by_swapping (below) fixes
    most of that.
    """

    scenarios = len(times[0])

    chosen = []
    best_so_far = [math.inf] * scenarios

    for _ in range(count):

        best_candidate = None
        best_score = (math.inf, math.inf)

        for candidate in range(len(times)):

            if candidate in chosen:
                continue

            cost = 0.0
            unreached = 0

            for emergency in range(scenarios):
                t = min(best_so_far[emergency], times[candidate][emergency])

                if t == math.inf:
                    cost += penalty
                    unreached += 1
                else:
                    cost += t

            # Lowest cost wins. If two are exactly level, the one that
            # leaves fewer emergencies unreached wins; after that the
            # earlier candidate, so nothing here is random.
            if (cost, unreached) < best_score:
                best_score = (cost, unreached)
                best_candidate = candidate

        chosen.append(best_candidate)

        best_so_far = [
            min(best_so_far[emergency], times[best_candidate][emergency])
            for emergency in range(scenarios)
        ]

    return chosen


def set_score(times, members, penalty):
    """What selection tries to minimise: (ranking value, unreached count)."""

    measured = measure_set(times, members, penalty, 1)

    return (measured["ranking_seconds"], measured["unreachable"])


def refine_by_swapping(times, chosen, penalty, max_passes=20):
    """Improve a set by swapping stations in and out.

    For each chosen station, try replacing it with each station that was
    not chosen. Keep any swap that makes the set better, and repeat until
    a whole pass finds nothing. The result is never worse than the set
    it started with.
    """

    members = list(chosen)
    score = set_score(times, members, penalty)

    for _ in range(max_passes):

        improved = False

        for position in range(len(members)):

            for candidate in range(len(times)):

                if candidate in members:
                    continue

                trial = members[:position] + [candidate] + members[position + 1:]
                trial_score = set_score(times, trial, penalty)

                if trial_score < score:
                    members = trial
                    score = trial_score
                    improved = True

        if not improved:
            break

    return members


def order_by_contribution(times, members, penalty):
    """Put a set in the order a planner would build it.

    First the member that is best on its own, then the one that adds most
    to it, and so on (greedy, but only among the members already chosen).
    """

    sub_table = [times[member] for member in members]
    order = greedy_selection(sub_table, len(members), penalty)

    return [members[index] for index in order]


def exhaustive_selection(times, count, penalty):
    """Try EVERY set of `count` candidates and return the best one.

    Only practical for small problems (the number of sets grows very
    quickly), so it is used by the tests and the audit tool to check how
    close greedy gets, not by the API.
    """

    best_members = None
    best_value = (math.inf, math.inf)

    for members in itertools.combinations(range(len(times)), count):

        value = set_score(times, members, penalty)

        if value < best_value:
            best_value = value
            best_members = list(members)

    return best_members


def random_baseline(times, count, penalty, target_seconds, seed, trials):
    """Average result of `trials` random sets of `count` stations."""

    generator = random.Random(seed)
    candidates = range(len(times))

    measured = [
        measure_set(
            times,
            generator.sample(candidates, count),
            penalty,
            target_seconds
        )
        for _ in range(trials)
    ]

    def mean_of(field):
        values = [m[field] for m in measured if m[field] is not None]
        return sum(values) / len(values) if values else None

    return {
        "average_seconds": mean_of("average_seconds"),
        "worst_seconds": mean_of("worst_seconds"),
        "within_target_percent": mean_of("within_target_percent"),
        "unreachable": mean_of("unreachable"),
        "ranking_seconds": mean_of("ranking_seconds"),
    }


def optimise_station_set(
    graph,
    candidates,
    count,
    scenarios=200,
    seed=1,
    target_seconds=480,
    speed=20.0,
    blocked_segments=None
):
    """Choose `count` stations from the candidates that work well TOGETHER.

    Arguments are the same as optimise_station_locations, plus `count`.
    Raises ValueError if the settings make no sense, or if there are fewer
    than `count` distinct candidate junctions.

    Returns a dict:
        stations   the chosen stations in the order they were picked, each
                   with the whole set's average response after it was added
        result     how the chosen set performs (see measure_set)
        baselines  the same measures for sensible alternatives:
                       best_single     the best one station on its own
                       top_individual  the `count` stations that rank best
                                       one by one (they tend to bunch up)
                       random          the average of random sets
    """

    if not candidates:
        raise ValueError("Give at least one candidate location.")

    if count < 1:
        raise ValueError("Choose at least one station.")

    check_settings(scenarios, target_seconds, speed)

    stations = prepare_stations(graph, candidates, blocked_segments)

    if count > len(stations):
        raise ValueError(
            f"Asked for {count} stations but only {len(stations)} distinct "
            f"candidate junctions are available."
        )

    emergencies = sample_emergencies(stations, scenarios, seed)

    times = response_table(stations, emergencies, speed)
    penalty = unreachable_penalty(times)

    # --- the chosen set ----------------------------------------
    # Pick greedily, improve by swapping, then list the stations in the
    # order they add value.
    chosen = greedy_selection(times, count, penalty)
    chosen = refine_by_swapping(times, chosen, penalty)
    chosen = order_by_contribution(times, chosen, penalty)

    picks = []

    for order in range(1, count + 1):

        so_far = measure_set(times, chosen[:order], penalty, target_seconds)
        node_data = graph.nodes[stations[chosen[order - 1]]["node"]]

        picks.append({
            "order": order,
            "node": stations[chosen[order - 1]]["node"],
            "latitude": node_data["latitude"],
            "longitude": node_data["longitude"],
            # Whole set so far, so you can see what each extra station adds
            "average_seconds_after": so_far["average_seconds"],
            "within_target_percent_after": so_far["within_target_percent"],
        })

    result = measure_set(times, chosen, penalty, target_seconds)

    # --- baselines ----------------------------------------------
    best_single = measure_set(
        times, greedy_selection(times, 1, penalty), penalty, target_seconds
    )

    # Rank every candidate on its own, then just take the best `count`
    one_by_one = sorted(
        range(len(times)),
        key=lambda candidate: measure_set(
            times, [candidate], penalty, target_seconds
        )["ranking_seconds"]
    )
    top_individual = measure_set(
        times, one_by_one[:count], penalty, target_seconds
    )

    baselines = [
        {
            "name": "best_single",
            "label": "Best single station",
            "stations": 1,
            **best_single,
        },
        {
            "name": "top_individual",
            "label": f"Top {count} ranked one by one",
            "stations": count,
            **top_individual,
        },
        {
            "name": "random",
            "label": f"{count} random stations (average of "
                     f"{RANDOM_BASELINE_TRIALS})",
            "stations": count,
            **random_baseline(
                times, count, penalty, target_seconds,
                seed + 1, RANDOM_BASELINE_TRIALS
            ),
        },
    ]

    return {
        "count": count,
        "candidates_considered": len(stations),
        "stations": picks,
        "result": result,
        "baselines": baselines,
    }


def percent_faster(before, after):
    """How much smaller `after` is than `before`, as a percentage."""

    if before is None or after is None or before <= 0:
        return None

    return 100 * (before - after) / before


def explain_station_set(outcome, scenarios, target_seconds):
    """Plain-English summary of optimise_station_set's answer.

    Only says things the numbers support: if a baseline does as well as
    the chosen set, the sentence says so rather than claiming a win.
    """

    count = outcome["count"]
    result = outcome["result"]
    baselines = {b["name"]: b for b in outcome["baselines"]}

    if result["average_seconds"] is None:
        return ["The chosen stations could not reach any of the emergencies."]

    sentences = []

    if count == 1:
        sentences.append(
            f"One station was requested, so this is the best single station: "
            f"average {minutes(result['average_seconds'])}, worst case "
            f"{minutes(result['worst_seconds'])}."
        )
    else:
        sentences.append(
            f"{count} stations together average "
            f"{minutes(result['average_seconds'])} per emergency, "
            f"with a worst case of {minutes(result['worst_seconds'])} "
            f"(best single station: "
            f"{minutes(baselines['best_single']['worst_seconds'])})."
        )

        for name, description in (
            ("best_single", "the best single station"),
            ("top_individual", "the top-ranked stations picked one by one"),
            ("random", "random stations"),
        ):
            other = baselines[name]
            gain = percent_faster(
                other["average_seconds"], result["average_seconds"]
            )

            if gain is None:
                continue

            if gain > 0.5:
                sentences.append(
                    f"On average it is {gain:.0f}% faster than {description} "
                    f"({minutes(other['average_seconds'])})."
                )
            elif gain < -0.5:
                sentences.append(
                    f"On average {description} did better "
                    f"({minutes(other['average_seconds'])}); the chosen set "
                    f"is {-gain:.0f}% slower."
                )
            else:
                sentences.append(
                    f"On average it is about the same as {description} "
                    f"({minutes(other['average_seconds'])})."
                )

    sentences.append(
        f"{result['within_target_percent']:.1f}% of emergencies were reached "
        f"within the {minutes(target_seconds)} target"
        + (
            f" (best single station: "
            f"{baselines['best_single']['within_target_percent']:.1f}%)."
            if count > 1 else "."
        )
    )

    if result["unreachable"]:
        sentences.append(
            f"{result['unreachable']} of the {scenarios} emergencies could "
            f"not be reached by any chosen station; those count as the "
            f"slowest response seen."
        )

    return sentences
