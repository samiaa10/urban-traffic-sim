"""Optimiser results saved through the API, and fetched back from it."""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402


SETTINGS = {"scenarios": 40, "seed": 3}


@pytest.fixture
def app(make_app):
    return make_app()


@pytest.fixture
def client(app):
    return TestClient(app)


def candidates(app, nodes=(0, 5, 10, 15)):
    graph = app.state.context.graph
    return ";".join(
        f"{graph.nodes[n]['latitude']},{graph.nodes[n]['longitude']}" for n in nodes
    )


def without_ids(answer):
    return {k: v for k, v in answer.items() if k not in ("optimiser_run_id", "reused")}


def ranking(client, app, **params):
    response = client.get(
        "/optimiser/stations",
        params={"candidates": candidates(app), **SETTINGS, **params},
    )
    assert response.status_code == 200, response.text
    return response.json()


def station_set(client, app, **params):
    response = client.get(
        "/optimiser/station-set",
        params={"stations": 2, "candidates": candidates(app), **SETTINGS, **params},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_a_ranking_is_saved_and_comes_back_identical(client, app):
    answer = ranking(client, app)

    assert answer["reused"] is False
    saved = client.get(f"/optimiser/runs/{answer['optimiser_run_id']}").json()

    assert saved["kind"] == "ranking"
    assert saved["candidate_source"] == "custom"
    assert saved["candidates_considered"] == 4
    assert saved["compute_ms"] > 0
    assert saved["answer"] == without_ids(answer)


def test_a_station_set_is_saved_and_comes_back_identical(client, app):
    answer = station_set(client, app)

    saved = client.get(f"/optimiser/runs/{answer['optimiser_run_id']}").json()

    assert saved["kind"] == "station_set"
    assert saved["stations"] == 2
    assert saved["answer"] == without_ids(answer)


def test_the_default_candidates_are_recorded_as_such(client):
    answer = client.get("/optimiser/stations", params=SETTINGS).json()

    saved = client.get(f"/optimiser/runs/{answer['optimiser_run_id']}").json()

    assert saved["candidate_source"] == "default"


def test_an_identical_request_can_reuse_the_saved_answer(client, app):
    first = ranking(client, app)
    again = ranking(client, app, reuse=True)
    fresh = ranking(client, app)                     # reuse is opt-in

    assert again["reused"] is True
    assert again["optimiser_run_id"] == first["optimiser_run_id"]
    assert without_ids(again) == without_ids(first)
    assert fresh["reused"] is False
    assert fresh["optimiser_run_id"] != first["optimiser_run_id"]


def test_a_different_request_is_not_answered_from_a_saved_one(client, app):
    ranking(client, app)

    for change in ({"seed": 4}, {"scenarios": 41}, {"target_seconds": 300}):
        assert ranking(client, app, reuse=True, **change)["reused"] is False, change


def test_closed_roads_are_part_of_what_is_reused(client, app):
    simulation_id = client.post(
        "/simulations", params={"start": 0, "destination": 15}
    ).json()["simulation_id"]

    open_roads = ranking(client, app, simulation_id=simulation_id)
    client.post(
        f"/simulations/{simulation_id}/incidents/block",
        params={"start": 5, "destination": 6},
    )
    closed = ranking(client, app, simulation_id=simulation_id, reuse=True)

    assert closed["reused"] is False
    assert closed["closed_roads"] == 1
    assert open_roads["closed_roads"] == 0


def test_results_are_linked_to_the_run_whose_roads_they_used(client, app):
    simulation_id = client.post(
        "/simulation/start", params={"start": 0, "destination": 15}
    ).json()["simulation_id"]

    linked = ranking(client, app)                 # default run's closures
    ranking(client, app, simulation_id=simulation_id)

    listed = client.get("/optimiser/runs", params={"simulation_id": simulation_id}).json()

    assert listed["total"] == 2
    assert {r["simulation_id"] for r in listed["optimiser_runs"]} == {simulation_id}
    assert linked["optimiser_run_id"] in {r["optimiser_run_id"] for r in listed["optimiser_runs"]}


def test_results_outlive_the_run_they_used(client, app):
    simulation_id = client.post(
        "/simulations", params={"start": 0, "destination": 15}
    ).json()["simulation_id"]
    answer = ranking(client, app, simulation_id=simulation_id)

    client.delete(f"/simulations/{simulation_id}", params={"purge": True})
    saved = client.get(f"/optimiser/runs/{answer['optimiser_run_id']}").json()

    assert saved["simulation_id"] is None
    assert saved["answer"] == without_ids(answer)


def test_results_are_listed_newest_first_and_filtered_by_kind(client, app):
    first = ranking(client, app)
    second = station_set(client, app)

    everything = client.get("/optimiser/runs").json()
    sets = client.get("/optimiser/runs", params={"kind": "station_set"}).json()

    assert [r["optimiser_run_id"] for r in everything["optimiser_runs"]] == [
        second["optimiser_run_id"], first["optimiser_run_id"]
    ]
    assert [r["optimiser_run_id"] for r in sets["optimiser_runs"]] == [second["optimiser_run_id"]]
    assert client.get("/optimiser/runs", params={"kind": "x"}).status_code == 400
    assert client.get("/optimiser/runs", params={"limit": 1}).json()["total"] == 2


def test_results_survive_a_restart(make_app):
    before = TestClient(make_app())
    answer = ranking(before, before.app)

    after = TestClient(make_app())
    saved = after.get(f"/optimiser/runs/{answer['optimiser_run_id']}").json()

    assert saved["answer"] == without_ids(answer)
    assert ranking(after, after.app, reuse=True)["reused"] is True


def test_post_creates_a_saved_result(client, app):
    response = client.post(
        "/optimiser/runs",
        params={"kind": "station_set", "stations": 2, "candidates": candidates(app), **SETTINGS},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["count"] == 2
    assert client.get(f"/optimiser/runs/{body['optimiser_run_id']}").status_code == 200
    assert client.post("/optimiser/runs", params={"kind": "nope"}).status_code == 400


def test_a_saved_result_can_be_deleted(client, app):
    answer = ranking(client, app)
    path = f"/optimiser/runs/{answer['optimiser_run_id']}"

    assert client.delete(path).status_code == 200
    assert client.get(path).status_code == 404
    assert client.delete(path).status_code == 404
    assert client.get("/optimiser/runs/not-an-id").status_code == 404


def test_a_result_is_still_answered_if_it_cannot_be_saved(client, app, monkeypatch):
    def broken():
        raise RuntimeError("database went away")

    monkeypatch.setattr(app.state.context.database, "transaction", broken)

    answer = ranking(client, app)

    assert answer["optimiser_run_id"] is None
    assert answer["results"]
