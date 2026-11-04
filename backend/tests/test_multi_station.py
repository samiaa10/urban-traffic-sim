"""Choosing several ambulance stations that work well together."""

import math

import pytest

from backend.optimiser.station_optimiser import (
    check_settings,
    exhaustive_selection,
    explain_station_set,
    greedy_selection,
    measure_set,
    optimise_station_locations,
    optimise_station_set,
    order_by_contribution,
    prepare_stations,
    random_baseline,
    refine_by_swapping,
    response_table,
    sample_emergencies,
    set_score,
    unreachable_penalty,
)
from backend.routing.a_star import get_segment
from backend.routing.graph import Graph
from backend.tests.helpers import make_diamond_graph

INF = math.inf


def make_long_line(length=9):
    """Junctions 1..length in a row, 500 m apart, two-way roads."""
    graph = Graph()

    for number in range(1, length + 1):
        graph.add_node(number, 52.63, 1.29 + 0.0075 * number)

    for number in range(1, length):
        graph.add_edge(number, number + 1, 500)
        graph.add_edge(number + 1, number, 500)

    return graph


def point_of(graph, node):
    return (graph.nodes[node]["latitude"], graph.nodes[node]["longitude"])


def every_junction(graph):
    return [point_of(graph, node) for node in sorted(graph.nodes)]


# ------------------------------------------------------------
# Measuring a set of stations from a table of response times
# ------------------------------------------------------------

def test_set_answers_each_emergency_with_its_nearest_station():
    # two stations, three emergencies
    times = [
        [10, 90, 50],
        [80, 20, 40],
    ]

    measured = measure_set(times, [0, 1], penalty=100, target_seconds=45)

    # fastest per emergency: 10, 20, 40
    assert measured["average_seconds"] == pytest.approx(70 / 3)
    assert measured["worst_seconds"] == 40
    assert measured["within_target_percent"] == 100
    assert measured["unreachable"] == 0


def test_unreachable_emergencies_are_counted_not_hidden():
    times = [
        [10, INF],
        [INF, INF],
    ]

    measured = measure_set(times, [0, 1], penalty=60, target_seconds=480)

    assert measured["unreachable"] == 1
    # the average only covers the emergency that WAS reached...
    assert measured["average_seconds"] == 10
    # ...but ranking charges the penalty for the one that was not
    assert measured["ranking_seconds"] == pytest.approx((10 + 60) / 2)
    # and coverage is out of ALL emergencies, not just the reached ones
    assert measured["within_target_percent"] == 50


def test_set_that_reaches_nothing_has_no_average():
    measured = measure_set([[INF, INF]], [0], penalty=60, target_seconds=480)

    assert measured["average_seconds"] is None
    assert measured["worst_seconds"] is None
    assert measured["unreachable"] == 2
    assert measured["within_target_percent"] == 0


def test_adding_a_station_never_makes_the_ranking_worse():
    times = [
        [10, 90, 50, 70],
        [80, 20, 40, 30],
        [60, 60, 60, 60],
    ]

    one = measure_set(times, [0], 100, 480)["ranking_seconds"]
    two = measure_set(times, [0, 1], 100, 480)["ranking_seconds"]
    three = measure_set(times, [0, 1, 2], 100, 480)["ranking_seconds"]

    assert three <= two <= one


# ------------------------------------------------------------
# Greedy selection
# ------------------------------------------------------------

def test_greedy_prefers_the_station_that_fills_a_gap():
    # Station 1 is the best on its own (194 vs 200). Station 0 is nearly
    # identical to it, so the second pick should be station 2, which
    # covers the emergencies stations 0 and 1 are bad at.
    times = [
        [10, 10, 90, 90],
        [12, 12, 85, 85],
        [90, 90, 10, 10],
    ]

    assert greedy_selection(times, 2, penalty=100) == [1, 2]


def test_greedy_tie_goes_to_station_that_leaves_fewer_unreached():
    times = [
        [1, 1, INF],        # picked first
        [2, 2, INF],        # adds nothing
        [5, 5, 5],          # reaches the emergency nobody else can
    ]

    # penalty equals 5, so by cost alone stations 1 and 2 tie
    chosen = greedy_selection(times, 2, penalty=5)

    assert chosen == [0, 2]


