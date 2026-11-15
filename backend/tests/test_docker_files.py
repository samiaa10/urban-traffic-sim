"""Checks that the Docker files agree with each other and with the code.

Like test_deployment_files.py for Render, these cannot prove the
containers run (the CI "docker" job builds and starts them), but they
catch the mistakes that would break them: a start-up order that lets the
API start before the migrations, a database URL that does not match the
database, more than one worker, the map or the migrations left out of the
image by .dockerignore, or nginx and Compose disagreeing about ports.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def compose():
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text())


def services(compose):
    return compose["services"]


def text(path):
    return (ROOT / path).read_text()


# ------------------------------------------------------------
# docker-compose.yml
# ------------------------------------------------------------

def test_the_whole_application_is_in_compose(compose):
    assert set(services(compose)) == {"db", "migrate", "backend", "frontend"}


def test_services_start_in_a_safe_order(compose):
    s = services(compose)

    assert s["migrate"]["depends_on"]["db"]["condition"] == "service_healthy"
    assert s["backend"]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    assert s["backend"]["depends_on"]["db"]["condition"] == "service_healthy"
    assert s["frontend"]["depends_on"]["backend"]["condition"] == "service_healthy"
    assert "healthcheck" in s["db"]


def test_migrate_runs_the_migrations_once(compose):
    migrate = services(compose)["migrate"]

    assert migrate["command"] == ["alembic", "upgrade", "head"]
    assert migrate["restart"] == "no"


def test_the_backend_and_migrations_use_the_compose_database(compose):
    s = services(compose)
    db = s["db"]["environment"]

    for name in ("migrate", "backend"):
        url = s[name]["environment"]["DATABASE_URL"]
        user, database = db["POSTGRES_USER"], db["POSTGRES_DB"]

        assert url.startswith(f"postgresql://{user}:"), name
        assert url.endswith(f"@db:5432/{database}"), name
        # The same password variable on both sides
        assert "${POSTGRES_PASSWORD:-" in url, name

    assert "${POSTGRES_PASSWORD:-" in db["POSTGRES_PASSWORD"]


def test_saved_data_lives_in_a_named_volume(compose):
    assert "pgdata:/var/lib/postgresql/data" in services(compose)["db"]["volumes"]
    assert "pgdata" in compose["volumes"]


def test_the_backend_is_never_scaled(compose):
    """Live runs are in one process's memory (see sessions.py)."""

    backend = services(compose)["backend"]

    assert backend.get("deploy", {}).get("replicas", 1) == 1
    assert "--workers" in text("backend/Dockerfile")
    assert '"--workers", "1"' in text("backend/Dockerfile")


def test_the_backend_port_is_only_published_on_this_computer(compose):
    assert services(compose)["backend"]["ports"] == [
        "127.0.0.1:${BACKEND_PORT:-8000}:8000"
    ]


def test_the_frontend_is_built_for_the_nginx_api_path(compose):
    frontend = services(compose)["frontend"]

    assert frontend["build"]["args"]["VITE_API_URL"] == "/api"
    assert frontend["ports"] == ["${FRONTEND_PORT:-8080}:8080"]


# ------------------------------------------------------------
# backend/Dockerfile and .dockerignore
# ------------------------------------------------------------

def test_the_backend_image_has_what_the_api_needs():
    dockerfile = text("backend/Dockerfile")

    assert "requirements-deploy.txt" in dockerfile
    assert "COPY alembic.ini" in dockerfile
    assert "COPY backend ./backend" in dockerfile
    # Where backend/api/main.py looks for the map by default
    assert "COPY data/osm/norwich_graph.pkl ./data/osm/norwich_graph.pkl" in dockerfile
    assert '"data" / "osm" / "norwich_graph.pkl"' in text("backend/api/main.py")
    assert "backend.api.main:app" in dockerfile
    assert "HEALTHCHECK" in dockerfile


def test_the_backend_does_not_run_as_root():
    users = re.findall(r"^USER (\S+)", text("backend/Dockerfile"), re.MULTILINE)

    assert users and users[-1] not in ("root", "0")


def docker_ignores(patterns, path):
    """Whether .dockerignore `patterns` leave `path` out of the build.

    Docker's rules: patterns are relative to the context root, * and ?
    do not cross "/", ** crosses any number of folders, a pattern that
    matches a folder covers everything in it, ! brings a path back, and
    the last pattern that matches wins.
    """

    def regex(pattern):
        out = ""
        i = 0
        while i < len(pattern):
            if pattern.startswith("**", i):
                out += ".*"
                i += 2
            elif pattern[i] == "*":
                out += "[^/]*"
                i += 1
            elif pattern[i] == "?":
                out += "[^/]"
                i += 1
            else:
                out += re.escape(pattern[i])
                i += 1
        return re.compile(out + r"\Z")

    parts = path.split("/")
    candidates = ["/".join(parts[:n]) for n in range(1, len(parts) + 1)]
    ignored = False

    for line in patterns:
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        keep = line.startswith("!")
        pattern = regex(line.lstrip("!").strip("/"))

        if any(pattern.match(candidate) for candidate in candidates):
            ignored = not keep

    return ignored


