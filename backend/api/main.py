"""The API's entry point: `uvicorn backend.api.main:app`.

The endpoints live in backend/api/routes/, the app is put together in
backend/api/app.py, and live simulation runs are kept in
backend/api/sessions.py. This module sets up logging, loads the Norwich
road graph and exposes the app.
"""

import logging
import os
from pathlib import Path

from backend.api.app import create_app
from backend.api.settings import (
    allowed_origins,
    database_url,
    log_level,
    max_live_runs,
    max_study_scenarios,
    resume_studies,
    scenario_workers,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


# Logging. LOG_LEVEL (default INFO) sets how much is written; DEBUG also
# writes every dispatched emergency route point by point. basicConfig does
# nothing if logging was already set up (for example by pytest), and
# uvicorn's own loggers keep their own format.
logging.basicConfig(
    level=log_level(os.environ.get("LOG_LEVEL")),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

# The Norwich road network. NORWICH_SIM_GRAPH can point at a different
# graph file; the API tests use this to run against a tiny map.
GRAPH_FILE = Path(
    os.environ.get(
        "NORWICH_SIM_GRAPH",
        PROJECT_ROOT / "data" / "osm" / "norwich_graph.pkl"
    )
)


app = create_app(
    GRAPH_FILE,
    # Which websites may call this API from a browser. Locally that is the
    # Vite dev server; when deployed, set CORS_ORIGINS to the frontend's URL.
    cors_origins=allowed_origins(os.environ.get("CORS_ORIGINS")),
    # How many simulation runs may be in memory at once (default 8)
    max_live=max_live_runs(os.environ.get("MAX_LIVE_RUNS")),
    # PostgreSQL for saving runs and optimiser results; not set = in memory
    database_url=database_url(os.environ.get("DATABASE_URL")),
    # Processes that run scenario studies (default min(4, CPUs); 0 = one
    # at a time in this process) and the largest study allowed
    scenario_workers=scenario_workers(os.environ.get("SCENARIO_WORKERS")),
    max_study_scenarios=max_study_scenarios(os.environ.get("MAX_STUDY_SCENARIOS")),
    # Queue studies a restart interrupted again (default yes)
    resume_studies=resume_studies(os.environ.get("RESUME_STUDIES")),
)

context = app.state.context

# Shared, read-only structures (also handy in tests and scripts)
graph = context.graph
network = context.network
node_by_point = context.node_by_point
default_station_candidates = context.default_station_candidates
registry = context.registry


def __getattr__(name):
    """`main.simulation` and `main.traffic`: the default run's simulation
    and traffic manager (None when nothing is running), as before runs
    had IDs."""

    if name in ("simulation", "traffic"):
        run = registry.default()
        return getattr(run, name) if run is not None else None

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