def test_greedy_never_picks_the_same_station_twice():
    times = [[i, 10 - i, 5] for i in range(6)]

    chosen = greedy_selection(times, 6, penalty=100)

    assert sorted(chosen) == list(range(6))


def small_map_table(length=9, scenarios=200, seed=3):
    """Response times for a station at every junction of a straight road."""

    graph = make_long_line(length)
    stations = prepare_stations(graph, every_junction(graph))
    emergencies = sample_emergencies(stations, scenarios, seed=seed)
    times = response_table(stations, emergencies, speed=20)

    return times, unreachable_penalty(times)


def test_exhaustive_is_at_least_as_good_as_greedy():
    # exhaustive tries every set, so it can never lose to greedy
    times, penalty = small_map_table()

    for count in (1, 2, 3):
        greedy = greedy_selection(times, count, penalty)
        best = exhaustive_selection(times, count, penalty)

        assert set_score(times, best, penalty) <= set_score(times, greedy, penalty)


def test_plain_greedy_can_miss_the_best_pair():
    # The reason swapping exists. On a straight road the best single
    # station is in the middle, but the best PAIR is not built around it.
    times, penalty = small_map_table()

    greedy = greedy_selection(times, 2, penalty)
    best = exhaustive_selection(times, 2, penalty)

    assert set_score(times, greedy, penalty) > set_score(times, best, penalty)


def test_swapping_never_makes_a_set_worse():
    times, penalty = small_map_table()

    for count in (1, 2, 3, 4):
        greedy = greedy_selection(times, count, penalty)
        refined = refine_by_swapping(times, greedy, penalty)

        assert set_score(times, refined, penalty) <= set_score(times, greedy, penalty)
        assert len(refined) == len(set(refined)) == count


def test_swapping_finds_the_best_pair_that_greedy_missed():
    times, penalty = small_map_table()

    refined = refine_by_swapping(
        times, greedy_selection(times, 2, penalty), penalty
    )
    best = exhaustive_selection(times, 2, penalty)

    assert set_score(times, refined, penalty) == set_score(times, best, penalty)


def test_greedy_plus_swapping_matches_the_true_optimum_on_a_small_map():
    times, penalty = small_map_table()

    for count in (1, 2, 3):
        refined = refine_by_swapping(
            times, greedy_selection(times, count, penalty), penalty
        )
        best = exhaustive_selection(times, count, penalty)

        assert set_score(times, refined, penalty) == set_score(times, best, penalty)


def test_ordering_keeps_the_same_stations():
    times, penalty = small_map_table()

    members = refine_by_swapping(
        times, greedy_selection(times, 3, penalty), penalty
    )
    ordered = order_by_contribution(times, members, penalty)

    assert sorted(ordered) == sorted(members)
    # first = best of them on its own
    assert set_score(times, ordered[:1], penalty) == min(
        set_score(times, [m], penalty) for m in members
    )


# ------------------------------------------------------------
# optimise_station_set on a real (tiny) road network
# ------------------------------------------------------------

def test_one_station_matches_the_single_station_ranking():
    graph = make_long_line(9)
    candidates = every_junction(graph)

    single = optimise_station_locations(graph, candidates, scenarios=100, seed=4)
    group = optimise_station_set(graph, candidates, 1, scenarios=100, seed=4)

    best = single[0]
    result = group["result"]

    assert group["stations"][0]["node"] == best["node"]
    assert result["average_seconds"] == pytest.approx(best["average_seconds"])
    assert result["worst_seconds"] == pytest.approx(best["worst_seconds"])
    assert result["within_target_percent"] == best["within_target_percent"]
    assert result["unreachable"] == best["unreachable"]
    assert result["ranking_seconds"] == pytest.approx(best["ranking_seconds"])


