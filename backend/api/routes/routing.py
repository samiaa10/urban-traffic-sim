"""Health check, nearest junction and point-to-point routes."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from backend.api.context import AppContext
from backend.api.routes.common import (
    get_context,
    run_by_id,
    nearest_node_id,
    path_points,
    require_node,
)
from backend.routing.a_star import a_star


router = APIRouter()


@router.get("/", tags=["Service"])
def root():
    """The API's name and that it is online."""

    return {
        "name": "NORWICH//SIM",
        "status": "online"
    }


@router.get("/health", tags=["Service"])
def health():
    """Liveness: the process answers (no database or other checks), for
    the hosting platform's health check."""

    return {"status": "ok"}


@router.get("/ready", tags=["Service"])
def ready(context: AppContext = Depends(get_context)):
    """Readiness: whether saving works. 200 with the database "ok" or "not
    configured"; 503 if a database is configured but cannot be reached
    (the API still runs, without saving)."""

    from fastapi.responses import JSONResponse

    if context.database is None:
        if context.database_error is not None:
            return JSONResponse(status_code=503, content={
                "status": "degraded", "database": "unreachable at start-up",
                "detail": context.database_error,
                "live_runs": len(context.registry),
            })
        return {"status": "ok", "database": "not configured", "live_runs": len(context.registry)}

    try:
        from sqlalchemy import text

        with context.database.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as error:   # the database went away while running
        return JSONResponse(status_code=503, content={
            "status": "degraded", "database": "unreachable",
            "detail": f"{type(error).__name__}".strip(),
            "live_runs": len(context.registry),
        })

    return {"status": "ok", "database": "ok", "live_runs": len(context.registry)}


@router.get("/nearest-node", tags=["Routing"])
def nearest_node(
    latitude: float,
    longitude: float,
    context: AppContext = Depends(get_context)
):
    """The road junction nearest a point (how the website turns a map click
    into a start or destination)."""

    graph = context.graph
    nearest = nearest_node_id(graph, latitude, longitude)

    return {
        "node": nearest,
        "latitude": graph.nodes[nearest]["latitude"],
        "longitude": graph.nodes[nearest]["longitude"]
    }


@router.get("/route", tags=["Routing"])
def route(
    start: int,
    destination: int,
    simulation_id: Optional[str] = None,
    context: AppContext = Depends(get_context)
):
    """A* route between two junctions.

    Traffic and closed roads come from the run named by simulation_id, or
    from the default run when it is left out (none if nothing is running).
    """

    graph = context.graph

    require_node(graph, start, "Start")
    require_node(graph, destination, "Destination")

    if simulation_id is not None:
        run = run_by_id(simulation_id, context)
    else:
        run = context.registry.default()

    traffic = None
    blocked_segments = set()

    if run is not None:
        with run.lock:
            traffic = run.simulation.get_segment_traffic()
            blocked_segments = set(run.simulation.get_blocked_segments())

    path, distance, nodes_explored = a_star(
        graph,
        start,
        destination,
        return_stats=True,
        traffic=traffic,
        blocked_segments=blocked_segments
    )

    if not path:
        raise HTTPException(
            status_code=404,
            detail="No route available (roads may be closed)"
        )

    return {
        "algorithm": "A*",
        "start": start,
        "destination": destination,
        "path": path,
        "path_coordinates": path_points(graph, path),
        "distance_metres": distance,
        "nodes_explored": nodes_explored
    }
