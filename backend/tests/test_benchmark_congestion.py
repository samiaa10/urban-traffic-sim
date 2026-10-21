from backend.routing.a_star import CONGESTION_PENALTY_METRES, a_star, get_segment
from backend.routing.benchmark_congestion import (
    compare_journey,
    compare_journeys,
    describe_route,
    format_report,
    describe_traffic,
    summarise,
)
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
