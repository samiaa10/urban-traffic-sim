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


# ============================================================
# Simulation runs and their history
# ============================================================

def _time(value):
    return value.isoformat() if value is not None else None


def run_summary(run):
    """A saved run, in the same shape as a live run's summary."""

    return {
        "simulation_id": str(run.id),
        "status": run.status.value,
        "live": False,
        "is_default": False,
        "created_at": _time(run.created_at),
        "ended_at": _time(run.ended_at),
        "start": run.start_node,
        "destination": run.destination_node,
        "simulation_time": run.sim_time,
        "vehicles": run.vehicles_total,
        "vehicles_finished": run.vehicles_finished,
        "rerouting_enabled": run.rerouting_enabled,
        "graph_sha256": run.graph_sha256,
    }


def _point(latitude, longitude):
    return {"latitude": latitude, "longitude": longitude}


def incident_json(incident):
    body = {
        "incident_id": incident.id,
        "kind": incident.kind.value,
        "start": _point(incident.start_latitude, incident.start_longitude),
        "end": _point(incident.end_latitude, incident.end_longitude),
        "start_node": incident.start_node,
        "end_node": incident.end_node,
        "started_at": incident.started_sim_time,
        "ended_at": incident.ended_sim_time,
        "end_reason": incident.end_reason.value if incident.end_reason else None,
        "emergency_id": incident.emergency_id,
    }

    if incident.kind.value == "accident":
        body.update({
            "severity": incident.severity,
            "speed_factor": incident.speed_factor,
            "capacity_factor": incident.capacity_factor,
            "planned_end_at": incident.planned_end_sim_time,
        })

    return body


def emergency_json(emergency):
    vehicle = emergency.vehicle

    return {
        "emergency_id": emergency.id,
        "emergency_type": emergency.emergency_type,
        "location": _point(emergency.latitude, emergency.longitude),
        "status": emergency.status.value,
        "cancel_reason": emergency.cancel_reason,
        "vehicle_id": vehicle.vehicle_index if vehicle is not None else None,
        "station_id": vehicle.station.station_index if vehicle is not None else None,
        "created_at": emergency.created_sim_time,
        "arrived_at": emergency.arrived_sim_time,
        "response_time_seconds": emergency.response_seconds,
        "route_distance_metres": emergency.route_distance_m,
        "nodes_explored": emergency.nodes_explored,
        "reroutes": emergency.reroutes,
    }


def station_json(station):
    return {
        "station_id": station.station_index,
        "station_type": station.station_type,
        "location": _point(station.latitude, station.longitude),
        "created_at": station.created_sim_time,
        "vehicles": [
            {
                "vehicle_id": vehicle.vehicle_index,
                "vehicle_type": vehicle.vehicle_type,
                "speed": vehicle.speed,
                "created_at": vehicle.created_sim_time,
            }
            for vehicle in station.vehicles
        ],
    }


EVENT_LINKS = (
    "incident_id", "emergency_id", "station_id", "vehicle_id",
    "surge_id", "fleet_spawn_id",
)


def event_json(event):
    body = {
        "seq": event.seq,
        "simulation_time": event.sim_time,
        "type": event.event_type.value,
        "recorded_at": _time(event.created_at),
    }

    for link in EVENT_LINKS:
        value = getattr(event, link)
        if value is not None:
            body[link] = value

    return body


def metric_json(metric):
    return {
        "simulation_time": metric.sim_time,
        "vehicles": metric.vehicles_total,
        "vehicles_finished": metric.vehicles_finished,
        "mean_speed_factor": metric.mean_speed_factor,
        "congested_roads": metric.congested_roads,
        "closed_roads": metric.closed_roads,
        "accidents": metric.active_accidents,
        "reroutes": metric.reroutes_total,
    }


def surge_json(surge):
    return {
        "surge_id": surge.id,
        "zone": {
            "latitude": surge.latitude,
            "longitude": surge.longitude,
            "radius_metres": surge.radius_metres,
        },
        "direction": surge.direction.value,
        "vehicles_per_minute": surge.vehicles_per_minute,
        "starts_at": surge.starts_sim_time,
        "ends_at": surge.ends_sim_time,
        "spawned": surge.spawned,
        "failed": surge.failed,
    }


def fleet_json(fleet):
    return {
        "fleet_spawn_id": fleet.id,
        "simulation_time": fleet.sim_time,
        "requested": fleet.requested,
        "spawned": fleet.spawned,
        "failed": fleet.failed,
        "seed": fleet.seed,
        "seconds": fleet.wall_seconds,
    }
