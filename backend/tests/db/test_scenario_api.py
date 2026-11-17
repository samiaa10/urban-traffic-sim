"""Scenario studies through the API: background jobs, results, comparison."""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from backend.api.app import create_app  # noqa: E402


CUT = [{"start": 3 * 8 + c, "destination": 4 * 8 + c} for c in range(1, 8)]


@pytest.fixture
def make_scenario_app(engine, database_url_for_tests, grid_graph_file, make_app):
    """Apps on the grid map. make_app is used for its clean-up only."""

    from sqlalchemy import text

    apps = []

    def make(**options):
        app = create_app(grid_graph_file, database_url=database_url_for_tests, **options)
        apps.append(app)
        return app

    yield make

    for app in apps:
        app.state.context.jobs.shutdown()
        app.state.context.database.dispose()

    with engine.begin() as connection:
        connection.execute(text("TRUNCATE scenario_studies RESTART IDENTITY CASCADE"))


@pytest.fixture
def app(make_scenario_app):
    return make_scenario_app()


@pytest.fixture
def client(app):
    return TestClient(app)


def scenario(name, **overrides):
    body = {"name": name, "seed": 4, "demand": {"vehicles": 40}}
    body.update(overrides)
    return body


def run_study(client, app, scenarios, **options):
    response = client.post("/scenarios/studies", json={"scenarios": scenarios, **options})
    assert response.status_code == 202, response.text
    assert app.state.context.jobs.wait_until_idle(timeout=120)
    return client.get(f"/scenarios/studies/{response.json()['study_id']}").json()


# ------------------------------------------------------------
# A study from start to finish
# ------------------------------------------------------------

def test_a_study_runs_in_the_background_and_ranks_its_scenarios(client, app):
    created = client.post("/scenarios/studies", json={
        "name": "Cut test",
        "scenarios": [scenario("Baseline"), scenario("Network cut", closures=CUT)],
        "baseline": 0,
    })

    assert created.status_code == 202
    assert created.json()["status"] == "queued"
    assert created.json()["scenario_count"] == 2

    assert app.state.context.jobs.wait_until_idle(timeout=120)
    study = client.get(created.json()["links"]["study"]).json()

    assert study["status"] == "completed"
    assert (study["completed"], study["failed"], study["progress_percent"]) == (2, 0, 100)
    assert [s["status"] for s in study["scenarios"]] == ["completed", "completed"]
    assert study["scenarios"][0]["is_baseline"] is True
    assert study["scenarios"][0]["metrics"]["total_delay_vehicle_hours"] > 0

    assert [row["name"] for row in study["ranking"]] in (
        ["Baseline", "Network cut"], ["Network cut", "Baseline"]
    )
    assert study["ranking_metric"]["key"] == "total_delay_vehicle_hours"
    assert any("Trip by trip" in sentence for sentence in study["explanation"])


def test_a_scenario_shows_its_full_result_and_its_difference_from_the_baseline(client, app):
    study = run_study(client, app, [scenario("Baseline"), scenario("Cut", closures=CUT)], baseline=0)
    cut_id = study["scenarios"][1]["scenario_id"]

    detail = client.get(f"/scenarios/{cut_id}").json()
    result = detail["result"]

    # The stored spec is the validated one, with defaults filled in
    assert [(c["start"], c["destination"]) for c in detail["spec"]["closures"]] == [
        (c["start"], c["destination"]) for c in CUT
    ]
    assert detail["spec"]["closures"][0]["start_at"] == 0
    # Each closure with its coordinates, for drawing on the map
    assert len(detail["disruptions"]) == 7
    first = detail["disruptions"][0]
    graph = app.state.context.graph
    assert first["kind"] == "closure" and first["start_node"] == 25
    assert first["start"] == {"latitude": graph.nodes[25]["latitude"], "longitude": graph.nodes[25]["longitude"]}
    assert result["summary"]["trips_requested"] == 40
    assert result["trip_counts"]["finished"] + result["trip_counts"]["unfinished"] \
        + result["trip_counts"]["unroutable"] == 40
    assert result["roads"] and result["areas"] and result["timeseries"]
    assert result["timeseries"][0]["closed_roads"] == 7
    assert result["metrics"]["total_delay_vehicle_hours"] == result["summary"]["total_delay_vehicle_hours"]

    versus = detail["vs_baseline"]
    assert versus["baseline_name"] == "Baseline"
    assert versus["paired"]["trips_compared"] > 0
    assert versus["paired"]["extra_vehicle_hours"] > 0
    assert "increases" in versus["roads"]

    baseline = client.get(f"/scenarios/{study['scenarios'][0]['scenario_id']}").json()
    assert baseline["vs_baseline"] is None