def test_more_stations_give_faster_and_wider_coverage():
    graph = make_long_line(9)
    candidates = every_junction(graph)

    averages = []
    coverage = []

    for count in (1, 2, 3, 4):
        group = optimise_station_set(
            graph, candidates, count, scenarios=300, seed=1, target_seconds=30
        )
        averages.append(group["result"]["average_seconds"])
        coverage.append(group["result"]["within_target_percent"])

    assert averages == sorted(averages, reverse=True)
    assert coverage == sorted(coverage)
    assert averages[-1] < averages[0]
    assert coverage[-1] > coverage[0]


def test_each_pick_reports_the_whole_set_so_far():
    graph = make_long_line(9)

    group = optimise_station_set(
        graph, every_junction(graph), 3, scenarios=200, seed=2
    )

    picks = group["stations"]

    assert [pick["order"] for pick in picks] == [1, 2, 3]
    assert len({pick["node"] for pick in picks}) == 3

    averages = [pick["average_seconds_after"] for pick in picks]
    assert averages == sorted(averages, reverse=True)

    # the last pick describes the finished set
    assert averages[-1] == pytest.approx(group["result"]["average_seconds"])


def test_two_stations_spread_out_along_a_road():
    graph = make_long_line(9)

    group = optimise_station_set(
        graph, every_junction(graph), 2, scenarios=400, seed=1
    )

    nodes = sorted(pick["node"] for pick in group["stations"])

    # one in each half of the road, not two neighbours in the middle
    assert nodes[0] <= 4 < nodes[1]
    assert nodes[1] - nodes[0] >= 3


def test_chosen_set_beats_picking_the_top_stations_one_by_one():
    # On a straight road the best individual stations are all in the
    # middle, bunched together. A good SET spreads out instead.
    graph = make_long_line(9)

    group = optimise_station_set(
        graph, every_junction(graph), 2, scenarios=400, seed=1
    )

    baselines = {b["name"]: b for b in group["baselines"]}

    assert (
        group["result"]["average_seconds"]
        < baselines["top_individual"]["average_seconds"]
    )
    assert (
        group["result"]["average_seconds"]
        < baselines["best_single"]["average_seconds"]
    )
    assert (
        group["result"]["average_seconds"]
        < baselines["random"]["average_seconds"]
    )


def test_chosen_set_is_as_good_as_trying_every_possible_set():
    graph = make_long_line(9)
    candidates = every_junction(graph)

    group = optimise_station_set(graph, candidates, 2, scenarios=200, seed=3)

    stations = prepare_stations(graph, candidates)
    emergencies = sample_emergencies(stations, 200, seed=3)
    times = response_table(stations, emergencies, speed=20)
    penalty = unreachable_penalty(times)

    best = measure_set(
        times, exhaustive_selection(times, 2, penalty), penalty, 480
    )

    assert group["result"]["ranking_seconds"] == pytest.approx(
        best["ranking_seconds"]
    )


def test_best_single_baseline_is_the_best_single_station_overall():
    graph = make_long_line(9)
    candidates = every_junction(graph)

    group = optimise_station_set(graph, candidates, 3, scenarios=200, seed=5)
    single = optimise_station_locations(graph, candidates, scenarios=200, seed=5)

    baseline = {b["name"]: b for b in group["baselines"]}["best_single"]

    assert baseline["average_seconds"] == pytest.approx(
        single[0]["average_seconds"]
    )


def test_baselines_are_labelled_and_complete():
    graph = make_long_line(9)

    group = optimise_station_set(
        graph, every_junction(graph), 3, scenarios=100, seed=1
    )

    names = [baseline["name"] for baseline in group["baselines"]]

    assert names == ["best_single", "top_individual", "random"]

    for baseline in group["baselines"]:
        for field in (
            "label", "stations", "average_seconds", "worst_seconds",
            "within_target_percent", "unreachable", "ranking_seconds",
        ):
            assert field in baseline

    sizes = {b["name"]: b["stations"] for b in group["baselines"]}
    assert sizes == {"best_single": 1, "top_individual": 3, "random": 3}


def test_same_seed_gives_identical_answers():
    graph = make_long_line(9)
    candidates = every_junction(graph)

    first = optimise_station_set(graph, candidates, 3, scenarios=150, seed=7)
    second = optimise_station_set(graph, candidates, 3, scenarios=150, seed=7)

    assert first == second


