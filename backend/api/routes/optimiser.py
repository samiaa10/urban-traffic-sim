"""Station optimiser: rank single stations, or choose a set that works
well together. With a database every answer is saved and can be listed
and fetched again from /optimiser/runs."""

import logging
import math
import time
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.context import AppContext
from backend.api.routes.common import closures_for, get_context
from backend.optimiser.station_optimiser import (
    explain_best,
    explain_station_set,
    optimise_station_locations,
    optimise_station_set,
)


logger = logging.getLogger(__name__)

router = APIRouter()

KINDS = ("ranking", "station_set")


def optimiser_points(context, candidates):
    """Candidate locations for the optimiser, from the ?candidates= text.

    candidates looks like "52.63,1.29;52.62,1.31" (latitude,longitude
    pairs separated by semicolons). None means "use the default spread".
    """

    if candidates is None:
        return context.default_station_candidates

    try:
        points = [
            tuple(float(value) for value in pair.split(","))
            for pair in candidates.split(";")
            if pair.strip()
        ]

        if any(len(point) != 2 for point in points):
            raise ValueError

        if not all(math.isfinite(value) for point in points for value in point):
            raise ValueError

    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="candidates must look like 52.63,1.29;52.62,1.31"
        )

    if len(points) < 2 or len(points) > 20:
        raise HTTPException(
            status_code=400,
            detail="Give between 2 and 20 candidate locations"
        )

    return points


def check_optimiser_settings(scenarios, target_seconds, speed):
    """Turn bad optimiser settings into a 400 error."""

    if scenarios < 1 or scenarios > 5000:
        raise HTTPException(
            status_code=400,
            detail="scenarios must be between 1 and 5000"
        )

    if not math.isfinite(speed) or speed <= 0:
        raise HTTPException(
            status_code=400,
            detail="speed must be above 0"
        )

    if not math.isfinite(target_seconds) or target_seconds <= 0:
        raise HTTPException(
            status_code=400,
            detail="target_seconds must be above 0"
        )


def check_station_count(stations):
    if stations < 1 or stations > 10:
        raise HTTPException(
            status_code=400,
            detail="stations must be between 1 and 10"
        )


def service_settings(service, target_seconds, speed, distribution):
    """Fill in the speed and target from the service (simulation/
    services.py) when not given; without a service the original defaults
    (20 m/s, 480 s) stay."""

    from backend.simulation.services import service as find_service

    if distribution not in ("uniform", "population"):
        raise HTTPException(status_code=400, detail="distribution must be uniform or population")

    if service is None:
        return (480.0 if target_seconds is None else target_seconds,
                20.0 if speed is None else speed)

    try:
        chosen = find_service(service)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None

    return (chosen.target_seconds if target_seconds is None else target_seconds,
            chosen.speed if speed is None else speed)


def extra_settings(service, distribution):
    """The service and distribution, in answers that used them (answers
    without them look exactly as they always did)."""

    extra = {}
    if service is not None:
        extra["service"] = service
    if distribution != "uniform":
        extra["distribution"] = distribution
    return extra