def test_a_different_ranking_metric_can_be_asked_for(client, app):
    study = run_study(client, app, [scenario("A"), scenario("B", rerouting=False)])

    by_increase = client.get(
        f"/scenarios/studies/{study['study_id']}", params={"metric": "mean_increase_seconds"}
    ).json()

    assert by_increase["ranking_metric"]["key"] == "mean_increase_seconds"
    values = [row["value"] for row in by_increase["ranking"]]
    assert values == sorted(values)
    assert client.get(
        f"/scenarios/studies/{study['study_id']}", params={"metric": "nope"}
    ).status_code == 400


def test_comparing_scenarios_from_different_studies(client, app):
    first = run_study(client, app, [scenario("Baseline")])
    second = run_study(client, app, [scenario("Baseline"), scenario("Cut", closures=CUT)])
    ids = [first["scenarios"][0]["scenario_id"], second["scenarios"][1]["scenario_id"]]

    outcome = client.get(
        "/scenarios/compare", params={"ids": ",".join(ids), "baseline": ids[0]}
    ).json()

    names = [row["name"] for row in outcome["ranking"]]
    assert sorted(names) == ["Baseline", "Cut"]
    rows = {row["name"]: row for row in outcome["ranking"]}
    assert rows["Cut"]["paired"]["trips_compared"] > 0
    assert outcome["explanation"]

    # Same names from two studies are told apart
    same = client.get("/scenarios/compare", params={"ids": ",".join([
        first["scenarios"][0]["scenario_id"], second["scenarios"][0]["scenario_id"],
    ])}).json()
    assert sorted(r["name"] for r in same["ranking"]) == ["Baseline", "Baseline (2)"]

    assert client.get("/scenarios/compare", params={"ids": ids[0]}).status_code == 400


def test_a_failing_scenario_does_not_stop_the_study(client, app, monkeypatch):
    from backend.scenarios import jobs

    real = jobs._run

    def flaky(spec, world=None):
        if spec["name"] == "Breaks":
            return "error", "simulated failure"
        return real(spec, world)

    monkeypatch.setattr(jobs, "_run", flaky)

    study = run_study(client, app, [scenario("Works"), scenario("Breaks")])

    assert study["status"] == "completed"
    assert (study["completed"], study["failed"]) == (1, 1)
    broken = [s for s in study["scenarios"] if s["name"] == "Breaks"][0]
    assert (broken["status"], broken["error"]) == ("failed", "simulated failure")
    assert [row["name"] for row in study["ranking"]] == ["Works"]


# ------------------------------------------------------------
# Refusing bad studies
# ------------------------------------------------------------

def test_invalid_scenarios_are_refused_with_their_position(client):
    response = client.post("/scenarios/studies", json={"scenarios": [
        scenario("Fine"),
        scenario("Unknown road", closures=[{"start": 0, "destination": 999}]),
        scenario("Not a road", accidents=[{"start": 0, "destination": 63}]),
        scenario("Too many", demand={"vehicles": 99999}),
        scenario("Empty zone", surges=[{"latitude": 10.0, "longitude": 10.0}]),
    ]})

    assert response.status_code == 400
    problems = " ".join(response.json()["detail"])
    assert "scenarios[1]" in problems and "not on the map" in problems
    assert "scenarios[2]" in problems and "not directly connected" in problems
    assert "scenarios[3].demand.vehicles" in problems
    assert "scenarios[4].surges[0]" in problems


def test_study_limits(make_scenario_app):
    client = TestClient(make_scenario_app(max_study_scenarios=2))

    too_many = client.post("/scenarios/studies", json={"scenarios": [scenario(str(i)) for i in range(3)]})
    bad_metric = client.post("/scenarios/studies", json={"scenarios": [scenario("A")], "metric": "fun"})
    bad_baseline = client.post("/scenarios/studies", json={"scenarios": [scenario("A")], "baseline": 4})

    assert too_many.status_code == 400 and "at most 2" in too_many.json()["detail"]
    assert bad_metric.status_code == 400
    assert bad_baseline.status_code == 400