def test_random_baseline_is_repeatable():
    times = [[i + j for j in range(5)] for i in range(6)]

    first = random_baseline(times, 2, 100, 480, seed=1, trials=20)
    second = random_baseline(times, 2, 100, 480, seed=1, trials=20)

    assert first == second


def test_candidates_on_the_same_junction_count_once():
    graph = make_long_line(9)
    point = point_of(graph, 5)

    group = optimise_station_set(
        graph, [point, point, point_of(graph, 2)], 2, scenarios=50
    )

    assert group["candidates_considered"] == 2


def test_asking_for_every_candidate_works():
    graph = make_long_line(5)

    group = optimise_station_set(graph, every_junction(graph), 5, scenarios=50)

    assert len(group["stations"]) == 5
    # a station everywhere: nobody is further than the station they stand on
    assert group["result"]["worst_seconds"] == 0


def test_one_scenario_still_works():
    graph = make_long_line(5)

    group = optimise_station_set(graph, every_junction(graph), 2, scenarios=1)

    assert group["result"]["unreachable"] == 0
    assert group["result"]["average_seconds"] is not None


# ------------------------------------------------------------
# Closed roads and unreachable emergencies
# ------------------------------------------------------------

def test_a_closed_road_splits_the_map_and_two_stations_cover_both_sides():
    graph = make_long_line(9)
    candidates = every_junction(graph)
    closed = {get_segment(graph, 4, 5)}

    one = optimise_station_set(
        graph, candidates, 1, scenarios=300, seed=1, blocked_segments=closed
    )
    two = optimise_station_set(
        graph, candidates, 2, scenarios=300, seed=1, blocked_segments=closed
    )

    # one station is cut off from the other half of the road
    assert one["result"]["unreachable"] > 0

    # two stations, one each side, reach everything
    assert two["result"]["unreachable"] == 0

    nodes = sorted(pick["node"] for pick in two["stations"])
    assert nodes[0] <= 4 < nodes[1]


def test_closures_make_responses_slower():
    graph = make_diamond_graph()
    candidates = [point_of(graph, "A"), point_of(graph, "D")]

    open_roads = optimise_station_set(graph, candidates, 1, scenarios=100)
    closed = optimise_station_set(
        graph, candidates, 1, scenarios=100,
        blocked_segments={get_segment(graph, "A", "B")}
    )

    assert (
        closed["result"]["average_seconds"]
        >= open_roads["result"]["average_seconds"]
    )


def test_one_way_pocket_nobody_can_enter_is_reported_as_unreachable():
    # Junctions 1-10 form a two-way road. Junction 0 has a one-way road
    # INTO junction 1: a station at 0 can reach everything, but nothing
    # on the main road can get back to 0.
    graph = make_long_line(10)
    graph.add_node(0, 52.63, 1.29)
    graph.add_edge(0, 1, 500)

    main_road = [point_of(graph, node) for node in range(2, 11)]

    group = optimise_station_set(graph, main_road, 2, scenarios=300, seed=1)

    # junction 0 is only reachable from a station at 0, which was not offered
    # as a candidate, so it never even enters the emergency pool
    assert group["result"]["unreachable"] == 0

    with_pocket = optimise_station_set(
        graph, main_road + [point_of(graph, 0)], 2, scenarios=300, seed=1
    )

    # The pocket junction is now a possible emergency location, and only the
    # station there can reach it. If that station is not picked, those
    # emergencies are counted as unreachable instead of being ignored.
    picked = {pick["node"] for pick in with_pocket["stations"]}
    unreachable = with_pocket["result"]["unreachable"]

    if 0 in picked:
        assert unreachable == 0
    else:
        assert unreachable > 0


# ------------------------------------------------------------
# Invalid input
# ------------------------------------------------------------

def test_no_candidates_is_an_error():
    graph = make_long_line(5)

    with pytest.raises(ValueError):
        optimise_station_set(graph, [], 1)


@pytest.mark.parametrize("count", [0, -1])
def test_zero_or_negative_station_count_is_an_error(count):
    graph = make_long_line(5)

    with pytest.raises(ValueError):
        optimise_station_set(graph, every_junction(graph), count)