def optimise(
    context, kind, candidates, scenarios, seed, target_seconds, speed,
    simulation_id, reuse, stations=None, service=None, distribution="uniform",
):
    """Answer an optimiser request: reuse a saved answer, or compute one
    and save it. Adds optimiser_run_id (None without a database, or if
    saving failed) and reused to the answer."""

    target_seconds, speed = service_settings(service, target_seconds, speed, distribution)

    if kind == "station_set":
        check_station_count(stations)

    points = optimiser_points(context, candidates)

    check_optimiser_settings(scenarios, target_seconds, speed)

    blocked_segments, run = closures_for(context, simulation_id)

    database = context.database
    request_key = None

    if database is not None:
        from backend.db.repository import optimiser_request_key

        request_key = optimiser_request_key(
            kind, context.graph_sha256, points, blocked_segments,
            scenarios, seed, target_seconds, speed, stations,
            distribution=distribution,
        )

        if reuse:
            saved = saved_answer(database, request_key)
            if saved is not None:
                # Saved rows do not keep these; the key covers what they
                # change (speed, target, distribution)
                return {**saved, **extra_settings(service, distribution)}

    began = time.perf_counter()

    if kind == "ranking":
        results = optimise_station_locations(
            context.graph,
            points,
            scenarios=scenarios,
            seed=seed,
            target_seconds=target_seconds,
            speed=speed,
            blocked_segments=blocked_segments,
            distribution=distribution,
        )
        answer = {
            "scenarios": scenarios,
            "seed": seed,
            "target_seconds": target_seconds,
            "speed": speed,
            **extra_settings(service, distribution),
            "closed_roads": len(blocked_segments),
            "explanation": explain_best(results, scenarios, target_seconds),
            "results": results
        }

    else:
        try:
            outcome = optimise_station_set(
                context.graph,
                points,
                stations,
                scenarios=scenarios,
                seed=seed,
                target_seconds=target_seconds,
                speed=speed,
                blocked_segments=blocked_segments,
                distribution=distribution,
            )

        except ValueError as error:
            # e.g. more stations asked for than there are candidate junctions
            raise HTTPException(
                status_code=400,
                detail=str(error)
            )

        answer = {
            "scenarios": scenarios,
            "seed": seed,
            "target_seconds": target_seconds,
            "speed": speed,
            **extra_settings(service, distribution),
            "closed_roads": len(blocked_segments),
            "explanation": explain_station_set(
                outcome, scenarios, target_seconds
            ),
            **outcome
        }

    compute_ms = (time.perf_counter() - began) * 1000

    optimiser_run_id = None

    if database is not None:
        optimiser_run_id = save_answer(
            database, kind, answer,
            simulation_run_id=run.id_uuid if run is not None else None,
            graph_sha256=context.graph_sha256,
            request_key=request_key,
            candidate_source="default" if candidates is None else "custom",
            candidates_considered=len(points),
            compute_ms=compute_ms,
        )

    return {**answer, "optimiser_run_id": optimiser_run_id, "reused": False}


def saved_answer(database, request_key):
    """The newest saved answer to an identical request, or None."""

    from backend.db.repository import OptimiserRepository
    from backend.db.serializers import optimiser_response

    with database.transaction() as session:
        stored = OptimiserRepository(session).latest_for_key(request_key)

        if stored is None:
            return None

        return {
            **optimiser_response(stored),
            "optimiser_run_id": str(stored.id),
            "reused": True,
        }


def save_answer(database, kind, answer, **settings):
    """Save an answer; its id, or None if the database failed (the answer
    is still returned: it is correct, just not saved)."""

    from backend.db.repository import OptimiserRepository

    optimiser_run_id = uuid.uuid4()

    try:
        with database.transaction() as session:
            repo = OptimiserRepository(session)
            save = repo.save_ranking if kind == "ranking" else repo.save_station_set
            save(answer, optimiser_run_id=optimiser_run_id, **settings)

    except Exception:
        logger.exception("Could not save an optimiser result")
        return None

    return str(optimiser_run_id)


def require_database(context):
    if context.database is None:
        raise HTTPException(
            status_code=503,
            detail="Saved optimiser results need a database (DATABASE_URL is not set)"
        )


@router.get("/optimiser/stations")
def optimise_stations(
    candidates: Optional[str] = None,
    scenarios: int = 200,
    seed: int = 1,
    target_seconds: Optional[float] = None,
    speed: Optional[float] = None,
    simulation_id: Optional[str] = None,
    reuse: bool = False,
    service: Optional[str] = None,
    distribution: str = "uniform",
    context: AppContext = Depends(get_context)
):
    """Rank candidate station locations by emergency response time.

    candidates looks like "52.63,1.29;52.62,1.31" (latitude,longitude
    pairs separated by semicolons). Leave it out to use a spread of
    about 40 locations over the whole map. Roads closed in the run named
    by simulation_id (or the default run if it is left out) are avoided.
    reuse=true answers from a saved identical request when there is one.
    service (ambulance, fire or police) sets the speed and target to that
    service's (unless given); without it they default to 20 m/s and 480 s,
    as always. distribution=population places emergencies where people
    live (Census 2021) instead of evenly over the junctions.
    """

    return optimise(
        context, "ranking", candidates, scenarios, seed, target_seconds,
        speed, simulation_id, reuse, service=service, distribution=distribution,
    )