def test_repeated_names_are_made_unique(client, app):
    study = run_study(client, app, [scenario("Same"), scenario("Same", seed=5)])

    assert [s["name"] for s in study["scenarios"]] == ["Same", "Same (2)"]


# ------------------------------------------------------------
# Sweeps
# ------------------------------------------------------------

def test_each_closure_sweep_has_a_baseline_and_one_scenario_per_road(client, app):
    response = client.post("/scenarios/studies/sweep", json={
        "name": "Which road matters",
        "base": scenario("Weekday"),
        "kind": "each_closure",
        "roads": [[27, 28], [35, 36]],
    })
    assert response.status_code == 202, response.text
    assert app.state.context.jobs.wait_until_idle(timeout=120)

    study = client.get(f"/scenarios/studies/{response.json()['study_id']}").json()

    assert [s["name"] for s in study["scenarios"]] == [
        "Weekday: baseline", "Close 27-28", "Close 35-36",
    ]
    assert study["scenarios"][0]["is_baseline"] is True
    assert study["status"] == "completed"


def test_random_closure_sweep_draws_roads_from_the_zone(client, app):
    response = client.post("/scenarios/studies/sweep", json={
        "base": scenario("Base"),
        "kind": "random_closures",
        "count": 3,
        "closures_per_scenario": 2,
        "zone": {"latitude": 52.627, "longitude": 1.29, "radius_metres": 600},
        "sweep_seed": 9,
    })
    assert response.status_code == 202, response.text
    assert response.json()["scenario_count"] == 4
    assert app.state.context.jobs.wait_until_idle(timeout=120)


def test_seed_and_station_sweeps(client, app):
    seeds = client.post("/scenarios/studies/sweep", json={
        "base": scenario("Base"), "kind": "seeds", "count": 3, "first_seed": 10,
    })
    assert seeds.status_code == 202 and seeds.json()["scenario_count"] == 3

    layouts = client.post("/scenarios/studies/sweep", json={
        "base": scenario("Base", emergencies={"count": 3, "first_at": 10, "interval_seconds": 20},
                         stations=[{"latitude": 52.62, "longitude": 1.28}]),
        "kind": "station_layouts",
        "layouts": [
            {"name": "Corner", "stations": [{"latitude": 52.62, "longitude": 1.28}]},
            {"name": "Centre", "stations": [{"latitude": 52.627, "longitude": 1.29}]},
        ],
        "metric": "emergency_mean_response_seconds",
    })
    assert layouts.status_code == 202, layouts.text
    assert app.state.context.jobs.wait_until_idle(timeout=120)

    study = client.get(f"/scenarios/studies/{layouts.json()['study_id']}").json()
    assert study["ranking_metric"]["key"] == "emergency_mean_response_seconds"
    assert {row["name"] for row in study["ranking"]} == {"Corner", "Centre"}


# ------------------------------------------------------------
# Cancel, delete, restart
# ------------------------------------------------------------

def test_cancelling_stops_the_remaining_scenarios(client, app):
    created = client.post("/scenarios/studies", json={
        "scenarios": [scenario(f"S{i}", demand={"vehicles": 80}) for i in range(6)],
    }).json()

    cancelled = client.post(f"/scenarios/studies/{created['study_id']}/cancel")
    assert cancelled.status_code == 200
    assert app.state.context.jobs.wait_until_idle(timeout=120)

    study = client.get(f"/scenarios/studies/{created['study_id']}").json()
    statuses = [s["status"] for s in study["scenarios"]]

    assert study["status"] == "cancelled"
    assert "cancelled" in statuses
    assert "queued" not in statuses and "running" not in statuses
    assert client.post(f"/scenarios/studies/{created['study_id']}/cancel").status_code == 409


def test_deleting_a_study(client, app):
    study = run_study(client, app, [scenario("Gone")])
    scenario_id = study["scenarios"][0]["scenario_id"]

    assert client.delete(f"/scenarios/studies/{study['study_id']}").status_code == 200
    assert client.get(f"/scenarios/studies/{study['study_id']}").status_code == 404
    assert client.get(f"/scenarios/{scenario_id}").status_code == 404


