"""Road closures and accidents."""

import math
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from backend.api.context import AppContext
from backend.api.routes.common import (
    default_run,
    get_context,
    point_pair,
    require_road,
    road_between,
    run_route,
    segment_json,
)
from backend.api.sessions import LiveRun
from backend.simulation import rerouting
from backend.simulation.accidents import SEVERITIES


router = APIRouter()


@run_route(router, "post", "/incident/block", "/incidents/block")
def block_incident(
    start: int,
    destination: int,
    run: LiveRun = Depends(default_run),
    context: AppContext = Depends(get_context)
):
    graph = context.graph

    require_road(graph, start, destination)
    segment = point_pair(graph, start, destination)

    with run.lock:
        run.simulation.block_segment(segment)

        # Event -> affected road -> only the vehicles that use it get new
        # routes, planned with the same travel-time costs as everything else
        rerouting_stats = rerouting.reroute_affected_vehicles(
            graph,
            run.simulation,
            context.node_by_point,
            route_finder=run.traffic.route_finder()
        )

        # Vehicles that could not get round stay flagged and keep trying
        run.traffic.process_events()

    return {
        "status": "incident created",
        "blocked_segment": segment,
        "rerouting": rerouting_stats
    }


@run_route(router, "post", "/incident/unblock", "/incidents/unblock")
def unblock_incident(
    start: int,
    destination: int,
    run: LiveRun = Depends(default_run),
    context: AppContext = Depends(get_context)
):
    graph = context.graph

    require_road(graph, start, destination)
    segment = point_pair(graph, start, destination)

    with run.lock:
        run.simulation.unblock_segment(segment)

        # Vehicles that might now have a quicker way re-plan on the next step
        recheck = run.traffic.process_events()

    return {
        "status": "incident removed",
        "blocked_segment": segment,
        "vehicles_to_recheck": recheck
    }


@run_route(router, "get", "/incidents", "/incidents")
def get_incidents(run: LiveRun = Depends(default_run)):

    with run.lock:
        simulation = run.simulation

        incidents = [
            segment_json(segment)
            for segment in simulation.get_blocked_segments()
        ]

        # Accidents leave the road open but slow (see /incident/accident)
        accidents = [
            accident.describe(simulation.time)
            for accident in simulation.accidents.values()
        ]

    return {
        "incidents": incidents,
        "accidents": accidents
    }


@run_route(router, "post", "/incident/accident", "/incidents/accident")
def create_accident(
    start: int,
    destination: int,
    severity: str = "moderate",
    duration_seconds: Optional[float] = None,
    run: LiveRun = Depends(default_run),
    context: AppContext = Depends(get_context)
):
    """Start an accident on the road between two adjacent junctions.

    Unlike /incident/block the road stays open: traffic slows down and the
    road jams with fewer vehicles, for duration_seconds of simulated time
    (default depends on severity). Vehicles whose route uses the road
    re-plan on the next step if another way is meaningfully faster.
    """

    if severity not in SEVERITIES:
        raise HTTPException(
            status_code=400,
            detail="severity must be one of " + ", ".join(SEVERITIES)
        )

    if duration_seconds is not None and not (
        math.isfinite(duration_seconds) and 0 < duration_seconds <= 86400
    ):
        raise HTTPException(
            status_code=400,
            detail="duration_seconds must be above 0 and at most 86400"
        )

    segment = road_between(context, start, destination)

    with run.lock:
        simulation = run.simulation
        accident = simulation.add_accident(segment, severity, duration_seconds)

        return {
            "status": "accident created",
            "accident": accident.describe(simulation.time),
            "vehicles_to_recheck": run.traffic.process_events()
        }


@run_route(
    router, "post", "/incident/accident/clear", "/incidents/accident/clear"
)
def clear_accident(
    start: int,
    destination: int,
    run: LiveRun = Depends(default_run),
    context: AppContext = Depends(get_context)
):
    """Clear an accident before its time is up."""

    segment = road_between(context, start, destination)

    with run.lock:
        if not run.simulation.clear_accident(segment):
            raise HTTPException(
                status_code=404,
                detail="There is no accident on that road"
            )

        return {
            "status": "accident cleared",
            "segment": segment_json(segment),
            "vehicles_to_recheck": run.traffic.process_events()
        }
