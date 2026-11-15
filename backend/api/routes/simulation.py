"""Starting, listing, stepping and stopping simulation runs."""

import logging
import random
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from backend.api.context import AppContext
from backend.api.routes.common import (
    default_run,
    get_context,
    path_points,
    require_node,
    run_route,
)
from backend.api.sessions import LiveRun
from backend.routing.a_star import a_star
from backend.simulation import rerouting
from backend.simulation.simulation import Simulation
from backend.simulation.traffic_manager import TrafficManager


logger = logging.getLogger(__name__)

router = APIRouter()


def build_run(context, start, destination):
    """A new run with two vehicles: start -> destination and back.

    Returns the run and the details /simulation/start reports.
    """

    graph = context.graph

    require_node(graph, start, "Start")
    require_node(graph, destination, "Destination")

    # Vehicle 1: start -> destination
    path, distance, nodes_explored = a_star(
        graph,
        start,
        destination,
        return_stats=True
    )

    if not path:
        raise HTTPException(
            status_code=404,
            detail="No route between start and destination"
        )

    simulation = Simulation(path_points(graph, path))

    # Vehicle 2: the way back (one-way streets mean it may not exist, and
    # a vehicle with no route would crash the simulation)
    second_path, _, _ = a_star(
        graph,
        destination,
        start,
        return_stats=True
    )

    second_path_coordinates = path_points(graph, second_path)

    if second_path_coordinates:
        simulation.add_vehicle(second_path_coordinates)

    # Fleets, surges, dynamic rerouting; vehicles drive real road lengths.
    # One at a time: the first one builds the shared landmark tables.
    with context.network_lock:
        traffic = TrafficManager(context.network, simulation)

    run = LiveRun(simulation, traffic, start, destination)

    details = {
        "distance_metres": distance,
        "nodes_explored": nodes_explored,
        "vehicle_position": simulation.vehicles[0].get_current_location(),
        "simulation_id": run.id,
    }

    return run, details


def add_run(context, run, make_default):
    for stopped in context.registry.add(run, make_default=make_default):
        logger.info("Simulation %s stopped", stopped.id)


# ------------------------------------------------------------
# The original single-run start
# ------------------------------------------------------------

@router.post("/simulation/start")
def start_simulation(
    start: int,
    destination: int,
    context: AppContext = Depends(get_context)
):
    """Start a run and make it the default (the previous default stops).

    The original endpoints (/simulation/step and so on) act on this run.
    The answer includes its simulation_id for the /simulations/... ones.
    """

    run, details = build_run(context, start, destination)
    add_run(context, run, make_default=True)

    logger.info("Simulation %s started (%s -> %s)", run.id, start, destination)

    return {"status": "simulation started", **details}


# ------------------------------------------------------------
# Runs by ID
# ------------------------------------------------------------

@router.post("/simulations", status_code=201)
def create_simulation(
    start: int,
    destination: int,
    context: AppContext = Depends(get_context)
):
    """Start an independent run. It does not replace the default run."""

    run, details = build_run(context, start, destination)
    add_run(context, run, make_default=False)

    logger.info("Simulation %s created (%s -> %s)", run.id, start, destination)

    return {"status": "simulation started", **details}


@router.get("/simulations")
def list_simulations(context: AppContext = Depends(get_context)):
    """The live runs, newest first."""

    registry = context.registry
    summaries = []

    for run in registry.runs():
        with run.lock:
            summaries.append(run.summary(is_default=registry.is_default(run)))

    return {
        "max_live": registry.max_live,
        "simulations": summaries,
    }


@router.get("/simulations/{simulation_id}")
def get_simulation(
    simulation_id: str,
    context: AppContext = Depends(get_context)
):
    run = context.registry.get(simulation_id)

    if run is None:
        raise HTTPException(status_code=404, detail="Simulation not found")

    with run.lock:
        return run.summary(is_default=context.registry.is_default(run))


@router.delete("/simulations/{simulation_id}")
def stop_simulation(
    simulation_id: str,
    context: AppContext = Depends(get_context)
):
    """Stop a run and free its memory."""

    run = context.registry.remove(simulation_id)

    if run is None:
        raise HTTPException(status_code=404, detail="Simulation not found")

    logger.info("Simulation %s stopped on request", run.id)

    return {"status": "simulation stopped", "simulation_id": run.id}


# ------------------------------------------------------------
# Driving a run
# ------------------------------------------------------------

@run_route(router, "get", "/simulation/state", "/state")
def simulation_state(run: LiveRun = Depends(default_run)):

    with run.lock:
        vehicle = run.simulation.vehicles[0]

        return {
            "vehicle_position": vehicle.get_current_location(),
            "distance_travelled": vehicle.distance_travelled,
            "finished": vehicle.finished
        }


