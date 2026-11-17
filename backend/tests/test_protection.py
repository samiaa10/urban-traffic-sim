"""Production protections (api/protection.py and Stage 6 reliability):
rate limits, the request size limit, security headers, health and
readiness, starting without a reachable database, idle runs, and runs
that replace nobody else's."""

import logging
import pickle

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from backend.api.app import create_app  # noqa: E402
from backend.api.protection import RateLimiter, is_expensive  # noqa: E402
from backend.api.sessions import RunRegistry  # noqa: E402
from backend.api.settings import (  # noqa: E402
    log_format,
    rate_limit_per_minute,
    run_idle_minutes,
    trust_proxy,
)
from backend.tests.test_api import make_test_graph  # noqa: E402


@pytest.fixture(scope="module")
def graph_file(tmp_path_factory):
    path = tmp_path_factory.mktemp("graph") / "test_graph.pkl"
    with open(path, "wb") as file:
        pickle.dump(make_test_graph(), file)
    return path


# ------------------------------------------------------------
# Rate limits
# ------------------------------------------------------------

def test_the_bucket_refills_evenly():
    now = [0.0]
    limiter = RateLimiter(60, clock=lambda: now[0])     # one a second

    assert all(limiter.take("a")[0] for _ in range(60))
    allowed, wait = limiter.take("a")
    assert not allowed and wait == pytest.approx(1.0)
    assert limiter.take("b")[0]                         # others unaffected

    now[0] = 2.0
    assert limiter.take("a")[0] and limiter.take("a")[0]
    assert not limiter.take("a")[0]


def test_only_expensive_requests_are_limited():
    assert is_expensive("POST", "/simulation/start")
    assert is_expensive("POST", "/simulations/0d0a7c6e-2f3b-4d8e-9a8f-1c2d3e4f5a6b/fleet")
    assert is_expensive("GET", "/optimiser/stations")
    assert is_expensive("POST", "/scenarios/studies/sweep")
    assert not is_expensive("POST", "/simulation/step")
    assert not is_expensive("GET", "/simulations")
    assert not is_expensive("GET", "/emergency/status")


def test_over_the_limit_a_client_gets_429_with_retry_after(graph_file):
    client = TestClient(create_app(graph_file, rate_limit_per_minute=2))

    codes = [
        client.post("/simulation/start", params={"start": 0, "destination": 15}).status_code
        for _ in range(3)
    ]
    assert codes == [200, 200, 429]
    refused = client.post("/simulation/start", params={"start": 0, "destination": 15})
    assert int(refused.headers["Retry-After"]) >= 1
    assert "Too many requests" in refused.json()["detail"]
    # Cheap requests still work
    assert client.post("/simulation/step").status_code == 200


def test_behind_a_proxy_each_forwarded_client_has_its_own_limit(graph_file):
    client = TestClient(create_app(graph_file, rate_limit_per_minute=1, trust_proxy=True))
    start = {"start": 0, "destination": 15}

    assert client.post("/simulation/start", params=start, headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200
    assert client.post("/simulation/start", params=start, headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 429
    assert client.post("/simulation/start", params=start, headers={"X-Forwarded-For": "2.2.2.2, 10.0.0.1"}).status_code == 200


def test_settings_for_the_protections():
    assert rate_limit_per_minute(None) == 30 and rate_limit_per_minute("0") == 0
    assert run_idle_minutes(None) == 30
    assert trust_proxy(None) is False and trust_proxy("yes") is True
    assert log_format("JSON") == "json"
    for parse, bad in ((rate_limit_per_minute, "-1"), (run_idle_minutes, "x"), (trust_proxy, "maybe"), (log_format, "xml")):
        with pytest.raises(ValueError):
            parse(bad)


# ------------------------------------------------------------
# Size, headers, logging
# ------------------------------------------------------------

def test_large_bodies_are_refused_and_answers_carry_security_headers(graph_file):
    client = TestClient(create_app(graph_file))

    too_big = client.post(
        "/scenarios/studies", content=b"x" * (2 * 1024 * 1024 + 1),
        headers={"Content-Type": "application/json"},
    )
    assert too_big.status_code == 413

    answer = client.get("/health")
    assert answer.headers["X-Content-Type-Options"] == "nosniff"
    assert answer.headers["X-Frame-Options"] == "DENY"


def test_each_request_is_logged_once(graph_file, caplog):
    client = TestClient(create_app(graph_file, log_format="json"))

    with caplog.at_level(logging.INFO, logger="backend.requests"):
        client.get("/health")
        client.post("/simulation/start", params={"start": 0, "destination": 15})
        client.post("/simulation/step")     # polled every second: DEBUG only

    lines = [r.getMessage() for r in caplog.records if r.name == "backend.requests"]
    assert len(lines) == 2
    assert '"path": "/health"' in lines[0] and '"status": 200' in lines[0]


# ------------------------------------------------------------
# Health and readiness
# ------------------------------------------------------------

def test_health_and_readiness_without_a_database(graph_file):
    client = TestClient(create_app(graph_file))

    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").json()["database"] == "not configured"


def test_an_unreachable_database_degrades_the_api_instead_of_stopping_it(graph_file):
    # Nothing listens on port 1
    app = create_app(graph_file, database_url="postgresql+psycopg://user:pw@127.0.0.1:1/nothing")
    client = TestClient(app)

    ready = client.get("/ready")
    assert ready.status_code == 503
    assert ready.json()["status"] == "degraded"
    assert "127.0.0.1" not in ready.text and "user" not in ready.json()["detail"]
    # Simulation still works, it just is not saved
    assert client.post("/simulation/start", params={"start": 0, "destination": 15}).status_code == 200
    assert client.post("/simulation/step").status_code == 200
    assert client.get("/simulations/x/history").status_code in (404, 503)


# ------------------------------------------------------------
# Runs: idle ones end, and visitors do not replace each other
# ------------------------------------------------------------

class FakeRun:
    def __init__(self, run_id, last_used):
        self.id = run_id
        self.last_used = last_used

    def touch(self):
        pass


def test_runs_unused_for_too_long_are_stopped_when_a_run_is_added(monkeypatch):
    import backend.api.sessions as sessions

    now = [900.0]
    registry = RunRegistry(max_live=8, idle_seconds=60)
    monkeypatch.setattr(sessions.time, "monotonic", lambda: now[0])
    registry.add(FakeRun("old", last_used=900.0))
    registry.add(FakeRun("recent", last_used=990.0))

    now[0] = 1000.0
    stopped = registry.add(FakeRun("new", last_used=1000.0))

    assert [(run.id, reason) for run, reason in stopped] == [("old", "stopped")]
    assert registry.get("recent") is not None


def test_a_run_started_without_replacing_the_default_leaves_others_running(graph_file):
    client = TestClient(create_app(graph_file))
    start = {"start": 0, "destination": 15}

    first = client.post("/simulation/start", params={**start, "replace_default": False}).json()["simulation_id"]
    second = client.post("/simulation/start", params={**start, "replace_default": False}).json()["simulation_id"]

    for run_id in (first, second):
        assert client.post(f"/simulations/{run_id}/step").status_code == 200

    # The original behaviour is unchanged: replace_default (default true)
    # stops the previous default run
    third = client.post("/simulation/start", params=start).json()["simulation_id"]
    fourth = client.post("/simulation/start", params=start).json()["simulation_id"]
    assert client.post(f"/simulations/{third}/step").status_code == 404
    assert client.post(f"/simulations/{fourth}/step").status_code == 200
