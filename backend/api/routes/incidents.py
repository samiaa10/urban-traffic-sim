"""Road closures, accidents, floods and bridge failures, and how reachable
the city is while they last."""

import math
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

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


router = APIRouter(tags=["Incidents"])


@run_route(router, "post", "/incident/block", "/incidents/block")
def block_incident(
    start: int,
    destination: int,
    run: LiveRun = Depends(default_run),
    context: AppContext = Depends(get_context)
):
    """Close the road from `start` to `destination` (adjacent junctions). Only
    the vehicles whose routes use it re-plan, at once; answers how many were
    affected, rerouted or stranded."""

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
        run.recorder.sync()

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
    """Reopen a closed road. Vehicles that might now have a quicker way re-plan
    on the next step."""

    graph = context.graph

    require_road(graph, start, destination)
    segment = point_pair(graph, start, destination)

    with run.lock:
        run.simulation.unblock_segment(segment)

        # Vehicles that might now have a quicker way re-plan on the next step
        recheck = run.traffic.process_events()
        run.recorder.sync()

    return {
        "status": "incident removed",
        "blocked_segment": segment,
        "vehicles_to_recheck": recheck
    }


@run_route(router, "get", "/incidents", "/incidents")
def get_incidents(run: LiveRun = Depends(default_run)):
    """The run's closed roads, and its accidents with their severity, lost
    capacity and time left."""

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
        recheck = run.traffic.process_events()
        run.recorder.sync()

        return {
            "status": "accident created",
            "accident": accident.describe(simulation.time),
            "vehicles_to_recheck": recheck
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

        recheck = run.traffic.process_events()
        run.recorder.sync(manual_clear=True)

        return {
            "status": "accident cleared",
            "segment": segment_json(segment),
            "vehicles_to_recheck": recheck
        }



# ------------------------------------------------------------
# Floods and bridge failures (simulation/disruptions.py)
# ------------------------------------------------------------

def _reroute(run, context):
    stats = rerouting.reroute_affected_vehicles(
        context.graph, run.simulation, context.node_by_point,
        route_finder=run.traffic.route_finder(),
    )
    run.traffic.process_events()
    run.recorder.sync()
    return stats


@run_route(router, "post", "/incident/flood", "/incidents/flood")
def create_flood(
    latitude: float,
    longitude: float,
    radius_metres: float = Query(250, ge=20, le=5000),
    severity: Literal["minor", "moderate", "severe", "closed"] = "closed",
    duration_seconds: float = Query(1200, gt=0, le=86400),
    recovery_seconds: float = Query(600, ge=0, le=86400),
    run: LiveRun = Depends(default_run),
    context: AppContext = Depends(get_context),
):
    """Flood every road inside a circle: closed or slowed for
    duration_seconds, then recovering gradually over recovery_seconds. A
    transport disruption, not a flood prediction: the area and severity are
    yours to choose."""

    from backend.simulation.disruptions import segments_in_area

    if not (math.isfinite(latitude) and math.isfinite(longitude)):
        raise HTTPException(status_code=422, detail="latitude and longitude must be numbers")
    roads = segments_in_area(context.network, latitude, longitude, radius_metres)
    if not roads:
        raise HTTPException(status_code=422, detail="No road lies entirely inside that circle: make it bigger")

    with run.lock:
        flood = run.simulation.add_flood(roads, severity, duration_seconds, recovery_seconds)
        stats = _reroute(run, context) if flood.closes_roads else None
        if not flood.closes_roads:
            run.traffic.process_events()
        return {
            "status": "flood created",
            "flood": flood.describe(run.simulation.time),
            "rerouting": stats,
        }


@run_route(router, "get", "/incident/floods", "/incidents/floods")
def list_floods(run: LiveRun = Depends(default_run)):
    """Every flood now: its roads, stage (flooded or recovering) and how far
    it has recovered."""
    with run.lock:
        now = run.simulation.time
        return {"floods": [flood.describe(now) for flood in run.simulation.floods]}


@run_route(router, "post", "/incident/flood/clear", "/incidents/flood/clear")
def clear_flood(
    flood_id: int,
    run: LiveRun = Depends(default_run),
    context: AppContext = Depends(get_context),
):
    """End a flood at once: its roads reopen fully recovered."""
    with run.lock:
        if not run.simulation.clear_flood(flood_id):
            raise HTTPException(status_code=404, detail="No such flood")
        run.traffic.process_events()
        run.recorder.sync()
    return {"status": "flood cleared"}


@run_route(router, "post", "/incident/bridges", "/incidents/bridges")
def bridge_failure(
    fail: bool = True,
    names: Optional[List[str]] = Query(None, description="road names of the bridges; default: all"),
    run: LiveRun = Depends(default_run),
    context: AppContext = Depends(get_context),
):
    """Take the map's bridges (OpenStreetMap bridge=* roads) out of use, or
    put them back (fail=false)."""

    from backend.simulation.infrastructure import load_bridges

    position = context.network.position
    roads = [
        tuple(sorted((position[a], position[b])))
        for a, b, name in load_bridges()
        if a in position and b in position and (names is None or name in names)
    ]
    with run.lock:
        for road in roads:
            if fail:
                run.simulation.block_segment(road, source="failure:bridges")
            else:
                run.simulation.unblock_segment(road, source="failure:bridges")
        stats = _reroute(run, context) if fail else None
        if not fail:
            run.traffic.process_events()
            run.recorder.sync()
    return {"status": "bridges out of use" if fail else "bridges back in use",
            "bridges": len(roads), "rerouting": stats}


@run_route(router, "get", "/resilience/accessibility", "/resilience/accessibility")
def accessibility(
    run: LiveRun = Depends(default_run),
    context: AppContext = Depends(get_context),
):
    """How reachable the city is now against the same roads undisrupted:
    junctions still connected, extra drive time, the worst-hit areas, and
    the run's stations' reach (scenarios/resilience.py)."""

    from backend.scenarios.resilience import live_accessibility
    from backend.simulation.traffic_manager import car_speed

    with run.lock:
        return live_accessibility(
            context.graph, context.network, run.traffic.demand.nodes,
            run.simulation, car_speed(run.simulation),
        )
