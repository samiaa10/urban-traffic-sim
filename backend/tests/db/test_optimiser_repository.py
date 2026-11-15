"""Optimiser results stored as rows, and rebuilt into the exact answer.

Uses the real optimiser on a small grid map, so the stored answer has the
same shape and number types as the API's.
"""

import json
import uuid

import pytest

from backend.db.models import CandidateSource, OptimiserKind
from backend.db.repository import OptimiserRepository, optimiser_request_key
from backend.db.serializers import optimiser_response, optimiser_summary
from backend.optimiser.station_optimiser import (
    explain_best,
    explain_station_set,
    optimise_station_locations,
    optimise_station_set,
)
from backend.tests.helpers import make_grid_graph


GRAPH = "b" * 64


@pytest.fixture(scope="module")
def grid():
    return make_grid_graph(size=6, seed=3)


def candidate_points(graph, nodes):
    return [(graph.nodes[n]["latitude"], graph.nodes[n]["longitude"]) for n in nodes]


def ranking_answer(graph, closed=frozenset()):
    """What GET /optimiser/stations answers."""

    scenarios, seed, target, speed = 60, 4, 480.0, 20.0
    results = optimise_station_locations(
        graph, candidate_points(graph, [0, 14, 21, 35]),
        scenarios=scenarios, seed=seed, target_seconds=target, speed=speed,
        blocked_segments=set(closed),
    )
    return {
        "scenarios": scenarios, "seed": seed, "target_seconds": target,
        "speed": speed, "closed_roads": len(closed),
        "explanation": explain_best(results, scenarios, target),
        "results": results,
    }


def station_set_answer(graph):
    """What GET /optimiser/station-set answers."""

    scenarios, seed, target, speed = 60, 4, 300.0, 20.0
    outcome = optimise_station_set(
        graph, candidate_points(graph, [0, 5, 14, 21, 30, 35]), 2,
        scenarios=scenarios, seed=seed, target_seconds=target, speed=speed,
        blocked_segments=set(),
    )
    return {
        "scenarios": scenarios, "seed": seed, "target_seconds": target,
        "speed": speed, "closed_roads": 0,
        "explanation": explain_station_set(outcome, scenarios, target),
        **outcome,
    }


def settings(**overrides):
    values = dict(
        optimiser_run_id=uuid.uuid4(),
        graph_sha256=GRAPH,
        request_key="r" * 64,
        candidate_source="custom",
        candidates_considered=4,
        compute_ms=12.5,
    )
    values.update(overrides)
    return values


def as_json(body):
    return json.dumps(body, sort_keys=True)


def test_a_ranking_comes_back_exactly_as_it_was_answered(session, grid):
    answer = ranking_answer(grid)
    repo = OptimiserRepository(session)

    saved = repo.save_ranking(answer, **settings())
    session.expire_all()                 # read it back from the database

    stored = repo.get(saved.id)

    assert stored.kind == OptimiserKind.RANKING
    assert [c.rank for c in stored.candidates] == [1, 2, 3, 4]
    # Same JSON text, so ints stay ints and floats stay floats
    assert as_json(optimiser_response(stored)) == as_json(answer)


def test_a_station_set_comes_back_exactly_as_it_was_answered(session, grid):
    answer = station_set_answer(grid)
    repo = OptimiserRepository(session)

    saved = repo.save_station_set(answer, **settings(candidates_considered=6))
    session.expire_all()

    stored = repo.get(saved.id)

    assert stored.station_count == 2
    assert [s.pick_order for s in stored.selected_stations] == [1, 2]
    assert [b.name for b in stored.baselines] == ["best_single", "top_individual", "random"]
    assert as_json(optimiser_response(stored)) == as_json(answer)


def test_optimisations_are_listed_newest_first_and_by_kind(session, grid):
    repo = OptimiserRepository(session)
    repo.save_ranking(ranking_answer(grid), **settings())
    repo.save_station_set(station_set_answer(grid), **settings())

    everything, total = repo.list()
    sets, set_total = repo.list(kind="station_set")

    assert total >= 2
    assert set_total >= 1 and all(r.kind == OptimiserKind.STATION_SET for r in sets)
    assert everything[0].created_at >= everything[-1].created_at

    summary = optimiser_summary(everything[0])
    assert summary["candidate_source"] == CandidateSource.CUSTOM.value
    assert summary["compute_ms"] == 12.5


def test_the_newest_answer_to_an_identical_request_can_be_found(session, grid):
    repo = OptimiserRepository(session)
    key = optimiser_request_key("ranking", GRAPH, [(1, 2)], [], 60, 4, 480, 20)

    assert repo.latest_for_key(key) is None

    saved = repo.save_ranking(ranking_answer(grid), **settings(request_key=key))

    assert repo.latest_for_key(key).id == saved.id


def test_request_keys_change_with_anything_the_answer_depends_on():
    base = dict(
        kind="ranking", graph_sha256=GRAPH, candidates=[(52.6, 1.2), (52.7, 1.3)],
        closed_segments=[((52.6, 1.2), (52.61, 1.21))], scenarios=200, seed=1,
        target_seconds=480, speed=20,
    )
    key = optimiser_request_key(**base)

    assert optimiser_request_key(**base) == key
    # Number types do not matter
    assert optimiser_request_key(**{**base, "target_seconds": 480.0}) == key
    # Order of closed roads, and of each road's ends, does not matter
    assert optimiser_request_key(**{
        **base, "closed_segments": [((52.61, 1.21), (52.6, 1.2))]
    }) == key

    for change in (
        {"kind": "station_set", "station_count": 2},
        {"graph_sha256": "c" * 64},
        {"candidates": [(52.7, 1.3), (52.6, 1.2)]},
        {"closed_segments": []},
        {"scenarios": 201},
        {"seed": 2},
        {"target_seconds": 300},
        {"speed": 15},
        {"version": 999},                  # the optimiser itself changed
    ):
        assert optimiser_request_key(**{**base, **change}) != key, change


def test_settings_the_api_would_refuse_are_refused_by_the_database(session, grid):
    from sqlalchemy.exc import IntegrityError

    repo = OptimiserRepository(session)
    answer = {**ranking_answer(grid), "scenarios": 0}

    with pytest.raises(IntegrityError):
        with session.begin_nested():
            repo.save_ranking(answer, **settings())