def test_studies_left_unfinished_by_a_restart_are_interrupted(make_scenario_app):
    """With resuming switched off (resume_studies=False)."""

    first = make_scenario_app()
    before = TestClient(first)
    created = before.post("/scenarios/studies", json={
        "scenarios": [scenario(f"S{i}", demand={"vehicles": 80}) for i in range(8)],
    }).json()

    # Stop handing out work, as a server that went down would
    first.state.context.jobs.shutdown()

    after = TestClient(make_scenario_app(resume_studies=False))
    study = after.get(f"/scenarios/studies/{created['study_id']}").json()

    assert study["status"] == "interrupted"
    assert {s["status"] for s in study["scenarios"]} <= {"completed", "interrupted"}


def test_studies_are_listed_newest_first(client, app):
    run_study(client, app, [scenario("First")], name="One")
    run_study(client, app, [scenario("Second")], name="Two")

    listed = client.get("/scenarios/studies").json()

    assert listed["total"] == 2
    assert [s["name"] for s in listed["studies"]] == ["Two", "One"]
    assert "scenarios" not in listed["studies"][0]


def test_unknown_ids_are_404(client):
    assert client.get("/scenarios/studies/nope").status_code == 404
    assert client.get("/scenarios/00000000-0000-0000-0000-000000000000").status_code == 404
    assert client.post("/scenarios/studies/nope/cancel").status_code == 404


# ------------------------------------------------------------
# Worker processes
# ------------------------------------------------------------

def test_worker_processes_give_the_same_results_as_running_in_process(make_scenario_app, grid_graph_file):
    """Two real worker processes; each scenario's result must equal running
    it directly (the simulation is deterministic across processes)."""

    import pickle

    from backend.scenarios.runner import World, run_scenario

    app = make_scenario_app(scenario_workers=2)
    client = TestClient(app)
    specs = [scenario("A"), scenario("B", seed=5), scenario("C", closures=CUT)]

    study = run_study(client, app, specs)

    assert study["status"] == "completed"
    assert study["workers"] == 2

    with open(grid_graph_file, "rb") as file:
        world = World(pickle.load(file))

    for spec, row in zip(specs, study["scenarios"]):
        direct = run_scenario(world, spec)
        stored = client.get(f"/scenarios/{row['scenario_id']}").json()["result"]
        assert stored["summary"] == direct["summary"], spec["name"]
        assert stored["roads"] == direct["roads"], spec["name"]
        assert stored["timeseries"] == direct["timeseries"], spec["name"]


# ------------------------------------------------------------
# Resuming studies
# ------------------------------------------------------------

def test_a_restart_resumes_unfinished_studies_and_keeps_finished_results(make_scenario_app, grid_graph_file):
    """The server stops part-way through; at the next start the study is
    queued again, only its unfinished scenarios run, and every result is
    what an uninterrupted run gives (scenarios are deterministic)."""

    import pickle

    from backend.scenarios.runner import World, run_scenario

    specs = [scenario(f"S{i}", seed=i + 1) for i in range(5)]

    first = make_scenario_app()
    before = TestClient(first)
    created = before.post("/scenarios/studies", json={"scenarios": specs}).json()
    jobs = first.state.context.jobs
    jobs.shutdown()               # stop handing out work: a crash part-way
    jobs.wait_until_idle(timeout=60)

    partly = before.get(f"/scenarios/studies/{created['study_id']}").json()
    done_before = {s["name"] for s in partly["scenarios"] if s["status"] == "completed"}
    assert len(done_before) < 5

    app = make_scenario_app(resume_studies=True)
    after = TestClient(app)
    assert app.state.context.jobs.wait_until_idle(timeout=120)
    study = after.get(f"/scenarios/studies/{created['study_id']}").json()

    assert study["status"] == "completed"
    assert study["completed"] == 5
    assert [s["status"] for s in study["scenarios"]] == ["completed"] * 5

    with open(grid_graph_file, "rb") as file:
        world = World(pickle.load(file))
    for spec, row in zip(specs, study["scenarios"]):
        stored = after.get(f"/scenarios/{row['scenario_id']}").json()["result"]
        assert stored["summary"] == run_scenario(world, spec)["summary"], spec["name"]


def test_a_cancelled_study_can_be_resumed_by_hand(client, app):
    created = client.post("/scenarios/studies", json={
        "scenarios": [scenario(f"S{i}", demand={"vehicles": 60}) for i in range(4)],
    }).json()
    client.post(f"/scenarios/studies/{created['study_id']}/cancel")
    assert app.state.context.jobs.wait_until_idle(timeout=120)

    resumed = client.post(f"/scenarios/studies/{created['study_id']}/resume")

    assert resumed.status_code == 200
    assert resumed.json()["requeued"] >= 1
    assert app.state.context.jobs.wait_until_idle(timeout=120)
    study = client.get(f"/scenarios/studies/{created['study_id']}").json()
    assert study["status"] == "completed" and study["completed"] == 4