@pytest.mark.parametrize("path", [
    "data/osm/norwich_graph.pkl",
    "alembic.ini",
    "requirements-deploy.txt",
    "backend/api/main.py",
    "backend/db/migrations/env.py",
    "backend/db/migrations/versions/0001_initial_schema.py",
])
def test_dockerignore_keeps_what_the_image_copies(path):
    patterns = text(".dockerignore").splitlines()

    assert (ROOT / path).exists(), path
    assert not docker_ignores(patterns, path), path


@pytest.mark.parametrize("path", [
    ".git/HEAD",
    ".venv/bin/python",
    "backend/tests/test_api.py",
    "backend/api/__pycache__/main.cpython-312.pyc",
    "cache/81c6165849a925b01d5170b0b41d392afd6d2ade.json",
    "frontend/src/App.tsx",
    "data/some_other_download.pkl",
])
def test_dockerignore_leaves_out_what_the_image_does_not_need(path):
    assert docker_ignores(text(".dockerignore").splitlines(), path), path


def test_the_ignore_rules_are_read_the_way_docker_reads_them():
    rules = ["data/*", "!data/osm", "data/osm/*", "!data/osm/keep.pkl", "*.md", "**/x"]

    assert not docker_ignores(rules, "data/osm/keep.pkl")
    assert docker_ignores(rules, "data/osm/other.pkl")
    assert docker_ignores(rules, "README.md")
    assert not docker_ignores(rules, "docs/README.md")    # * does not cross /
    assert docker_ignores(rules, "a/b/x/c")


# ------------------------------------------------------------
# frontend/Dockerfile and nginx.conf
# ------------------------------------------------------------

def test_the_frontend_image_builds_from_the_lockfile_and_serves_with_nginx():
    dockerfile = text("frontend/Dockerfile")

    assert "npm ci" in dockerfile
    assert "ARG VITE_API_URL=/api" in dockerfile
    assert "nginx-unprivileged" in dockerfile
    assert "COPY nginx.conf /etc/nginx/conf.d/default.conf" in dockerfile
    assert "COPY --from=build /app/dist /usr/share/nginx/html" in dockerfile
    assert "VITE_API_URL" in text("frontend/src/config.ts")


def test_nginx_forwards_the_api_to_the_backend_service(compose):
    nginx = text("frontend/nginx.conf")

    assert "listen 8080;" in nginx
    assert "EXPOSE 8080" in text("frontend/Dockerfile")

    api_block = re.search(r"location /api/ \{(.*?)\}", nginx, re.S).group(1)

    # The /api prefix is removed before the request reaches the backend
    assert "rewrite ^/api/(.*)$ /$1 break;" in api_block
    assert "set $backend http://backend:8000;" in api_block
    assert "proxy_pass $backend;" in api_block
    assert "backend" in services(compose)


def test_nginx_finds_a_restarted_backend():
    """With a variable in proxy_pass and Docker's resolver, nginx looks the
    backend up again; with a fixed host name it would keep the IP it saw
    at start-up and answer 502 after the backend container restarts."""

    nginx = text("frontend/nginx.conf")

    assert re.search(r"resolver 127\.0\.0\.11 valid=\d+s", nginx)
    assert "proxy_pass http://backend" not in nginx

    # The app handles its own paths
    assert "try_files $uri $uri/ /index.html;" in nginx


def test_the_frontend_build_context_leaves_out_local_installs():
    ignored = text("frontend/.dockerignore").splitlines()

    assert "node_modules" in ignored
    assert "dist" in ignored


# ------------------------------------------------------------
# GitHub Actions (.github/workflows/tests.yml)
# ------------------------------------------------------------

@pytest.fixture(scope="module")
def workflow():
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(text(".github/workflows/tests.yml"))


PYTHON_JOBS = ("test", "test-python-3-12")


def steps_text(job):
    return "\n".join(step.get("run", "") for step in job["steps"])


@pytest.mark.parametrize("name", PYTHON_JOBS)
def test_ci_runs_the_database_tests_on_real_postgresql(workflow, name):
    job = workflow["jobs"][name]

    assert job["services"]["postgres"]["image"].startswith("postgres:16")
    assert "TEST_DATABASE_URL" in job["env"]
    # A missing database must fail the run, not skip the tests
    assert job["env"]["REQUIRE_DATABASE_TESTS"] == "1"

    # The test database's name contains "test" (the tests wipe it)
    assert "test" in job["env"]["TEST_DATABASE_URL"].rsplit("/", 1)[-1]


@pytest.mark.parametrize("name", PYTHON_JOBS)
def test_ci_checks_the_migrations(workflow, name):
    commands = steps_text(workflow["jobs"][name])

    assert "alembic upgrade head" in commands
    assert "alembic check" in commands
    assert "pytest backend/tests" in commands
    for package in ("sqlalchemy", "alembic", "psycopg"):
        assert package in commands, package


def test_ci_builds_and_runs_the_docker_stack(workflow):
    commands = steps_text(workflow["jobs"]["docker"])

    assert "docker compose config" in commands
    assert "scripts/compose_smoke_test.py" in commands
    assert (ROOT / "scripts" / "compose_smoke_test.py").is_file()


def test_ci_job_names_are_unchanged(workflow):
    """Branch protection can require checks by name; keep the old ones."""

    jobs = workflow["jobs"]

    assert jobs["test"].get("name", "test") == "test"
    assert jobs["test-python-3-12"]["name"] == "test (Python 3.12)"
    assert jobs["frontend"]["name"] == "frontend (lint, build and test)"