def test_more_stations_than_candidates_is_an_error():
    graph = make_long_line(5)
    candidates = [point_of(graph, 1), point_of(graph, 3)]

    with pytest.raises(ValueError, match="only 2 distinct"):
        optimise_station_set(graph, candidates, 3)


def test_duplicate_candidates_do_not_count_towards_the_station_total():
    graph = make_long_line(5)
    candidates = [point_of(graph, 1), point_of(graph, 1), point_of(graph, 3)]

    with pytest.raises(ValueError):
        optimise_station_set(graph, candidates, 3)


# Regression: these used to be accepted (or crash with ZeroDivisionError)
@pytest.mark.parametrize(
    "settings",
    [
        {"speed": 0},
        {"speed": -20},
        {"speed": float("nan")},
        {"speed": float("inf")},
        {"target_seconds": 0},
        {"target_seconds": -5},
        {"target_seconds": float("nan")},
        {"scenarios": 0},
    ],
)
def test_nonsense_settings_are_rejected_for_one_and_many_stations(settings):
    graph = make_long_line(5)
    candidates = every_junction(graph)

    with pytest.raises(ValueError):
        optimise_station_locations(graph, candidates, **settings)

    with pytest.raises(ValueError):
        optimise_station_set(graph, candidates, 2, **settings)


def test_check_settings_accepts_normal_values():
    check_settings(scenarios=1, target_seconds=480, speed=20)


# ------------------------------------------------------------
# The plain-English explanation
# ------------------------------------------------------------

def test_explanation_reports_real_numbers():
    graph = make_long_line(9)

    group = optimise_station_set(
        graph, every_junction(graph), 2, scenarios=300, seed=1,
        target_seconds=30
    )

    text = " ".join(explain_station_set(group, 300, 30))

    assert "2 stations together" in text
    assert "best single station" in text
    assert "within the 0.5 min target" in text


def test_explanation_for_one_station_says_so():
    graph = make_long_line(5)

    group = optimise_station_set(graph, every_junction(graph), 1, scenarios=50)

    sentences = explain_station_set(group, 50, 480)

    assert "One station was requested" in sentences[0]


def test_explanation_admits_when_a_baseline_is_as_good():
    outcome = {
        "count": 2,
        "result": {
            "average_seconds": 100.0, "worst_seconds": 200.0,
            "within_target_percent": 90.0, "unreachable": 0,
        },
        "baselines": [
            {"name": "best_single", "average_seconds": 150.0,
             "worst_seconds": 250.0, "within_target_percent": 80.0},
            {"name": "top_individual", "average_seconds": 100.2,
             "worst_seconds": 200.0, "within_target_percent": 90.0},
            {"name": "random", "average_seconds": 90.0,
             "worst_seconds": 210.0, "within_target_percent": 95.0},
        ],
    }

    text = " ".join(explain_station_set(outcome, 100, 480))

    assert "33% faster than the best single station" in text
    assert "about the same as the top-ranked stations" in text
    # random did better here, and the explanation must not hide that
    assert "random stations did better" in text


def test_explanation_mentions_unreachable_emergencies():
    outcome = {
        "count": 2,
        "result": {
            "average_seconds": 100.0, "worst_seconds": 200.0,
            "within_target_percent": 90.0, "unreachable": 3,
        },
        "baselines": [
            {"name": "best_single", "average_seconds": 150.0,
             "worst_seconds": 250.0, "within_target_percent": 80.0},
            {"name": "top_individual", "average_seconds": 120.0,
             "worst_seconds": 200.0, "within_target_percent": 90.0},
            {"name": "random", "average_seconds": 130.0,
             "worst_seconds": 210.0, "within_target_percent": 85.0},
        ],
    }

    text = " ".join(explain_station_set(outcome, 100, 480))

    assert "3 of the 100 emergencies could not be reached" in text


def test_explanation_when_nothing_can_be_reached():
    outcome = {
        "count": 1,
        "result": {
            "average_seconds": None, "worst_seconds": None,
            "within_target_percent": 0, "unreachable": 5,
        },
        "baselines": [],
    }

    assert "could not reach any" in explain_station_set(outcome, 5, 480)[0]
