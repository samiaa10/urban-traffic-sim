"""Turning stored rows back into the JSON the API answers with."""

from backend.db.models import OptimiserKind


def optimiser_response(run):
    """The answer the optimiser endpoint gave, rebuilt from the rows.

    For a ranking this is exactly the /optimiser/stations body; for a set,
    exactly the /optimiser/station-set body (tests check both).
    """

    body = {
        "scenarios": run.scenarios,
        "seed": run.seed,
        "target_seconds": run.target_seconds,
        "speed": run.speed,
        "closed_roads": run.closed_roads,
        "explanation": list(run.explanation),
    }

    if run.kind == OptimiserKind.RANKING:
        body["results"] = [
            {
                "node": c.node,
                "latitude": c.latitude,
                "longitude": c.longitude,
                "average_seconds": c.average_seconds,
                "worst_seconds": c.worst_seconds,
                "within_target_percent": c.within_target_percent,
                "unreachable": c.unreachable,
                "ranking_seconds": c.ranking_seconds,
                "rank": c.rank,
            }
            for c in run.candidates
        ]
        return body

    body.update({
        "count": run.station_count,
        "candidates_considered": run.candidates_considered,
        "stations": [
            {
                "order": s.pick_order,
                "node": s.node,
                "latitude": s.latitude,
                "longitude": s.longitude,
                "average_seconds_after": s.average_seconds_after,
                "within_target_percent_after": s.within_target_percent_after,
            }
            for s in run.selected_stations
        ],
        "result": {
            "average_seconds": run.set_average_seconds,
            "worst_seconds": run.set_worst_seconds,
            "within_target_percent": run.set_within_target_percent,
            "unreachable": run.set_unreachable,
            "ranking_seconds": run.set_ranking_seconds,
        },
        "baselines": [
            {
                "name": b.name,
                "label": b.label,
                "stations": b.stations,
                "average_seconds": b.average_seconds,
                "worst_seconds": b.worst_seconds,
                "within_target_percent": b.within_target_percent,
                # A count for real station sets; the random baseline's is a
                # mean over trials, so it can be fractional
                "unreachable": (
                    b.unreachable if b.name == "random" else int(b.unreachable)
                ),
                "ranking_seconds": b.ranking_seconds,
            }
            for b in run.baselines
        ],
    })

    return body


def optimiser_summary(run):
    """One line of GET /optimiser/runs."""

    return {
        "optimiser_run_id": str(run.id),
        "kind": run.kind.value,
        "simulation_id": str(run.simulation_run_id) if run.simulation_run_id else None,
        "created_at": run.created_at.isoformat(),
        "scenarios": run.scenarios,
        "seed": run.seed,
        "target_seconds": run.target_seconds,
        "speed": run.speed,
        "stations": run.station_count,
        "candidate_source": run.candidate_source.value,
        "candidates_considered": run.candidates_considered,
        "closed_roads": run.closed_roads,
        "compute_ms": run.compute_ms,
    }
