"""Emergencies: dispatching the nearest ambulance and following it."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from backend.api.context import AppContext
from backend.api.routes.common import (
    default_run,
    get_context,
    run_route,
)
from backend.api.sessions import LiveRun
from backend.simulation import dispatch


logger = logging.getLogger(__name__)

router = APIRouter(tags=["Emergency services"])


def record_outcome(run, outcome):
    """Save what dispatch_emergency did (dispatched, queued or refused) to
    the run's history. Call while holding the run's lock."""

    emergency = outcome["emergency"]
    status = outcome["status"]
    if status == "queued":
        run.recorder.emergency_queued(emergency)
    elif status == "dispatched":
        run.recorder.emergency_dispatched(
            emergency, outcome["vehicle_index"], outcome["distance"], outcome["nodes_explored"]
        )
    else:
        run.recorder.emergency_refused(emergency, status, outcome["vehicle_index"])


@run_route(router, "post", "/emergency/create", "/emergencies")
def create_emergency(
    latitude: float,
    longitude: float,
    emergency_type: str = "ambulance",
    run: LiveRun = Depends(default_run),
    context: AppContext = Depends(get_context)
):
    """Raise an emergency for one service (ambulance, fire or police) and
    send a vehicle (services.py). In a run with the osm traffic model, the
    free vehicle that would arrive first is sent, and if none is free the
    emergency waits in a queue (202, "emergency queued")."""

    from backend.api.routes.stations import known_service

    known_service(emergency_type, "emergency_type")

    with run.lock:
        outcome = dispatch.dispatch_emergency(
            context.graph, run.simulation, latitude, longitude, emergency_type
        )
        emergency = outcome["emergency"]
        record_outcome(run, outcome)

        if outcome["status"] == "queued":
            waiting = len(run.simulation.queued_emergencies())
            return JSONResponse(status_code=202, content={
                "status": "emergency queued",
                "emergency_type": emergency_type,
                "emergency_location": {"latitude": latitude, "longitude": longitude},
                "queue_position": waiting,
            })

        if outcome["status"] == "no_vehicle":
            raise HTTPException(
                status_code=404,
                detail="No available emergency vehicle"
            )

        if outcome["status"] == "no_route":
            raise HTTPException(
                status_code=404,
                detail="No route to emergency"
            )

        path = outcome["path"]
        distance = outcome["distance"]
        nodes_explored = outcome["nodes_explored"]
        new_route = outcome["route"]
        vehicle_id = outcome["vehicle_index"]

    logger.info(
        "Emergency (%s) at (%s, %s): dispatched vehicle %d, "
        "route %.0f m, %d junctions, %d nodes explored",
        emergency_type,
        latitude,
        longitude,
        vehicle_id,
        distance,
        len(path),
        nodes_explored,
    )
    logger.debug("Emergency route: %s", new_route)

    return {
        "status": "emergency dispatched",
        "emergency_type": emergency_type,
        "vehicle_id": vehicle_id,
        "emergency_location": {
            "latitude": latitude,
            "longitude": longitude
        },
        "route_distance_metres": distance,
        "nodes_explored": nodes_explored,
        "route": new_route
    }


@run_route(router, "get", "/emergency/status", "/emergencies/status")
def emergency_status(run: LiveRun = Depends(default_run)):
    """Live status of every emergency (state, ETA, response time, reroutes)."""

    with run.lock:
        simulation = run.simulation

        return {
            "simulation_time": simulation.time,
            "emergencies": [
                dispatch.emergency_status(simulation, emergency)
                for emergency in simulation.get_emergencies()
            ]
        }


def emergency_for(run, result):
    """The emergency whose ambulance /emergency/block-ahead closed a road
    in front of (None if no road was closed)."""

    vehicle_id = result.get("vehicle_id")

    if vehicle_id is None:
        return None

    vehicle = run.simulation.vehicles[vehicle_id]

    for emergency in run.simulation.get_emergencies():
        if emergency.active and emergency.assigned_vehicle is vehicle:
            return emergency

    return None


BLOCK_AHEAD_ERRORS = {
    "no_emergency": (404, "No ambulance is on its way to an emergency"),
    "too_close": (400, "The ambulance is too close to its destination to close a road ahead"),
    "no_detour": (400, "Every road ahead is the only way there, so none was closed"),
    "off_network": (400, "The ambulance route is not on the road network"),
}


@run_route(
    router, "post", "/emergency/block-ahead", "/emergencies/block-ahead"
)
def emergency_block_road_ahead(
    run: LiveRun = Depends(default_run),
    context: AppContext = Depends(get_context)
):
    """Close a road on the ambulance's route; it is rerouted around it."""

    with run.lock:
        result = dispatch.block_road_ahead(
            context.graph,
            run.simulation,
            context.node_by_point,
            route_finder=run.traffic.route_finder()
        )
        run.traffic.process_events()

        # The closure is saved linked to the emergency it was made for
        run.recorder.sync(closure_emergency=emergency_for(run, result))

    if result["outcome"] in BLOCK_AHEAD_ERRORS:
        status_code, detail = BLOCK_AHEAD_ERRORS[result["outcome"]]
        raise HTTPException(status_code=status_code, detail=detail)

    start_point, end_point = result["blocked_segment"]

    return {
        "status": "road closed, ambulance rerouted",
        "vehicle_id": result["vehicle_id"],
        "blocked_segment": {
            "start": {"latitude": start_point[0], "longitude": start_point[1]},
            "end": {"latitude": end_point[0], "longitude": end_point[1]}
        },
        "distance_before_metres": result["distance_before_metres"],
        "distance_after_metres": result["distance_after_metres"],
        "extra_metres": result["extra_metres"],
        "extra_seconds": result["extra_seconds"],
        "rerouting": result["rerouting"],
        "route": result["route"]
    }
