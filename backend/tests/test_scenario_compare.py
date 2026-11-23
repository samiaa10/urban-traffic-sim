"""Comparing, ranking and explaining scenarios (backend/scenarios/compare.py).

Uses real results from run_scenario on a grid map, plus a few hand-made
results where exact numbers matter.
"""

import pytest

from backend.scenarios.compare import (
    METRICS,
    compare,
    explain,
    metric_values,
    paired,
    rank,
    road_changes,
    spec_differences,
)
from backend.scenarios.runner import World, run_scenario
from backend.scenarios.spec import ScenarioSpec
from backend.tests.helpers import make_grid_graph


@pytest.fixture(scope="module")
def world():
    return World(make_grid_graph(size=8, seed=7))


def item(world, name, **overrides):
    body = {"name": name, "seed": 4, "demand": {"vehicles": 60}}
    body.update(overrides)
    spec = ScenarioSpec.model_validate(body)
    return {
        "name": name,
        "spec": spec.model_dump(mode="json"),
        "result": run_scenario(world, spec),
    }


# Closing every road between rows 3 and 4 of the grid but one
CUT = [{"start": 3 * 8 + c, "destination": 4 * 8 + c} for c in range(1, 8)]


@pytest.fixture(scope="module")
def baseline(world):
    return item(world, "Baseline")


@pytest.fixture(scope="module")
def cut(world):
    return item(world, "Network cut", closures=CUT)


def fake(name, delay, travel=10.0, emergency=None, signature="s"):
    """A minimal result with chosen numbers."""

    return {
        "name": name,
        "spec": {"seed": 1, "demand": {"vehicles": 1}},
        "result": {
            "trip_signature": signature,
            "summary": {
                "total_delay_vehicle_hours": delay,
                "total_travel_vehicle_hours": travel,
                "completion_percent": 100.0,
            },
            "emergencies": {"mean_response_seconds": emergency},
            "trips": {"origin": [], "destination": [], "status": [], "journey_seconds": []},
            "roads": [],
        },
    }


# ------------------------------------------------------------
# Ranking
# ------------------------------------------------------------

def test_lower_is_better_for_delay():
    ranked = rank([fake("B", 3.0), fake("A", 1.0), fake("C", 2.0)])

    assert [r["name"] for r in ranked] == ["A", "C", "B"]
    assert [r["rank"] for r in ranked] == [1, 2, 3]
    assert ranked[0]["gap_to_best"] == 0
    assert ranked[2]["gap_to_best"] == pytest.approx(2.0)
    assert ranked[2]["gap_percent"] == pytest.approx(200.0)


def test_higher_is_better_for_coverage():
    a = fake("A", 1.0)
    b = fake("B", 1.0)
    a["result"]["summary"]["completion_percent"] = 80.0
    b["result"]["summary"]["completion_percent"] = 95.0

    assert [r["name"] for r in rank([a, b], "completion_percent")] == ["B", "A"]


def test_ties_are_broken_by_travel_time_then_name():
    ranked = rank([fake("Z", 1.0, travel=5.0), fake("Y", 1.0, travel=4.0), fake("X", 1.0, travel=5.0)])

    assert [r["name"] for r in ranked] == ["Y", "X", "Z"]


def test_scenarios_without_the_metric_come_last():
    ranked = rank(
        [fake("No emergencies", 1.0), fake("Fast", 1.0, emergency=200.0), fake("Slow", 1.0, emergency=400.0)],
        "emergency_mean_response_seconds",
    )

    assert [r["name"] for r in ranked] == ["Fast", "Slow", "No emergencies"]
    assert ranked[2]["value"] is None and ranked[2]["gap_to_best"] is None


def test_unknown_metrics_and_duplicate_names_are_refused():
    with pytest.raises(ValueError, match="metric"):
        rank([fake("A", 1.0)], "happiness")
    with pytest.raises(ValueError, match="different names"):
        rank([fake("A", 1.0), fake("A", 2.0)])


def test_every_metric_has_a_label_unit_and_direction(baseline):
    values = metric_values(baseline["result"])

    assert set(values) == set(METRICS)
    for definition in METRICS.values():
        assert definition["better"] in ("lower", "higher")
        assert definition["label"] and definition["unit"] and definition["definition"]


