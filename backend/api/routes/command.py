"""The emergency response command centre (simulation/command.py): a run
with a standing fleet, incidents logged on the map, the units sent to
them, their timelines, response figures and recorded tracks for replay."""

import logging
import random

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.context import AppContext
from backend.api.routes.common import default_run, get_context, run_route
from backend.api.routes.emergencies import record_outcome
from backend.api.sessions import LiveRun
from backend.simulation import dispatch
from backend.simulation.command import KINDS, CommandCentre


logger = logging.getLogger(__name__)

router = APIRouter(tags=["Command centre"])

# The run a command centre starts: its first vehicle drives from Norwich
# station to City Hall (every run has one planned trip)
SHIFT_START = (52.6274, 1.3069)
SHIFT_END = (52.6286, 1.2925)

# Ambulance standby points: OpenStreetMap has no road ambulance station in
# Norwich, so ambulances wait at the optimiser's best sites
AMBULANCE_POINTS = 3


def _command(run):
    if run.command is None:
        raise HTTPException(
            status_code=409,
            detail="This run has no command centre: mobilise a fleet first (POST /command/fleet)",
        )
    return run.command


def mobilise(context, run, vehicles_per_station):
    """Stations and units for the command centre: OpenStreetMap's fire and
    police stations, and the optimiser's ambulance standby points, each
    with `vehicles_per_station` units. Recorded like any station."""

    from backend.optimiser import station_optimiser as optimiser
    from backend.optimiser.existing_stations import load
    from backend.optimiser.planning import MAX_SNAP_METRES, plan
    from backend.simulation.vehicle import straight_line_metres

    graph = context.graph
    command = run.command = CommandCentre(run.simulation, context.network)
    places = []
    for station in load():
        if station["service"] not in ("fire", "police"):
            continue
        node = optimiser.snap_to_road(graph, station["latitude"], station["longitude"])
        data = graph.nodes[node]
        snapped = (data["latitude"], data["longitude"])
        if straight_line_metres((station["latitude"], station["longitude"]), snapped) > MAX_SNAP_METRES:
            continue
        places.append((*snapped, station["service"], station["name"], "OpenStreetMap"))
    best = plan(graph, context.network, "ambulance", AMBULANCE_POINTS)["optimised"]
    for site in best:
        places.append((
            site["latitude"], site["longitude"], "ambulance",
            f"Ambulance standby point {site['order']}", "optimiser",
        ))

    for latitude, longitude, service, name, source in places:
        index, vehicles = command.add_station(latitude, longitude, service, name, source, vehicles_per_station)
        run.recorder.station_added(index, run.simulation.stations[index])
        for vehicle_index in vehicles:
            run.recorder.vehicle_added(index, vehicle_index, run.simulation.vehicles[vehicle_index])
    return command


@router.post("/command/start", status_code=201)
def start_command_centre(
    vehicles_per_station: int = Query(2, ge=1, le=4, description="units at each station"),
    background_vehicles: int = Query(800, ge=0, le=2000, description="ordinary traffic on the roads"),
    seed: int = 1,
    context: AppContext = Depends(get_context),
):
    """Start a run for the command centre (the osm traffic model, so units
    queue when none is free and drive back to their stations): a fleet at
    OpenStreetMap's fire and police stations and the optimiser's ambulance
    standby points, with ordinary traffic. Does not replace the default
    run. Answers the run's id and the command centre's state."""

    from backend.api.routes.simulation import add_run, build_run

    graph = context.graph
    start = dispatch.nearest_junction(graph, *SHIFT_START)
    end = dispatch.nearest_junction(graph, *SHIFT_END)
    run, details = build_run(context, start, end, traffic_model="osm")
    add_run(context, run, make_default=False)

    with run.lock:
        command = mobilise(context, run, vehicles_per_station)
        if background_vehicles:
            outcome = run.traffic.spawn_vehicles(background_vehicles, random.Random(seed))
            run.recorder.fleet_added(outcome, seed)
        state = command.state()

    logger.info("Command centre %s started: %d units", run.id, len(state["units"]))
    return {"simulation_id": run.id, "traffic_model": details["traffic_model"], **state}


@run_route(router, "post", "/command/fleet", "/command/fleet", status_code=201)
def mobilise_fleet(
    vehicles_per_station: int = Query(2, ge=1, le=4),
    run: LiveRun = Depends(default_run),
    context: AppContext = Depends(get_context),
):
    """Give a run that has none a command centre and its fleet."""

    with run.lock:
        if run.command is not None:
            raise HTTPException(status_code=409, detail="This run already has a command centre")
        return mobilise(context, run, vehicles_per_station).state()


@run_route(router, "get", "/command", "/command")
def command_state(run: LiveRun = Depends(default_run)):
    """Every incident with its units and timeline, every unit (where it is,
    what it is doing, its route while on a call), the stations and the
    response figures."""

    with run.lock:
        return _command(run).state()


@run_route(router, "post", "/command/incidents", "/command/incidents", status_code=201)
def report_incident(
    kind: str,
    latitude: float,
    longitude: float,
    run: LiveRun = Depends(default_run),
    context: AppContext = Depends(get_context),
):
    """Log an incident: medical, fire, crime, road_accident, flood or
    road_closure. Its response plan's units are sent at once (the free unit
    of each service that would arrive first by road; queued if none is
    free), and accidents, floods and closures take effect on the roads."""

    if kind not in KINDS:
        raise HTTPException(status_code=422, detail=f"kind must be one of {', '.join(KINDS)}")

    with run.lock:
        command = _command(run)
        try:
            incident, outcomes = command.report(kind, latitude, longitude, context.graph)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
        for outcome in outcomes:
            record_outcome(run, outcome)
        # Vehicles whose route the accident, flood or closure affects re-plan
        if incident.segment is not None or incident.flood is not None:
            from backend.simulation import rerouting

            rerouting.reroute_affected_vehicles(
                context.graph, run.simulation, context.node_by_point,
                route_finder=run.traffic.route_finder(),
            )
        run.traffic.process_events()
        run.recorder.sync()
        command.update()
        return {
            "incident": command.describe_incident(incident),
            "dispatched": [o["status"] for o in outcomes],
            "metrics": command.metrics(),
        }


@run_route(router, "post", "/command/incidents/{incident_id}/resolve", "/command/incidents/{incident_id}/resolve")
def resolve_incident(
    incident_id: int,
    run: LiveRun = Depends(default_run),
    context: AppContext = Depends(get_context),
):
    """Close an incident from the command centre: units at the scene leave
    for their stations, and its road reopens or its accident or flood ends.
    Refused (409) while a unit is still on its way."""

    with run.lock:
        command = _command(run)
        incident = command.find(incident_id)
        if incident is None:
            raise HTTPException(status_code=404, detail="No such incident")
        try:
            command.resolve(incident)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        run.traffic.process_events()
        run.recorder.sync(manual_clear=True)
        return {"incident": command.describe_incident(incident), "metrics": command.metrics()}


@run_route(router, "get", "/command/replay", "/command/replay")
def command_replay(run: LiveRun = Depends(default_run)):
    """What the command centre recorded: every unit's position each
    simulated second while it was out, and every incident's timeline."""

    with run.lock:
        return _command(run).replay()
