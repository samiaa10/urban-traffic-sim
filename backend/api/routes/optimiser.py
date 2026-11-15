"""Station optimiser: rank single stations, or choose a set that works
well together."""

import math
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from backend.api.context import AppContext
from backend.api.routes.common import closures_for, get_context
from backend.optimiser.station_optimiser import (
    explain_best,
    explain_station_set,
    optimise_station_locations,
    optimise_station_set,
)


router = APIRouter()


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


@router.get("/optimiser/stations")
def optimise_stations(
    candidates: Optional[str] = None,
    scenarios: int = 200,
    seed: int = 1,
    target_seconds: float = 480,
    speed: float = 20,
    simulation_id: Optional[str] = None,
    context: AppContext = Depends(get_context)
):
    """Rank candidate station locations by emergency response time.

    candidates looks like "52.63,1.29;52.62,1.31" (latitude,longitude
    pairs separated by semicolons). Leave it out to use a spread of
    about 40 locations over the whole map. Roads closed in the run named
    by simulation_id (or the default run if it is left out) are avoided.
    """

    points = optimiser_points(context, candidates)

    check_optimiser_settings(scenarios, target_seconds, speed)

    blocked_segments, _ = closures_for(context, simulation_id)

    results = optimise_station_locations(
        context.graph,
        points,
        scenarios=scenarios,
        seed=seed,
        target_seconds=target_seconds,
        speed=speed,
        blocked_segments=blocked_segments
    )

    return {
        "scenarios": scenarios,
        "seed": seed,
        "target_seconds": target_seconds,
        "speed": speed,
        "closed_roads": len(blocked_segments),
        "explanation": explain_best(results, scenarios, target_seconds),
        "results": results
    }


@router.get("/optimiser/station-set")
def optimise_station_group(
    stations: int = 3,
    candidates: Optional[str] = None,
    scenarios: int = 200,
    seed: int = 1,
    target_seconds: float = 480,
    speed: float = 20,
    simulation_id: Optional[str] = None,
    context: AppContext = Depends(get_context)
):
    """Choose several stations that work well together.

    stations is how many to pick (1 to 10). The other settings are the
    same as /optimiser/stations. The answer includes how the chosen set
    performs and how it compares with baselines (the best single
    station, the best stations ranked one by one, and random stations).
    """

    if stations < 1 or stations > 10:
        raise HTTPException(
            status_code=400,
            detail="stations must be between 1 and 10"
        )

    points = optimiser_points(context, candidates)

    check_optimiser_settings(scenarios, target_seconds, speed)

    blocked_segments, _ = closures_for(context, simulation_id)

    try:
        outcome = optimise_station_set(
            context.graph,
            points,
            stations,
            scenarios=scenarios,
            seed=seed,
            target_seconds=target_seconds,
            speed=speed,
            blocked_segments=blocked_segments
        )

    except ValueError as error:
        # e.g. more stations asked for than there are candidate junctions
        raise HTTPException(
            status_code=400,
            detail=str(error)
        )

    return {
        "scenarios": scenarios,
        "seed": seed,
        "target_seconds": target_seconds,
        "speed": speed,
        "closed_roads": len(blocked_segments),
        "explanation": explain_station_set(
            outcome, scenarios, target_seconds
        ),
        **outcome
    }
