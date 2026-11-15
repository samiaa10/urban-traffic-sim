"""What happened in a run, read from the database.

These work for live runs and for runs that have ended (stopped, replaced,
or interrupted by a server restart): the history is in PostgreSQL, not in
the server's memory. Without a database they answer 503.
"""

from contextlib import contextmanager
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from backend.api.context import AppContext
from backend.api.routes.common import get_context, parse_run_id


router = APIRouter()

PREFIX = "/simulations/{simulation_id}/history"


@contextmanager
def History(context, simulation_id):
    """`with History(context, id) as (repo, run):` - one read transaction.

    503 without a database, 404 if no such run was ever saved.
    """

    if context.database is None:
        raise HTTPException(
            status_code=503,
            detail="History needs a database (DATABASE_URL is not set)"
        )

    run_uuid = parse_run_id(simulation_id)

    from backend.db.repository import RunRepository

    with context.database.transaction() as session:
        repo = RunRepository(session)
        run = repo.get_run(run_uuid)

        if run is None:
            raise HTTPException(status_code=404, detail="Simulation not found")

        yield repo, run


@router.get(PREFIX)
def run_history(
    simulation_id: str,
    context: AppContext = Depends(get_context)
):
    """The run, with how much of each kind of history it has."""

    from backend.db.serializers import run_summary

    with History(context, simulation_id) as (repo, run):
        incidents = repo.incidents(run.id)
        emergencies = repo.emergencies(run.id)

        summary = run_summary(run)
        live = context.registry.get(str(run.id))
        if live is not None:
            summary["live"] = True

        summary["counts"] = {
            "closures": sum(1 for i in incidents if i.kind.value == "closure"),
            "accidents": sum(1 for i in incidents if i.kind.value == "accident"),
            "emergencies": len(emergencies),
            "emergencies_arrived": sum(
                1 for e in emergencies if e.status.value == "arrived"
            ),
            "stations": len(run.stations),
            "surges": len(run.surges),
            "fleets": len(run.fleet_spawns),
            "events": repo.next_seq(run.id) - 1,
            "metrics": len(run.metrics),
        }

        arrived = [e.response_seconds for e in emergencies if e.response_seconds is not None]
        summary["mean_response_seconds"] = sum(arrived) / len(arrived) if arrived else None

        return summary


@router.get(PREFIX + "/incidents")
def incident_history(
    simulation_id: str,
    kind: Optional[str] = None,
    context: AppContext = Depends(get_context)
):
    """Every closure and accident, open or ended, oldest first."""

    from backend.db.serializers import incident_json

    if kind not in (None, "closure", "accident"):
        raise HTTPException(status_code=400, detail="kind must be closure or accident")

    with History(context, simulation_id) as (repo, run):
        incidents = [
            incident_json(i) for i in repo.incidents(run.id)
            if kind is None or i.kind.value == kind
        ]

    return {"simulation_id": simulation_id, "incidents": incidents}


@router.get(PREFIX + "/emergencies")
def emergency_history(
    simulation_id: str,
    context: AppContext = Depends(get_context)
):
    """Every emergency: dispatched, arrived, refused or cut short."""

    from backend.db.models import Emergency, EmergencyVehicle
    from backend.db.serializers import emergency_json

    with History(context, simulation_id) as (repo, run):
        rows = repo.session.scalars(
            select(Emergency)
            .where(Emergency.run_id == run.id)
            .options(
                selectinload(Emergency.vehicle).selectinload(EmergencyVehicle.station)
            )
            .order_by(Emergency.id)
        ).all()

        emergencies = [emergency_json(e) for e in rows]

    return {"simulation_id": simulation_id, "emergencies": emergencies}


@router.get(PREFIX + "/stations")
def station_history(
    simulation_id: str,
    context: AppContext = Depends(get_context)
):
    from backend.db.serializers import station_json

    with History(context, simulation_id) as (repo, run):
        stations = [station_json(s) for s in run.stations]

    return {"simulation_id": simulation_id, "stations": stations}


@router.get(PREFIX + "/demand")
def demand_history(
    simulation_id: str,
    context: AppContext = Depends(get_context)
):
    """Fleets added and surges started."""

    from backend.db.serializers import fleet_json, surge_json

    with History(context, simulation_id) as (repo, run):
        body = {
            "simulation_id": simulation_id,
            "fleets": [fleet_json(f) for f in repo.fleet_spawns(run.id)],
            "surges": [surge_json(s) for s in repo.surges(run.id)],
        }

    return body


@router.get(PREFIX + "/events")
def event_history(
    simulation_id: str,
    after_seq: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=1000),
    context: AppContext = Depends(get_context)
):
    """The timeline in order. Page through it with after_seq (the last
    seq you have) - this also lets a client follow a live run."""

    from backend.db.serializers import event_json

    with History(context, simulation_id) as (repo, run):
        events = [event_json(e) for e in repo.events(run.id, after_seq, limit)]

    return {"simulation_id": simulation_id, "events": events}


@router.get(PREFIX + "/metrics")
def metric_history(
    simulation_id: str,
    from_time: Optional[float] = None,
    to_time: Optional[float] = None,
    context: AppContext = Depends(get_context)
):
    """Traffic snapshots over simulated time."""

    from backend.db.serializers import metric_json

    with History(context, simulation_id) as (repo, run):
        metrics = [metric_json(m) for m in repo.metrics(run.id, from_time, to_time)]

    return {"simulation_id": simulation_id, "metrics": metrics}
