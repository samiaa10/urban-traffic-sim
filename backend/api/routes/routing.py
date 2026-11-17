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


@router.get("/")
def root():
    return {
        "name": "NORWICH//SIM",
        "status": "online"
    }


@router.get("/nearest-node")
def nearest_node(
    latitude: float,
    longitude: float,
    context: AppContext = Depends(get_context)
):
    graph = context.graph
    nearest = nearest_node_id(graph, latitude, longitude)

    return {
        "node": nearest,
        "latitude": graph.nodes[nearest]["latitude"],
        "longitude": graph.nodes[nearest]["longitude"]
    }


@router.get("/route")
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
