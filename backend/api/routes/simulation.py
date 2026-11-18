"""Starting, listing, stepping and stopping simulation runs."""

import logging
import random
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from backend.api.context import AppContext
from backend.api.routes.common import (
    default_run,
    get_context,
    parse_run_id,
    path_points,
    require_node,
    run_route,
)
from backend.api.sessions import LiveRun
from backend.routing.a_star import a_star
from backend.simulation import rerouting
from backend.simulation.road_model import make_road_model
from backend.simulation.simulation import Simulation
from backend.simulation.traffic_manager import TrafficManager, car_speed


logger = logging.getLogger(__name__)

router = APIRouter(tags=["Simulation runs"])


TrafficModelQuery = Query(
    None,
    description="simple (original rules) or osm (OpenStreetMap speed limits, "
    "lanes and per-direction traffic); default: the server's TRAFFIC_MODEL",
)


def build_run(context, start, destination, restarted_from=None, traffic_model=None):
    """A new run with two vehicles: start -> destination and back.

    Returns the run and the details /simulation/start reports.
    restarted_from (a UUID) links it to the ended run it restarts.
    traffic_model: "simple" or "osm" (simulation/road_model.py); None uses
    the server's default.
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

    model_name = traffic_model or context.traffic_model

    # Built once per map and shared, so made under the same lock as the
    # landmark tables below
    with context.network_lock:
        road_model = make_road_model(model_name, context.network)

    simulation = Simulation(path_points(graph, path), road_model=road_model)

    # In the osm model a car's speed is set by each road's limit
    speed = car_speed(simulation)
    simulation.vehicles[0].speed = speed

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
        simulation.add_vehicle(second_path_coordinates, speed)

    # Fleets, surges, dynamic rerouting; vehicles drive real road lengths.
    # One at a time: the first one builds the shared landmark tables.
    with context.network_lock:
        traffic = TrafficManager(context.network, simulation)

    run = LiveRun(simulation, traffic, start, destination)
    run.restarted_from = restarted_from

    # Saved before anyone can use it; a run that cannot be saved is not
    # started (it would have no history)
    if not context.attach_recorder(run):
        raise HTTPException(
            status_code=503,
            detail="The simulation could not be saved (database unavailable)"
        )

    details = {
        "distance_metres": distance,
        "nodes_explored": nodes_explored,
        "vehicle_position": simulation.vehicles[0].get_current_location(),
        "simulation_id": run.id,
        "traffic_model": road_model.name,
    }

    return run, details


def add_run(context, run, make_default):
    for stopped, reason in context.registry.add(run, make_default=make_default):
        context.end_run(stopped, reason)
        logger.info("Simulation %s %s", stopped.id, reason)


# ------------------------------------------------------------
# The original single-run start
# ------------------------------------------------------------

@router.post("/simulation/start")
def start_simulation(
    start: int,
    destination: int,
    traffic_model: Optional[Literal["simple", "osm"]] = TrafficModelQuery,
    replace_default: bool = Query(
        True,
        description="false: start the run without making it the default, so it "
        "replaces nobody else's (the website does this and uses the run's id)",
    ),
    context: AppContext = Depends(get_context)
):
    """Start a run and make it the default (the previous default stops).

    The original endpoints (/simulation/step and so on) act on this run.
    The answer includes its simulation_id for the /simulations/... ones.
    With replace_default=false the run is started on its own, like POST
    /simulations: several visitors can then each have a run.
    """

    run, details = build_run(context, start, destination, traffic_model=traffic_model)
    add_run(context, run, make_default=replace_default)

    logger.info("Simulation %s started (%s -> %s)", run.id, start, destination)

    return {"status": "simulation started", **details}


# ------------------------------------------------------------
# Runs by ID
# ------------------------------------------------------------

@router.post("/simulations", status_code=201)
def create_simulation(
    start: int,
    destination: int,
    traffic_model: Optional[Literal["simple", "osm"]] = TrafficModelQuery,
    context: AppContext = Depends(get_context)
):
    """Start an independent run. It does not replace the default run."""

    run, details = build_run(context, start, destination, traffic_model=traffic_model)
    add_run(context, run, make_default=False)

    logger.info("Simulation %s created (%s -> %s)", run.id, start, destination)

    return {"status": "simulation started", **details}


RUN_STATUSES = ("active", "stopped", "replaced", "interrupted")


def stored_summary(row, live_run=None, registry=None):
    from backend.db.serializers import run_summary

    summary = run_summary(row)

    if live_run is not None:
        # Live: the in-memory figures are newer than the saved ones
        with live_run.lock:
            summary.update(live_run.summary(registry.is_default(live_run)))

    return summary


@router.get("/simulations")
def list_simulations(
    status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    context: AppContext = Depends(get_context)
):
    """Simulation runs, newest first.

    With a database: every saved run, live or not (filter with status:
    active, stopped, replaced or interrupted; page with limit/offset).
    Without one: the live runs only.
    """

    registry = context.registry

    if status is not None and status not in RUN_STATUSES:
        raise HTTPException(
            status_code=400,
            detail="status must be one of " + ", ".join(RUN_STATUSES)
        )

    if context.database is None:
        live = [r for r in registry.runs()]
        if status not in (None, "active"):
            live = []

        summaries = []
        for run in live[offset:offset + limit]:
            with run.lock:
                summaries.append(run.summary(is_default=registry.is_default(run)))

        return {
            "persistence": False,
            "max_live": registry.max_live,
            "total": len(live),
            "limit": limit,
            "offset": offset,
            "simulations": summaries,
        }

    from backend.db.models import RunStatus
    from backend.db.repository import RunRepository

    with context.database.transaction() as session:
        rows, total = RunRepository(session).list_runs(
            status=RunStatus(status) if status else None,
            limit=limit, offset=offset,
        )

    return {
        "persistence": True,
        "max_live": registry.max_live,
        "total": total,
        "limit": limit,
        "offset": offset,
        "simulations": [
            stored_summary(row, registry.get(str(row.id)), registry)
            for row in rows
        ],
    }


@router.get("/simulations/{simulation_id}")
def get_simulation(
    simulation_id: str,
    context: AppContext = Depends(get_context)
):
    """One run: live, or (with a database) one that has ended."""

    run_uuid = parse_run_id(simulation_id)
    run = context.registry.get(str(run_uuid))

    if context.database is not None:
        from backend.db.repository import RunRepository

        with context.database.transaction() as session:
            row = RunRepository(session).get_run(run_uuid)

        if row is not None:
            return stored_summary(row, run, context.registry)

    if run is None:
        raise HTTPException(status_code=404, detail="Simulation not found")

    with run.lock:
        return run.summary(is_default=context.registry.is_default(run))


@router.delete("/simulations/{simulation_id}")
def stop_simulation(
    simulation_id: str,
    purge: bool = False,
    context: AppContext = Depends(get_context)
):
    """Stop a live run (its history is kept), or with purge=true also
    delete it and its history from the database (live or not)."""

    run_uuid = parse_run_id(simulation_id)
    run = context.registry.remove(str(run_uuid))

    if run is not None:
        context.end_run(run, "stopped")
        logger.info("Simulation %s stopped on request", run.id)

    deleted = False

    if context.database is not None:
        from backend.db.repository import RunRepository

        with context.database.transaction() as session:
            repo = RunRepository(session)

            if purge:
                deleted = repo.delete_run(run_uuid)
            elif run is None and repo.get_run(run_uuid) is not None:
                raise HTTPException(
                    status_code=409,
                    detail="Simulation has already ended; "
                           "use purge=true to delete its history"
                )

    if run is None and not deleted:
        raise HTTPException(status_code=404, detail="Simulation not found")

    if deleted:
        return {"status": "simulation deleted", "simulation_id": str(run_uuid)}

    return {"status": "simulation stopped", "simulation_id": run.id}


@router.post("/simulations/{simulation_id}/restart", status_code=201)
def restart_simulation(
    simulation_id: str,
    make_default: bool = True,
    context: AppContext = Depends(get_context)
):
    """Start a new run from an ended run's saved setup.

    A run cannot carry on after it ends (its vehicles were only in
    memory), but its setup is saved, so it can be started again: the same
    route, rerouting setting, stations and ambulances, the closures and
    accidents still in place when it ended (accidents with the time they
    had left), and its fleets (with their saved seeds, so the same trips).
    Vehicles start from the beginning; surges are not repeated. The new
    run is linked to the old one (restarted_from) and, by default, becomes
    the default run, as /simulation/start would.
    """

    if context.database is None:
        raise HTTPException(
            status_code=503,
            detail=f"Restarting needs a database ({context.no_database_reason})"
        )

    run_uuid = parse_run_id(simulation_id)

    if context.registry.get(str(run_uuid)) is not None:
        raise HTTPException(
            status_code=409,
            detail="The simulation is still running; only an ended one can be restarted"
        )

    from backend.db.models import IncidentEndReason, IncidentKind
    from backend.db.repository import RunRepository

    with context.database.transaction() as session:
        repo = RunRepository(session)
        row = repo.get_run(run_uuid)

        if row is None:
            raise HTTPException(status_code=404, detail="Simulation not found")

        setup = {
            "start": row.start_node,
            "destination": row.destination_node,
            "rerouting": row.rerouting_enabled,
            "traffic_model": row.traffic_model,
            "stations": [
                {
                    "latitude": s.latitude, "longitude": s.longitude,
                    "type": s.station_type,
                    "vehicles": [(v.vehicle_type, v.speed) for v in s.vehicles],
                }
                for s in row.stations
            ],
            "incidents": [
                {
                    "kind": i.kind,
                    "segment": (
                        (i.start_latitude, i.start_longitude),
                        (i.end_latitude, i.end_longitude),
                    ),
                    "severity": i.severity,
                    "remaining": (
                        max(1.0, i.planned_end_sim_time - i.ended_sim_time)
                        if i.kind == IncidentKind.ACCIDENT else None
                    ),
                }
                for i in repo.incidents(run_uuid)
                if i.end_reason == IncidentEndReason.RUN_ENDED
            ],
            "fleets": [(f.requested, f.seed) for f in repo.fleet_spawns(run_uuid)],
        }

    run, details = build_run(
        context, setup["start"], setup["destination"], run_uuid,
        traffic_model=setup["traffic_model"],
    )
    simulation, traffic = run.simulation, run.traffic
    applied = {"stations": 0, "ambulances": 0, "closures": 0, "accidents": 0, "fleet_vehicles": 0}

    with run.lock:
        if not setup["rerouting"]:
            traffic.policy.enabled = False
            run.recorder.rerouting_changed(False)

        for index, station_setup in enumerate(setup["stations"]):
            station = simulation.add_station(
                station_setup["latitude"], station_setup["longitude"], station_setup["type"]
            )
            run.recorder.station_added(index, station)
            applied["stations"] += 1
            location = station.get_location()
            for vehicle_type, speed in station_setup["vehicles"]:
                vehicle = simulation.add_emergency_vehicle(
                    [location, location], station, speed=speed, vehicle_type=vehicle_type
                )
                run.recorder.vehicle_added(index, simulation.vehicles.index(vehicle), vehicle)
                applied["ambulances"] += 1

        for incident in setup["incidents"]:
            if incident["kind"] == IncidentKind.CLOSURE:
                simulation.block_segment(incident["segment"])
                applied["closures"] += 1
            else:
                simulation.add_accident(
                    tuple(sorted(incident["segment"])), incident["severity"], incident["remaining"]
                )
                applied["accidents"] += 1

        if setup["incidents"]:
            rerouting.reroute_affected_vehicles(
                context.graph, simulation, context.node_by_point,
                route_finder=traffic.route_finder(),
            )
            traffic.process_events()
            run.recorder.sync()

        for requested, seed in setup["fleets"]:
            generator = random.Random(seed) if seed is not None else None
            outcome = traffic.spawn_vehicles(requested, generator)
            run.recorder.fleet_added(outcome, seed)
            applied["fleet_vehicles"] += outcome["spawned"]

        route = list(simulation.vehicles[0].route)

    add_run(context, run, make_default=make_default)
    logger.info("Simulation %s restarted from %s", run.id, simulation_id)

    return {
        "status": "simulation restarted",
        **details,
        "restarted_from": str(run_uuid),
        "applied": applied,
        "route": route,
    }


# ------------------------------------------------------------
# Driving a run
# ------------------------------------------------------------

@run_route(router, "get", "/simulation/state", "/state")
def simulation_state(run: LiveRun = Depends(default_run)):
    """Where the run's first vehicle (the one on the planned route) is, how far
    it has gone and whether it has arrived."""

    with run.lock:
        vehicle = run.simulation.vehicles[0]

        return {
            "vehicle_position": vehicle.get_current_location(),
            "distance_travelled": vehicle.distance_travelled,
            "finished": vehicle.finished
        }


@run_route(router, "post", "/simulation/step", "/step")
def simulation_step(
    seconds: int = Query(1, ge=1, le=60, description="simulated seconds to advance (the website's speed control)"),
    run: LiveRun = Depends(default_run),
):
    """Advance the run by `seconds` simulated seconds. Answers every
    vehicle's position and state, and a summary of the traffic for live
    charts (computed from the same pass over the vehicles)."""

    with run.lock:
        simulation = run.simulation

        rerouted = 0
        for _ in range(seconds):
            # Run surges, move all vehicles, then react to changes and re-plan
            rerouted += run.traffic.step()

            # Save what changed (usually nothing: then no database work)
            run.recorder.sync()

        vehicles = []
        active = held_up = arrived = 0
        speed_sum = 0.0
        journey_sum = 0
        reroutes = 0

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

            if vehicle.vehicle_type != "normal":
                continue
            reroutes += vehicle.reroutes
            if vehicle.finished:
                if vehicle.journey_seconds:
                    arrived += 1
                    journey_sum += vehicle.journey_seconds
            else:
                active += 1
                speed_sum += vehicle.speed_factor
                if vehicle.speed_factor < 1:
                    held_up += 1

        simulation_time = simulation.time
        summary = {
            "active_vehicles": active,
            "held_up": held_up,
            "mean_speed_factor": speed_sum / active if active else None,
            "arrived": arrived,
            "mean_journey_seconds": journey_sum / arrived if arrived else None,
            "reroutes": reroutes,
            "closed_roads": len(simulation.get_blocked_segments()),
            "accidents": len(simulation.accidents),
        }

    # Already plain numbers, strings and booleans, so skip FastAPI's
    # general-purpose encoder: with 2,000 vehicles it took about 55 ms of
    # every step just to walk this list. The JSON is the same.
    return JSONResponse({
        "vehicles": vehicles,
        "simulation_time": simulation_time,
        "rerouted_this_step": rerouted,
        "summary": summary,
    })


@run_route(router, "get", "/simulation/congestion", "/congestion")
def simulation_congestion(run: LiveRun = Depends(default_run)):
    """Every road with vehicles on it: how many, and its congestion level
    (light, moderate or heavy), including capacity lost to accidents. The
    website draws this as the live congestion layer."""

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
    """Plan a new route for one vehicle from where it is now, around closed
    roads and with current congestion."""

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
            traffic=simulation.get_segment_traffic(),
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
        run.recorder.fleet_added(outcome, seed)

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
