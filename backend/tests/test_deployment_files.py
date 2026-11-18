"""Checks that the files Render needs are present and agree with each other.

These cannot prove a deployment works (only Render can), but they catch the
mistakes that would break one: the map missing from Git, a start command
that ignores Render's port, or an environment variable that was renamed on
one side only.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def blueprint():
    yaml = pytest.importorskip("yaml")

    return yaml.safe_load((ROOT / "render.yaml").read_text())


def service(blueprint, name):
    return next(s for s in blueprint["services"] if s["name"] == name)


def env_keys(a_service):
    return {item["key"] for item in a_service["envVars"]}


def test_the_norwich_map_is_in_the_repository_for_render_to_build_from():
    graph = ROOT / "data" / "osm" / "norwich_graph.pkl"

    assert graph.is_file() and graph.stat().st_size > 100_000

    # data/ is ignored by Git except for this one file
    ignore = (ROOT / ".gitignore").read_text()
    assert "!data/osm/norwich_graph.pkl" in ignore


def test_backend_starts_on_the_port_render_provides(blueprint):
    api = service(blueprint, "norwich-sim-api")

    assert api["runtime"] == "python"
    assert "backend.api.main:app" in api["startCommand"]
    assert "--host 0.0.0.0" in api["startCommand"]
    assert "--port $PORT" in api["startCommand"]


def test_backend_installs_from_a_file_that_exists_and_stays_small(blueprint):
    api = service(blueprint, "norwich-sim-api")
    name = api["buildCommand"].split("-r")[-1].strip()

    lines = (ROOT / name).read_text().splitlines()
    packages = {
        line.split(">")[0].split("=")[0].strip()
        for line in lines
        if line.strip() and not line.startswith("#")
    }

    # numpy is needed to open the saved map; osmnx would make the build
    # slow and large for no benefit, because the map is already downloaded.
    # The database packages (Stage 3) are imported by the backend whether
    # or not DATABASE_URL is set; psycopg[binary] ships prebuilt wheels,
    # so nothing is compiled during the build.
    assert packages == {
        "fastapi", "uvicorn", "numpy",
        "sqlalchemy", "alembic", "psycopg[binary]",
    }


def test_both_services_use_the_same_variable_names_the_code_reads(blueprint):
    api = service(blueprint, "norwich-sim-api")
    site = service(blueprint, "norwich-sim")

    assert "CORS_ORIGINS" in env_keys(api)
    assert "VITE_API_URL" in env_keys(site)

    assert "CORS_ORIGINS" in (ROOT / "backend/api/main.py").read_text()
    assert "VITE_API_URL" in (ROOT / "frontend/src/config.ts").read_text()


def test_addresses_are_not_stored_in_git(blueprint):
    # They are entered in Render, so no real URL or "*" is committed here.
    for name, key in [("norwich-sim-api", "CORS_ORIGINS"), ("norwich-sim", "VITE_API_URL")]:
        item = next(i for i in service(blueprint, name)["envVars"] if i["key"] == key)

        assert item.get("sync") is False
        assert "value" not in item


def test_frontend_is_built_to_static_files(blueprint):
    site = service(blueprint, "norwich-sim")

    assert site["runtime"] == "static"
    assert site["rootDir"] == "frontend"
    assert site["staticPublishPath"] == "dist"
    assert (ROOT / "frontend" / "package-lock.json").is_file()  # npm ci needs it


def test_backend_uses_the_blueprints_database_and_migrates_it(blueprint):
    api = service(blueprint, "norwich-sim-api")
    database = next(d for d in blueprint["databases"] if d["name"] == "norwich-sim-db")

    # The connection string comes from the database Render creates, so no
    # password is committed
    item = next(i for i in api["envVars"] if i["key"] == "DATABASE_URL")
    assert item["fromDatabase"] == {"name": database["name"], "property": "connectionString"}
    assert "value" not in item
    assert "DATABASE_URL" in (ROOT / "backend/api/main.py").read_text()

    # Migrations run before the server starts, from files in the repository
    command = api["startCommand"]
    assert command.startswith("alembic upgrade head && ")
    assert command.index("alembic") < command.index("uvicorn")
    assert (ROOT / "alembic.ini").is_file()
    assert (ROOT / "backend/db/migrations/env.py").is_file()


def test_backend_runs_studies_in_process_on_the_small_free_service(blueprint):
    api = service(blueprint, "norwich-sim-api")
    item = next(i for i in api["envVars"] if i["key"] == "SCENARIO_WORKERS")

    assert item["value"] == "0"
    assert "SCENARIO_WORKERS" in (ROOT / "backend/api/main.py").read_text()


def test_render_checks_the_api_through_its_health_endpoint(blueprint):
    api = service(blueprint, "norwich-sim-api")

    assert api["healthCheckPath"] == "/health"
    assert '@router.get("/health"' in (ROOT / "backend/api/routes/routing.py").read_text()


def test_render_deploys_only_commits_whose_checks_passed(blueprint):
    for name in ("norwich-sim-api", "norwich-sim"):
        assert service(blueprint, name)["autoDeployTrigger"] == "checksPass"


def test_the_public_api_trusts_renders_proxy_and_limits_expensive_requests(blueprint):
    api = service(blueprint, "norwich-sim-api")
    values = {item["key"]: item.get("value") for item in api["envVars"]}

    assert values["TRUST_PROXY"] == "true"
    assert int(values["RATE_LIMIT_PER_MINUTE"]) > 0
    assert int(values["RUN_IDLE_MINUTES"]) > 0
    main = (ROOT / "backend/api/main.py").read_text()
    for key in ("TRUST_PROXY", "RATE_LIMIT_PER_MINUTE", "RUN_IDLE_MINUTES", "LOG_FORMAT"):
        assert key in main


def test_the_static_site_sends_security_headers(blueprint):
    site = service(blueprint, "norwich-sim")
    headers = {(h["path"], h["name"]): h["value"] for h in site["headers"]}

    assert headers[("/*", "X-Content-Type-Options")] == "nosniff"
    assert headers[("/*", "X-Frame-Options")] == "DENY"
