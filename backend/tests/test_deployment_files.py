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
    assert packages == {"fastapi", "uvicorn", "numpy"}


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