# ------------------------------------------------------------
# Trip by trip and road by road
# ------------------------------------------------------------

def test_the_same_trips_are_compared_trip_by_trip(baseline, cut):
    result = paired(baseline["result"], cut["result"])

    assert result["trips_compared"] == 60
    assert result["trips_slower"] + result["trips_faster"] + result["trips_unchanged"] == 60
    assert result["extra_vehicle_hours"] > 0
    assert result["worst_increase_seconds"] > 0
    assert result["mean_change_seconds"] == pytest.approx(result["extra_vehicle_hours"] * 3600 / 60)


def test_different_trips_are_not_paired(world, baseline):
    other_seed = item(world, "Other seed", seed=5)

    assert paired(baseline["result"], other_seed["result"]) is None


def test_trips_newly_cut_off_are_counted(world, baseline):
    isolated = item(world, "Corner isolated", closures=[
        {"start": 0, "destination": 1}, {"start": 0, "destination": 8},
    ])
    cut_off = isolated["result"]["trips"]["status"].count("unroutable")

    assert paired(baseline["result"], isolated["result"])["newly_cut_off"] == cut_off


def test_road_changes_find_where_the_extra_delay_is(baseline, cut):
    changes = road_changes(baseline["result"], cut["result"], limit=3)

    increases = [c["change_vehicle_seconds"] for c in changes["increases"]]
    assert increases == sorted(increases, reverse=True)
    assert all(value > 0 for value in increases)
    for change in changes["increases"] + changes["decreases"]:
        assert change["change_vehicle_seconds"] == pytest.approx(
            change["after_vehicle_seconds"] - change["before_vehicle_seconds"]
        )


# ------------------------------------------------------------
# Explanations
# ------------------------------------------------------------

def test_setup_differences_are_described():
    base = {"seed": 1, "demand": {"vehicles": 500}, "closures": [], "accidents": [], "stations": []}
    other = {
        **base,
        "closures": [{"start": 1, "destination": 2}, {"start": 3, "destination": 4}],
        "rerouting": False,
        "stations": [{"latitude": 52.6, "longitude": 1.3, "ambulances": 2}],
    }

    notes = spec_differences(base, other)

    assert "2 road closures that the other does not have" in notes
    assert "dynamic rerouting off" in notes
    assert "1 station(s) with 2 ambulance(s) instead of 0 with 0" in notes
    assert spec_differences(base, base) == []


# What the website's scenario builder sends (frontend/src/scenarios.ts, draftToSpec)
CENTRAL_BRIDGES = ["Prince of Wales Road", "Whitefriars", "Fye Bridge Street", "Duke Street"]
BUILDER = {"name": "Bridges open", "seed": 1, "demand": {"vehicles": 1000, "spread_seconds": 300},
           "rerouting": True, "traffic_model": "osm", "closures": [], "accidents": [],
           "surges": [{"latitude": 52.6286, "longitude": 1.2927, "radius_metres": 1200, "direction": "inbound",
                       "vehicles_per_minute": 60, "start_at": 60, "duration_seconds": 900}]}


def test_bridges_floods_and_signals_are_described():
    bridges = {**BUILDER, "name": "Central bridges closed", "resilience": True,
               "failures": [{"names": CENTRAL_BRIDGES, "start_at": 300, "end_at": 1500}]}
    # Seen in the app: these two were called "set up the same way apart from their names"
    assert spec_differences(BUILDER, bridges) == [
        "the bridges on Prince of Wales Road, Whitefriars, Fye Bridge Street, Duke Street out of use from minute 5 to minute 25"]
    assert spec_differences(bridges, BUILDER) == [
        "the bridges on Prince of Wales Road, Whitefriars, Fye Bridge Street, Duke Street in use throughout "
        "(the other closes them from minute 5 to minute 25)"]

    flood = {**BUILDER, "floods": [{"area": {"latitude": 52.6305, "longitude": 1.3045, "radius_metres": 350},
                                    "severity": "closed", "start_at": 300, "duration_seconds": 1200, "recovery_seconds": 600}]}
    assert spec_differences(BUILDER, flood) == ["a flood (closed) over every road within 350 m of a point, from minute 5 for 20 min"]
    assert spec_differences(flood, BUILDER) == ["no flood (closed) over every road within 350 m of a point, from minute 5 for 20 min"]

    signals = {**BUILDER, "signals": {"controller": "adaptive", "emergency_priority": True}}
    assert spec_differences(BUILDER, signals) == ["adaptive traffic signals with emergency priority instead of no traffic signals"]

    limit = {**BUILDER, "max_seconds": 1800}
    assert spec_differences(BUILDER, limit) == ["a time limit of 30 min instead of 60 min"]

    # Whether accessibility was also measured does not change a run
    assert spec_differences(BUILDER, {**BUILDER, "resilience": True}) == []