@run_route(router, "post", "/simulation/step", "/step")
def simulation_step(run: LiveRun = Depends(default_run)):

    with run.lock:
        simulation = run.simulation

        # Run surges, move all vehicles, then react to changes and re-plan
        rerouted = run.traffic.step()

        vehicles = []

        for index, vehicle in enumerate(simulation.vehicles):

            location = vehicle.get_current_location()

            vehicles.append({
                "vehicle_id": index,
                "latitude": location["latitude"],
                "longitude": location["longitude"],
                "distance_travelled": vehicle.distance_travelled,
                "finished": vehicle.finished,
                "vehicle_type": vehicle.vehicle_type,
                "on_emergency": vehicle.on_emergency,
                # Traffic: 1.0 = free flowing, lower = held up by others
                "speed_factor": vehicle.speed_factor,
                "journey_seconds": vehicle.journey_seconds,
                # Times this trip's route was changed (closures, accidents,
                # or a meaningfully faster route appearing)
                "reroutes": vehicle.reroutes
            })

        simulation_time = simulation.time

    # Already plain numbers, strings and booleans, so skip FastAPI's
    # general-purpose encoder: with 2,000 vehicles it took about 55 ms of
    # every step just to walk this list. The JSON is the same.
    return JSONResponse({
        "vehicles": vehicles,
        "simulation_time": simulation_time,
        "rerouted_this_step": rerouted
    })


@run_route(router, "get", "/simulation/congestion", "/congestion")
def simulation_congestion(run: LiveRun = Depends(default_run)):

    with run.lock:
        congestion = run.simulation.get_congestion()

    congestion_segments = []

    for segment, data in congestion.items():

        start_point, end_point = segment

        congestion_segments.append({
            "start": {
                "latitude": start_point[0],
                "longitude": start_point[1]
            },
            "end": {
                "latitude": end_point[0],
                "longitude": end_point[1]
            },
            "vehicle_count": data["vehicle_count"],
            # Includes any accident's lost capacity (see /incidents)
            "level": data["level"]
        })

    return {
        "congestion": congestion_segments
    }


@run_route(router, "post", "/simulation/reroute", "/reroute")
def reroute_vehicle(
    vehicle_id: int = 0,
    run: LiveRun = Depends(default_run),
    context: AppContext = Depends(get_context)
):

    with run.lock:
        simulation = run.simulation

        if vehicle_id < 0 or vehicle_id >= len(simulation.vehicles):
            raise HTTPException(
                status_code=404,
                detail="Vehicle not found"
            )

        vehicle = simulation.vehicles[vehicle_id]

        if vehicle.finished:
            raise HTTPException(
                status_code=400,
                detail="Vehicle has already finished"
            )

        result = rerouting.reroute_vehicle(
            context.graph,
            vehicle,
            context.node_by_point,
            traffic=simulation.get_traffic_density(),
            blocked_segments=simulation.get_blocked_segments(),
            route_finder=run.traffic.route_finder()
        )

    if result["status"] == "skipped":
        raise HTTPException(
            status_code=400,
            detail="Vehicle route is not on the road network"
        )

    if result["status"] == "stranded":
        raise HTTPException(
            status_code=404,
            detail="No alternative route available"
        )

    return {
        "status": "vehicle rerouted",
        "vehicle_id": vehicle_id,
        "distance_metres": result["distance"],
        "nodes_explored": result["nodes_explored"],
        "route": result["route"]
    }


@run_route(router, "post", "/simulation/fleet", "/fleet")
def spawn_fleet(
    count: int = 100,
    seed: Optional[int] = None,
    run: LiveRun = Depends(default_run)
):
    """Add `count` vehicles (1 to 2000) with trips across Norwich.

    Trips come from the demand model (origins spread over the road network,
    about half of destinations drawn towards the city centre). Each vehicle
    gets the fastest route under current traffic, accidents and closures.
    The same seed gives the same trips.
    """

    if count < 1 or count > 2000:
        raise HTTPException(
            status_code=400,
            detail="count must be between 1 and 2000"
        )

    generator = random.Random(seed) if seed is not None else None

    with run.lock:
        outcome = run.traffic.spawn_vehicles(count, generator)
        total_vehicles = len(run.simulation.vehicles)

    logger.info(
        "Spawned %d of %d vehicles in %.2f s",
        outcome["spawned"], count, outcome["seconds"]
    )

    return {
        "status": "fleet added",
        "requested": outcome["requested"],
        "spawned": outcome["spawned"],
        "failed": outcome["failed"],
        "seconds": outcome["seconds"],
        "total_vehicles": total_vehicles
    }
