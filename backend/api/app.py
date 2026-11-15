"""Builds the FastAPI app: settings, shared context and every router."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.context import AppContext, load_graph
from backend.api.routes import (
    emergencies,
    incidents,
    optimiser,
    routing,
    simulation,
    stations,
    traffic,
)
from backend.api.settings import DEFAULT_MAX_LIVE_RUNS, allowed_origins


def create_app(graph_file, cors_origins=None, max_live=DEFAULT_MAX_LIVE_RUNS):
    """A new app with its own road graph and (empty) set of runs.

    cors_origins is a list of origins (None: the local dev server only).
    backend/api/main.py reads both settings from the environment.
    """

    if cors_origins is None:
        cors_origins = allowed_origins(None)

    app = FastAPI(
        title="NORWICH//SIM API",
        description="Urban Traffic Simulation and routing API",
        version="0.2.0"
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.context = AppContext(
        load_graph(graph_file),
        max_live_runs=max_live,
        graph_file=graph_file,
    )

    for module in (
        routing,
        simulation,
        incidents,
        emergencies,
        stations,
        traffic,
        optimiser,
    ):
        app.include_router(module.router)

    return app
