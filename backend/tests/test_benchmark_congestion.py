import random

from backend.routing.a_star import CONGESTION_PENALTY_METRES, a_star, get_segment
from backend.routing.benchmark_congestion import (
    HEAVY_VEHICLES,
    build_rush_hour_traffic,
    compare_journey,
    compare_journeys,
    describe_route,
    format_report,
    describe_traffic,
    make_journeys,
    summarise,
)
from backend.routing.graph import Graph
from backend.simulation.simulation import Simulation
from backend.tests.helpers import make_diamond_graph

# The diamond map: A-B-D is the short way (680 m), A-C-D the long way (1200 m).


def traffic_on_short_way(graph, vehicles):
    """`vehicles` cars sitting on the road A-B."""
    return {get_segment(graph, "A", "B"): vehicles}


def test_without_traffic_both_approaches_choose_the_same_route():
    graph = make_diamond_graph()

    row = compare_journey(graph, "A", "D", traffic={})

    assert not row["changed"]
    assert row["shortest"]["distance"] == row["aware"]["distance"] == 680


def test_heavy_traffic_makes_congestion_aware_routing_take_the_longer_road():
    graph = make_diamond_graph()
    traffic = traffic_on_short_way(graph, 20)   # penalty 20 * 50 = 1000 m

    row = compare_journey(graph, "A", "D", traffic)

    assert row["changed"]
    assert row["shortest"]["distance"] == 680
    assert row["aware"]["distance"] == 1200        # longer...
    assert row["shortest"]["vehicles"] == 20
    assert row["aware"]["vehicles"] == 0           # ...but no traffic
    assert row["aware"]["cost"] < row["shortest"]["cost"]


def test_light_traffic_is_not_worth_a_detour():
    graph = make_diamond_graph()
    traffic = traffic_on_short_way(graph, 3)    # penalty 150 m: 830 < 1200

    row = compare_journey(graph, "A", "D", traffic)

    assert not row["changed"]


def test_measured_cost_matches_what_the_router_minimises():
    graph = make_diamond_graph()
    traffic = traffic_on_short_way(graph, 20)

    path, router_cost = a_star(graph, "A", "D", traffic=traffic)
    measured = describe_route(graph, path, traffic)

    assert measured["cost"] == router_cost
    assert CONGESTION_PENALTY_METRES == 50   # the 20-vehicle numbers above rely on it


def test_summary_and_report():
    graph = make_diamond_graph()
    traffic = traffic_on_short_way(graph, 20)

    # One journey that meets the traffic, one that does not
    rows = compare_journeys(graph, [("A", "D"), ("D", "C")], traffic)
    summary = summarise(rows)

    assert summary["journeys"] == 2
    assert summary["changed"] == 1
    assert summary["changed_extra_metres"] == 520
    assert summary["changed_vehicles_before"] == 20
    assert summary["changed_vehicles_after"] == 0
    assert summary["sanity_failures"] == 0

    report = format_report(
        summary,
        describe_traffic(traffic),
        {"vehicles": 20, "journeys": 2, "warmup": 0, "seed": 1},
        {"nodes": 4, "edges": 8},
    )

    assert "Congestion-aware" in report
    assert "1 of 2" in report
    assert "Sanity check passed" in report


# ------------------------------------------------------------
# Journeys with no route, and what the report says about them
# ------------------------------------------------------------

def make_one_way_graph():
    """A -> B only. There is no road back from B to A."""
    graph = Graph()
    graph.add_node("A", 52.6300, 1.2900)
    graph.add_node("B", 52.6300, 1.2950)
    graph.add_edge("A", "B", 340)
    return graph


def test_a_journey_with_no_route_is_skipped_not_measured():
    graph = make_one_way_graph()

    assert compare_journey(graph, "B", "A", traffic={}) is None

    rows = compare_journeys(graph, [("A", "B"), ("B", "A")], traffic={})

    assert len(rows) == 1


def test_report_says_how_many_journeys_were_skipped():
    graph = make_one_way_graph()
    rows = compare_journeys(graph, [("A", "B"), ("B", "A")], traffic={})

    report = format_report(
        summarise(rows),
        describe_traffic({}),
        {"vehicles": 0, "journeys": 2, "warmup": 0, "seed": 1},
        {"nodes": 2, "edges": 1},
    )

    assert "1 measured, from 2 random" in report
    assert "1 had no route and were skipped" in report


def test_report_does_not_mention_skipping_when_nothing_was_skipped():
    graph = make_diamond_graph()
    rows = compare_journeys(graph, [("A", "D")], traffic={})

    report = format_report(
        summarise(rows),
        describe_traffic({}),
        {"vehicles": 0, "journeys": 1, "warmup": 0, "seed": 1},
        {"nodes": 4, "edges": 8},
    )

    assert "skipped" not in report


# ------------------------------------------------------------
# Measuring consistently
# ------------------------------------------------------------

def test_parallel_roads_are_measured_by_the_one_the_router_uses():
    # Real maps can have two roads between the same pair of junctions
    graph = Graph()
    graph.add_node("A", 52.6300, 1.2900)
    graph.add_node("B", 52.6300, 1.2950)
    graph.add_edge("A", "B", 100)
    graph.add_edge("A", "B", 80)

    row = compare_journey(graph, "A", "B", traffic={})

    assert row["shortest"]["distance"] == 80
    assert row["aware"]["distance"] == 80


def test_heavy_road_threshold_matches_the_live_congestion_map():
    # The benchmark's "heavy" threshold is copied from the simulation,
    # so this fails if the two ever drift apart.
    route = [
        {"latitude": 52.6300, "longitude": 1.2900},
        {"latitude": 52.6300, "longitude": 1.2950},
    ]

    def level_with(vehicles):
        simulation = Simulation(route)

        for _ in range(vehicles - 1):
            simulation.add_vehicle(route)

        return list(simulation.get_congestion().values())[0]["level"]

    assert level_with(HEAVY_VEHICLES) == "heavy"
    assert level_with(HEAVY_VEHICLES - 1) != "heavy"


def test_describe_traffic_counts_vehicles_still_driving():
    traffic = {("a", "b"): 2, ("b", "c"): 3}

    stats = describe_traffic(traffic)

    assert stats["vehicles_on_road"] == 5
    assert stats["roads"] == 2
    assert stats["busiest"] == 3


# ------------------------------------------------------------
# Same seed, same benchmark
# ------------------------------------------------------------

def make_long_road(junctions=40):
    """A straight two-way road through the city centre (about 34 m per link)."""
    graph = Graph()

    for index in range(junctions):
        graph.add_node(index, 52.6309, 1.2974 + (index - junctions // 2) * 0.0005)

    for index in range(junctions - 1):
        graph.add_edge(index, index + 1, 34)
        graph.add_edge(index + 1, index, 34)

    return graph


def test_same_seed_builds_the_same_traffic_and_journeys():
    graph = make_long_road()

    traffic_one = build_rush_hour_traffic(graph, 5, 3, random.Random(7))
    traffic_two = build_rush_hour_traffic(graph, 5, 3, random.Random(7))

    assert traffic_one                      # not an empty comparison
    assert traffic_one == traffic_two

    journeys_one = make_journeys(graph, 10, random.Random(8))
    journeys_two = make_journeys(graph, 10, random.Random(8))

    assert journeys_one == journeys_two
