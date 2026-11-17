"""Testing the optimiser's answers in traffic (optimiser/evaluation.py):
the statistics, the fairness of the scenarios, and the report."""

import math

import pytest

from backend.optimiser.evaluation import (
    build_specs,
    candidates_to_test,
    kendall_tau,
    mean_interval,
    percentile,
    summarise,
)
from backend.tests.helpers import make_grid_graph


def settings(**overrides):
    base = {
        "kind": "ranking", "stations": 2, "top": 3, "service": "ambulance",
        "distribution": "uniform", "target_seconds": 300.0, "speed": 20.0,
        "optimiser_scenarios": 60, "optimiser_seed": 1,
        "demand": [40, 80], "seeds": [1, 2, 3], "emergencies": 4,
        "first_emergency_at": 30, "emergency_interval_seconds": 20,
        "vehicles_per_station": 1, "spread_seconds": 60, "max_seconds": 900,
    }
    base.update(overrides)
    return base


# ------------------------------------------------------------
# Statistics
# ------------------------------------------------------------

def test_kendall_tau():
    assert kendall_tau([1, 2, 3, 4], [1, 2, 3, 4]) == 1
    assert kendall_tau([1, 2, 3, 4], [4, 3, 2, 1]) == -1
    assert kendall_tau([1, 2, 3], [2, 1, 3]) == pytest.approx(1 / 3)


def test_confidence_intervals_use_students_t():
    mean, half = mean_interval([10, 12, 14])
    assert mean == 12
    # t(2) = 4.303, sd = 2, n = 3
    assert half == pytest.approx(4.303 * 2 / math.sqrt(3))
    assert mean_interval([5]) == (5, None)
    assert mean_interval([]) == (None, None)
    assert mean_interval([3, None, 5])[0] == 4


def test_percentile():
    assert percentile(list(range(1, 11)), 0.9) == 9
    assert percentile([], 0.9) is None


# ------------------------------------------------------------
# The scenarios
# ------------------------------------------------------------

def test_every_candidate_faces_the_same_trips_and_emergencies():
    graph = make_grid_graph(size=8, seed=7)
    chosen = candidates_to_test(graph, settings())
    specs, plan = build_specs(settings(), chosen)

    assert len(chosen) == 3
    assert len(specs) == len(plan) == 2 * 3 * (3 + 1)
    for vehicles in (40, 80):
        for seed in (1, 2, 3):
            group = [s for s, p in zip(specs, plan) if (p["vehicles"], p["seed"]) == (vehicles, seed)]
            assert len({s.trip_signature() for s in group}) == 1
            with_stations = [s for s in group if s.stations]
            # Same emergencies (timing, service, distribution); only the stations differ
            assert len({s.emergencies.model_dump_json() for s in with_stations}) == 1
            assert len({tuple((st.latitude, st.longitude) for st in s.stations) for s in with_stations}) == 3
            assert sum(1 for s in group if not s.stations) == 1
    assert all(s.traffic_model == "osm" for s in specs)


def test_a_station_set_is_compared_with_the_top_sites_and_the_best_single_site():
    graph = make_grid_graph(size=8, seed=7)
    chosen = candidates_to_test(graph, settings(kind="station_set", stations=2))

    assert [c["label"] for c in chosen] == [
        "Optimised 2 stations", "Top 2 ranked one by one", "Best single station",
    ]
    assert [len(c["stations"]) for c in chosen] == [2, 2, 1]
    assert all(c["estimate"]["mean_response_seconds"] > 0 for c in chosen)


# ------------------------------------------------------------
# The report
# ------------------------------------------------------------

def fake_result(responses, delay, requested=4, target=300, wait=None):
    reached = [r for r in responses if r is not None]
    return {
        "summary": {"total_delay_vehicle_hours": delay},
        "emergencies": {
            "requested": requested,
            "dispatched": len(reached),
            "mean_response_seconds": sum(reached) / len(reached) if reached else None,
            "within_target_percent": 100 * sum(r <= target for r in reached) / requested,
            "detail": {"responses": responses, "mean_wait_seconds": wait},
        },
    }


def test_the_report_ranks_by_simulation_and_shows_where_the_estimate_was_wrong():
    s = settings(demand=[100], seeds=[1, 2])
    candidates = [
        {"label": "A", "stations": [], "estimate": {"mean_response_seconds": 100, "worst_response_seconds": 200, "within_target_percent": 100}},
        {"label": "B", "stations": [], "estimate": {"mean_response_seconds": 120, "worst_response_seconds": 220, "within_target_percent": 100}},
    ]
    _, plan = build_specs(s, [dict(c, stations=[{"latitude": 52.62, "longitude": 1.28}]) for c in candidates])
    results = []
    for p in plan:
        if p["candidate"] is None:
            results.append(fake_result([], 1.0))
        elif p["candidate"] == 0:     # A: best estimate, but slow in traffic
            results.append(fake_result([300, 320, 340, None], 1.6))
        else:                          # B: answers faster in traffic
            results.append(fake_result([150, 160, 170, 180], 1.2))

    report = summarise(s, candidates, plan, results)

    assert report["complete"]
    assert [c["label"] for c in report["candidates"]] == ["B", "A"]
    assert report["rank_agreement_kendall_tau"] == -1
    a = next(c for c in report["candidates"] if c["label"] == "A")
    assert a["estimated_rank"] == 1 and a["simulated_rank"] == 2
    assert a["simulated_mean_response_seconds"] == pytest.approx(320)
    assert a["estimate_gap_percent"] == pytest.approx(220)
    demand = a["by_demand"][0]
    assert demand["unreached"] == 2
    assert demand["p90_response_seconds"] == 340
    assert demand["added_delay_vehicle_hours"]["mean"] == pytest.approx(0.6)
    assert demand["pooled_within_target_percent"] == pytest.approx(100 * 2 / 8)
    assert any("in a different order from" in line for line in report["explanation"])


def test_an_unfinished_evaluation_reports_progress_only():
    s = settings(demand=[100], seeds=[1])
    candidates = [{"label": "A", "stations": [{"latitude": 52.62, "longitude": 1.28}],
                   "estimate": {"mean_response_seconds": 100, "worst_response_seconds": 1, "within_target_percent": 1}}]
    _, plan = build_specs(s, candidates)
    report = summarise(s, candidates, plan, [None] * len(plan))

    assert (report["complete"], report["scenarios_done"], report["explanation"]) == (False, 0, [])


def test_the_report_splits_waiting_from_driving():
    s = settings(demand=[100], seeds=[1, 2])
    candidates = [{"label": "A", "stations": [{"latitude": 52.62, "longitude": 1.28}],
                   "estimate": {"mean_response_seconds": 100, "worst_response_seconds": 1, "within_target_percent": 1}}]
    _, plan = build_specs(s, candidates)
    results = [
        fake_result([], 1.0) if p["candidate"] is None
        else fake_result([200, 300, 400, 500], 1.0 + (0.01 if p["seed"] == 1 else -0.01), wait=150)
        for p in plan
    ]

    report = summarise(s, candidates, plan, results)
    row = report["candidates"][0]

    assert row["by_demand"][0]["mean_wait_seconds"] == 150
    assert row["mean_wait_seconds"] == 150
    assert row["mean_drive_seconds"] == 200
    assert any("no measurable effect on other traffic" in line for line in report["explanation"])