def test_any_other_difference_is_named_rather_than_called_the_same():
    assert spec_differences({"seed": 1}, {"seed": 1, "future_setting": 2}) == ["different future setting settings"]


def test_the_explanation_never_calls_different_setups_the_same(baseline):
    def as_run(name, spec):
        return {**baseline, "name": name, "spec": {**baseline["spec"], **spec, "name": name}}

    bridges = as_run("Central bridges closed", {"failures": [{"names": CENTRAL_BRIDGES, "start_at": 300, "end_at": 1500}]})
    text = " ".join(explain([as_run("Bridges open", {}), bridges]))
    assert "set up the same way" not in text
    assert "has the bridges on Prince of Wales Road" in text


def test_the_explanation_says_who_wins_by_how_much_and_why(baseline, cut):
    sentences = explain([cut, baseline])
    text = " ".join(sentences)

    assert sentences[0].startswith("Baseline ranks first on network delay")
    assert "Network cut is second" in sentences[0]
    assert "7 road closures that the other does not have" in text
    assert "Trip by trip (the same 60 completed trips in both)" in text
    assert "junction ids" in text


def test_different_trips_are_explained_as_totals_only(world, baseline):
    other_seed = item(world, "Other seed", seed=5)
    text = " ".join(explain([baseline, other_seed]))

    assert "compared on totals only" in text
    assert "different random seed" in text


def test_a_closure_that_helps_is_called_out():
    helped = fake("Closure", 1.0)
    helped["spec"]["closures"] = [{"start": 1, "destination": 2}]
    worse = fake("No closure", 2.0)
    worse["spec"]["closures"] = []

    text = " ".join(explain([helped, worse]))

    assert "Braess-like" in text


def test_one_scenario_is_not_a_comparison():
    assert explain([fake("Only", 1.0)]) == ["Run at least two scenarios to compare them."]


def test_compare_against_a_baseline(baseline, cut):
    outcome = compare([cut, baseline], baseline_name="Baseline")

    rows = {row["name"]: row for row in outcome["ranking"]}
    assert outcome["metric"]["key"] == "total_delay_vehicle_hours"
    assert rows["Baseline"]["paired"] is None
    assert rows["Baseline"]["vs_baseline"]["total_delay_vehicle_hours"] == 0
    assert rows["Network cut"]["paired"]["trips_compared"] == 60
    assert rows["Network cut"]["vs_baseline"]["total_travel_vehicle_hours"] == pytest.approx(
        rows["Network cut"]["metrics"]["total_travel_vehicle_hours"]
        - rows["Baseline"]["metrics"]["total_travel_vehicle_hours"]
    )
    with pytest.raises(ValueError):
        compare([cut, baseline], baseline_name="Nope")


def test_the_explanation_names_roads_when_the_map_has_names(baseline, cut):
    text = " ".join(explain([cut, baseline], road_name=lambda a, b: f"Road {a}-{b}"))

    assert "The roads that lost the most extra time in Network cut are Road " in text
    assert "junction ids" not in text


def test_small_delays_are_given_to_two_decimals():
    sentences = explain([fake("A", 0.712), fake("B", 0.738)])

    assert "A ranks first on network delay (0.71 vehicle-hours)" in sentences[0]
    assert "B is second (0.74 vehicle-hours, 0.03 vehicle-hours more" in sentences[0]
