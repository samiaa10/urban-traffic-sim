"""Builds the FastAPI app: settings, shared context and every router."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.context import AppContext, load_graph
from backend.api.protection import install as install_protection
from backend.db.database import SchemaOutOfDate
from backend.api.recorder import METRICS_INTERVAL
from backend.api.routes import (
    emergencies,
    history,
    scenarios,
    incidents,
    optimiser,
    routing,
    simulation,
    stations,
    traffic,
)
from backend.api.settings import (
    DEFAULT_MAX_LIVE_RUNS,
    DEFAULT_MAX_STUDY_SCENARIOS,
    allowed_origins,
)


logger = logging.getLogger(__name__)


def open_database(url):
    """Connect, check the schema is migrated, and mark runs left 'active'
    by a server that stopped as interrupted (they cannot be resumed)."""

    from backend.db.database import Database
    from backend.db.repository import RunRepository

    database = Database(url)
    database.check_schema()
    database.interrupted_studies = []

    from backend.db.scenario_repository import ScenarioRepository

    with database.transaction() as session:
        interrupted = RunRepository(session).interrupt_active_runs()
        studies = ScenarioRepository(session).interrupt_unfinished()

    if interrupted:
        logger.info(
            "Marked %d simulation run(s) from before the restart as interrupted",
            len(interrupted),
        )
    if studies:
        logger.info(
            "Marked %d scenario study(ies) from before the restart as interrupted",
            len(studies),
        )
    database.interrupted_studies = studies

    return database


API_DESCRIPTION = """
Traffic simulation, routing and emergency-response planning on the real road
network of Norwich (OpenStreetMap, 5,058 junctions and 10,980 road segments).

**Runs.** `POST /simulation/start` plans a route and starts a simulation run
with its own ID. Every endpoint that acts on a run exists twice: at its
original path (`/simulation/step`), acting on the *default* run, and at
`/simulations/{simulation_id}/...`, acting on the named run. Several runs
can go at once, each with its own vehicles, closures and stations.

**Typical use.** Start a run, then `POST .../step` once a second (with
`seconds` to go faster); add a fleet or a rush hour, close roads or add
accidents, add stations and vehicles, and raise emergencies. Read the live
congestion, the emergency status and, with a database, the saved history.

**Planning.** The optimiser ranks station locations or chooses a set of
stations; `/optimiser/evaluations` tests its best answers in full traffic
simulations. `/scenarios` runs and compares whole scenarios, and studies of
hundreds of them in the background.

**Limits.** Expensive requests are rate-limited per client (429 with
`Retry-After`). Saving, history, studies and evaluations need PostgreSQL
(`DATABASE_URL`); without it they answer 503 and everything else works.
`GET /ready` says whether the database is reachable.
"""

API_TAGS = [
    {"name": "Service", "description": "Health and readiness."},
    {"name": "Routing", "description": "Junctions and A* routes on the road network."},
    {"name": "Simulation runs", "description": "Start, step, list, stop and restart runs; fleets and live congestion."},
    {"name": "Traffic", "description": "Rush-hour surges and dynamic rerouting."},
    {"name": "Incidents", "description": "Road closures and accidents, with rerouting of the vehicles they affect."},
    {"name": "Emergency services", "description": "Stations, ambulances, fire engines and police cars, dispatch and response times."},
    {"name": "Optimiser", "description": "Where emergency stations should go, and those answers tested in traffic."},
    {"name": "Scenarios", "description": "Whole scenarios simulated, compared and ranked; large studies in the background."},
    {"name": "History", "description": "What a run did, as saved in PostgreSQL."},
]


def create_app(
    graph_file,
    cors_origins=None,
    max_live=DEFAULT_MAX_LIVE_RUNS,
    database_url=None,
    metrics_interval=METRICS_INTERVAL,
    scenario_workers=0,
    max_study_scenarios=DEFAULT_MAX_STUDY_SCENARIOS,
    resume_studies=False,
    traffic_model="simple",
    rate_limit_per_minute=0,
    trust_proxy=False,
    log_format="text",
    run_idle_minutes=0,
):
    """A new app with its own road graph and (empty) set of live runs.

    cors_origins is a list of origins (None: the local dev server only).
    database_url (a SQLAlchemy URL) turns on saving runs; None keeps them
    in memory only. scenario_workers is how many processes run scenario
    studies (0: one at a time on a thread); resume_studies queues studies a
    restart interrupted again; traffic_model is the model new runs and
    scenarios use when they do not ask for one ("simple" here, so tests
    keep the original rules; the server's TRAFFIC_MODEL defaults to osm).
    backend/api/main.py reads these from the environment.
    """

    if cors_origins is None:
        cors_origins = allowed_origins(None)

    database, database_error = None, None
    if database_url:
        try:
            database = open_database(database_url)
        except SchemaOutOfDate:
            # A deployment mistake (migrations not run): refuse to start
            raise
        except Exception as error:   # unreachable, expired, wrong password
            # Keep the site up without saving rather than not at all; the
            # error is logged and /ready reports it
            database_error = f"{type(error).__name__}: {error}".splitlines()[0][:300]
            logger.error(
                "The database could not be opened, so runs will not be saved: %s",
                database_error,
            )

    @asynccontextmanager
    async def lifespan(app):
        yield
        # Shutting down: live runs end here, so record them as interrupted
        context = app.state.context
        context.end_all_runs("interrupted")
        if context.jobs is not None:
            context.jobs.shutdown()
        if database is not None:
            database.dispose()

    app = FastAPI(
        title="NORWICH//SIM API",
        description=API_DESCRIPTION,
        version="1.0.0",
        openapi_tags=API_TAGS,
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
        max_study_scenarios=max_study_scenarios,
        traffic_model=traffic_model,
        run_idle_seconds=run_idle_minutes * 60 or None,
    )
    # /ready is public: only the kind of error (the message can name the
    # database host and user); the full message is in the log
    app.state.context.database_error = database_error and database_error.split(":")[0]

    install_protection(
        app, rate_limit_per_minute=rate_limit_per_minute,
        trust_proxy=trust_proxy, log_format=log_format,
    )

    if database is not None:
        from backend.scenarios.jobs import JobManager

        context = app.state.context
        context.jobs = JobManager(
            database,
            graph_file,
            context.graph_sha256,
            scenario_workers,
            world=context.scenario_world() if scenario_workers == 0 else None,
        )

        if resume_studies:
            from backend.db.scenario_repository import ScenarioRepository

            for study_id in database.interrupted_studies:
                with database.transaction() as session:
                    requeued = ScenarioRepository(session).resume_study(study_id)
                if requeued:
                    context.jobs.submit_study(study_id)
                    logger.info(
                        "Resumed scenario study %s: %d scenario(s) queued again",
                        study_id, requeued,
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
        scenarios,
    ):
        app.include_router(module.router)

    return app
