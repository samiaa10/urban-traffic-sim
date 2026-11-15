"""Builds the FastAPI app: settings, shared context and every router."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.context import AppContext, load_graph
from backend.api.recorder import METRICS_INTERVAL
from backend.api.routes import (
    emergencies,
    history,
    incidents,
    optimiser,
    routing,
    simulation,
    stations,
    traffic,
)
from backend.api.settings import DEFAULT_MAX_LIVE_RUNS, allowed_origins


logger = logging.getLogger(__name__)


def open_database(url):
    """Connect, check the schema is migrated, and mark runs left 'active'
    by a server that stopped as interrupted (they cannot be resumed)."""

    from backend.db.database import Database
    from backend.db.repository import RunRepository

    database = Database(url)
    database.check_schema()

    with database.transaction() as session:
        interrupted = RunRepository(session).interrupt_active_runs()

    if interrupted:
        logger.info(
            "Marked %d simulation run(s) from before the restart as interrupted",
            len(interrupted),
        )

    return database


def create_app(
    graph_file,
    cors_origins=None,
    max_live=DEFAULT_MAX_LIVE_RUNS,
    database_url=None,
    metrics_interval=METRICS_INTERVAL,
):
    """A new app with its own road graph and (empty) set of live runs.

    cors_origins is a list of origins (None: the local dev server only).
    database_url (a SQLAlchemy URL) turns on saving runs; None keeps them
    in memory only. backend/api/main.py reads these from the environment.
    """

    if cors_origins is None:
        cors_origins = allowed_origins(None)

    database = open_database(database_url) if database_url else None

    @asynccontextmanager
    async def lifespan(app):
        yield
        # Shutting down: live runs end here, so record them as interrupted
        app.state.context.end_all_runs("interrupted")
        if database is not None:
            database.dispose()

    app = FastAPI(
        title="NORWICH//SIM API",
        description="Urban Traffic Simulation and routing API",
        version="0.3.0",
        lifespan=lifespan,
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
        database=database,
        metrics_interval=metrics_interval,
    )

    for module in (
        routing,
        simulation,
        incidents,
        emergencies,
        stations,
        traffic,
        optimiser,
        history,
    ):
        app.include_router(module.router)

    return app