def test_only_interrupted_or_cancelled_studies_can_be_resumed(client, app):
    study = run_study(client, app, [scenario("Done")])

    assert client.post(f"/scenarios/studies/{study['study_id']}/resume").status_code == 409
    assert client.post("/scenarios/studies/00000000-0000-0000-0000-000000000000/resume").status_code == 404


# ------------------------------------------------------------
# Traffic models
# ------------------------------------------------------------

def test_a_scenario_is_saved_with_the_traffic_model_it_runs(make_scenario_app):
    app = make_scenario_app(traffic_model="osm")
    client = TestClient(app)

    study = run_study(client, app, [
        scenario("Default"),
        scenario("Original rules", traffic_model="simple"),
    ])
    details = {
        s["name"]: client.get(f"/scenarios/{s['scenario_id']}").json()
        for s in study["scenarios"]
    }

    # Not given: the server's default, written into the saved spec
    assert details["Default"]["spec"]["traffic_model"] == "osm"
    assert details["Original rules"]["spec"]["traffic_model"] == "simple"
    assert all(d["status"] == "completed" for d in details.values())
    # Same trips, different rules: the explanation says what differs
    assert details["Default"]["trip_signature"] == details["Original rules"]["trip_signature"]
    assert any("traffic model" in line for line in study["explanation"])


def test_roads_are_only_named_on_a_map_with_openstreetmap_attributes(client, app):
    from backend.api.routes.scenarios import road_name_field
    from backend.routing.graph import Graph
    from backend.routing.road_attributes import RoadInfo

    # The grid test map has no attributes: answers keep their old shape
    road = client.get("/scenarios/nearest-road", params={"latitude": 52.62, "longitude": 1.28}).json()
    assert "name" not in road

    graph = Graph()
    graph.road_info[(1, 2)] = RoadInfo("primary", 13.4, True, 1, False, False, "Riverside Road")
    graph.road_info[(2, 3)] = RoadInfo("residential", 8.9, True, 1, False, False, None)
    assert road_name_field(graph, 2, 1) == {"name": "Riverside Road"}
    assert road_name_field(graph, 2, 3) == {"name": None}


# ------------------------------------------------------------
# Testing the optimiser's answers in traffic
# ------------------------------------------------------------

def test_the_optimisers_best_sites_are_tested_in_traffic(client, app):
    response = client.post("/optimiser/evaluations", json={
        "top": 2, "demand": [30], "seeds": [1, 2], "emergencies": 3,
        "first_emergency_at": 20, "emergency_interval_seconds": 15,
        "optimiser_scenarios": 40, "max_seconds": 900, "vehicles_per_station": 1,
    })
    assert response.status_code == 202, response.text
    created = response.json()
    assert created["candidates"] == ["Site #1", "Site #2"]
    assert created["scenario_count"] == 1 * 2 * (2 + 1)

    assert app.state.context.jobs.wait_until_idle(timeout=180)
    body = client.get(f"/optimiser/evaluations/{created['evaluation_id']}").json()
    report = body["report"]

    assert body["status"] == "completed"
    assert report["complete"] and report["scenarios_done"] == 6
    assert report["rank_agreement_kendall_tau"] in (-1, 1)
    for candidate in report["candidates"]:
        demand = candidate["by_demand"][0]
        assert demand["runs"] == 2
        assert candidate["estimate"]["mean_response_seconds"] > 0
        assert candidate["simulated_mean_response_seconds"] > 0
        assert demand["mean_response_seconds"]["ci95"] is not None
        assert demand["added_delay_vehicle_hours"]["mean"] is not None
    assert report["explanation"]

    listed = client.get("/optimiser/evaluations").json()["evaluations"]
    assert listed[0]["evaluation_id"] == created["evaluation_id"]
    assert client.get("/optimiser/evaluations/not-an-id").status_code == 404


def test_evaluations_check_their_settings(client):
    assert client.post("/optimiser/evaluations", json={"demand": [0]}).status_code in (400, 422)
    assert client.post("/optimiser/evaluations", json={"service": "taxi"}).status_code == 422
    assert client.post("/optimiser/evaluations", json={"surprise": 1}).status_code == 422