@router.get("/optimiser/station-set")
def optimise_station_group(
    stations: int = 3,
    candidates: Optional[str] = None,
    scenarios: int = 200,
    seed: int = 1,
    target_seconds: Optional[float] = None,
    speed: Optional[float] = None,
    simulation_id: Optional[str] = None,
    reuse: bool = False,
    service: Optional[str] = None,
    distribution: str = "uniform",
    context: AppContext = Depends(get_context)
):
    """Choose several stations that work well together.

    stations is how many to pick (1 to 10). The other settings are the
    same as /optimiser/stations. The answer includes how the chosen set
    performs and how it compares with baselines (the best single
    station, the best stations ranked one by one, and random stations).
    """

    return optimise(
        context, "station_set", candidates, scenarios, seed, target_seconds,
        speed, simulation_id, reuse, stations=stations,
        service=service, distribution=distribution,
    )


# ------------------------------------------------------------
# Saved results
# ------------------------------------------------------------

@router.post("/optimiser/runs", status_code=201)
def create_optimiser_run(
    kind: str = "ranking",
    stations: int = 3,
    candidates: Optional[str] = None,
    scenarios: int = 200,
    seed: int = 1,
    target_seconds: Optional[float] = None,
    speed: Optional[float] = None,
    simulation_id: Optional[str] = None,
    reuse: bool = False,
    service: Optional[str] = None,
    distribution: str = "uniform",
    context: AppContext = Depends(get_context)
):
    """Run the optimiser (kind: ranking or station_set) and save it.

    The same as the GET endpoints, for clients that prefer POST for
    something that creates a saved result. stations is used for sets.
    """

    require_database(context)

    if kind not in KINDS:
        raise HTTPException(
            status_code=400, detail="kind must be ranking or station_set"
        )

    return optimise(
        context, kind, candidates, scenarios, seed, target_seconds, speed,
        simulation_id, reuse,
        stations=stations if kind == "station_set" else None,
        service=service, distribution=distribution,
    )


def parse_optimiser_id(optimiser_run_id):
    try:
        return uuid.UUID(optimiser_run_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Optimiser run not found")


@router.get("/optimiser/runs")
def list_optimiser_runs(
    kind: Optional[str] = None,
    simulation_id: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    context: AppContext = Depends(get_context)
):
    """Saved optimiser results, newest first (settings and timing, not the
    full answers: fetch one with /optimiser/runs/{id})."""

    require_database(context)

    if kind is not None and kind not in KINDS:
        raise HTTPException(
            status_code=400, detail="kind must be ranking or station_set"
        )

    from backend.db.repository import OptimiserRepository
    from backend.db.serializers import optimiser_summary

    simulation_uuid = None
    if simulation_id is not None:
        try:
            simulation_uuid = uuid.UUID(simulation_id)
        except ValueError:
            raise HTTPException(status_code=404, detail="Simulation not found")

    with context.database.transaction() as session:
        rows, total = OptimiserRepository(session).list(
            kind=kind, simulation_run_id=simulation_uuid,
            limit=limit, offset=offset,
        )
        summaries = [optimiser_summary(row) for row in rows]

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "optimiser_runs": summaries,
    }


@router.get("/optimiser/runs/{optimiser_run_id}")
def get_optimiser_run(
    optimiser_run_id: str,
    context: AppContext = Depends(get_context)
):
    """One saved result: its settings, and the answer exactly as the
    optimiser endpoint gave it."""

    require_database(context)

    from backend.db.repository import OptimiserRepository
    from backend.db.serializers import optimiser_response, optimiser_summary

    with context.database.transaction() as session:
        stored = OptimiserRepository(session).get(parse_optimiser_id(optimiser_run_id))

        if stored is None:
            raise HTTPException(status_code=404, detail="Optimiser run not found")

        return {
            **optimiser_summary(stored),
            "answer": optimiser_response(stored),
        }


@router.delete("/optimiser/runs/{optimiser_run_id}")
def delete_optimiser_run(
    optimiser_run_id: str,
    context: AppContext = Depends(get_context)
):
    require_database(context)

    from backend.db.repository import OptimiserRepository

    with context.database.transaction() as session:
        deleted = OptimiserRepository(session).delete(
            parse_optimiser_id(optimiser_run_id)
        )

    if not deleted:
        raise HTTPException(status_code=404, detail="Optimiser run not found")

    return {"status": "optimiser run deleted", "optimiser_run_id": optimiser_run_id}
