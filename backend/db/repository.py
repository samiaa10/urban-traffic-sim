"""Reading and writing runs and optimiser results.

Nothing outside backend/db touches the models or SQL directly; it goes
through these two classes. They never commit: the caller owns the
transaction (one per API request or simulation step), so a request's
changes are saved together or not at all.

Every write that belongs on a run's timeline also adds its run_events row
in the same transaction, so the timeline can never disagree with the
tables it points at.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Sequence, Tuple

from sqlalchemy import delete, func, select, update

from backend.optimiser.station_optimiser import OPTIMISER_VERSION

from backend.db.models import (
    CandidateSource,
    Emergency,
    EmergencyStatus,
    EmergencyVehicle,
    EventType,
    FleetSpawn,
    Incident,
    IncidentEndReason,
    IncidentKind,
    OptimiserBaseline,
    OptimiserCandidate,
    OptimiserKind,
    OptimiserRun,
    OptimiserSelectedStation,
    RunEvent,
    RunMetric,
    RunStatus,
    SimulationRun,
    Station,
    Surge,
    SurgeDirection,
)


@dataclass(frozen=True)
class Road:
    """A road as the simulation names it: two (lat, lon) points in sorted
    order, plus their junction ids when known."""

    start: Tuple[float, float]
    end: Tuple[float, float]
    start_node: Optional[int] = None
    end_node: Optional[int] = None

    @classmethod
    def from_segment(cls, segment, node_by_point=None):
        start, end = sorted(segment)
        lookup = node_by_point or {}
        return cls(start, end, lookup.get(start), lookup.get(end))


def _road_filter(road):
    return (
        Incident.start_latitude == road.start[0],
        Incident.start_longitude == road.start[1],
        Incident.end_latitude == road.end[0],
        Incident.end_longitude == road.end[1],
    )


INCIDENT_START_EVENTS = {
    IncidentKind.CLOSURE: EventType.ROAD_CLOSED,
    IncidentKind.ACCIDENT: EventType.ACCIDENT,
}

INCIDENT_END_EVENTS = {
    IncidentKind.CLOSURE: EventType.ROAD_REOPENED,
    IncidentKind.ACCIDENT: EventType.ACCIDENT_CLEARED,
}


class RunRepository:

    def __init__(self, session):
        self.session = session

    # ------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------

    def create_run(
        self, run_id, start_node, destination_node, graph_sha256,
        rerouting_enabled=True, vehicles_total=0, restarted_from=None,
        traffic_model="simple",
    ):
        run = SimulationRun(
            id=run_id,
            status=RunStatus.ACTIVE,
            start_node=start_node,
            destination_node=destination_node,
            graph_sha256=graph_sha256,
            rerouting_enabled=rerouting_enabled,
            sim_time=0,
            vehicles_total=vehicles_total,
            vehicles_finished=0,
            restarted_from=restarted_from,
            traffic_model=traffic_model,
        )
        self.session.add(run)
        self.session.flush()

        self.add_event(run_id, EventType.RUN_STARTED, 0)

        return run

    def get_run(self, run_id):
        return self.session.get(SimulationRun, run_id)

    def list_runs(self, status=None, limit=50, offset=0):
        """Runs newest first, and how many there are in all."""

        query = select(SimulationRun)
        count = select(func.count()).select_from(SimulationRun)

        if status is not None:
            query = query.where(SimulationRun.status == status)
            count = count.where(SimulationRun.status == status)

        rows = self.session.scalars(
            query.order_by(SimulationRun.created_at.desc(), SimulationRun.id)
            .limit(limit).offset(offset)
        ).all()

        return rows, self.session.scalar(count)

    def record_progress(
        self, run_id, sim_time, vehicles_total, vehicles_finished,
    ):
        self.session.execute(
            update(SimulationRun)
            .where(SimulationRun.id == run_id)
            .values(
                sim_time=sim_time,
                vehicles_total=vehicles_total,
                vehicles_finished=vehicles_finished,
                updated_at=func.now(),
            )
        )

    def set_rerouting(self, run_id, enabled, sim_time):
        self.session.execute(
            update(SimulationRun)
            .where(SimulationRun.id == run_id)
            .values(rerouting_enabled=enabled, updated_at=func.now())
        )
        self.add_event(run_id, EventType.REROUTING_CHANGED, sim_time)

    def end_run(self, run_id, status, sim_time=None):
        """Mark a run as no longer live (stopped, replaced or interrupted).

        Anything still in progress ends with it: open closures and
        accidents end with reason run_ended, and emergencies whose vehicle
        had not arrived get status run_ended. Returns False if the run is
        unknown or had already ended.
        """

        if status == RunStatus.ACTIVE:
            raise ValueError("end_run needs a status other than active")

        run = self.session.get(SimulationRun, run_id, with_for_update=True)

        if run is None or run.status != RunStatus.ACTIVE:
            return False

        if sim_time is None:
            sim_time = run.sim_time

        self._close_everything(run_id, sim_time)

        run.status = status
        run.sim_time = max(run.sim_time, sim_time)
        run.ended_at = datetime.now(timezone.utc)

        self.add_event(run_id, EventType.RUN_ENDED, run.sim_time)

        return True

    def interrupt_active_runs(self):
        """At start-up: runs still marked active were live in a server that
        stopped, so they can never be resumed. Returns their IDs."""

        run_ids = self.session.scalars(
            select(SimulationRun.id)
            .where(SimulationRun.status == RunStatus.ACTIVE)
            .with_for_update()
        ).all()

        for run_id in run_ids:
            self.end_run(run_id, RunStatus.INTERRUPTED)

        return list(run_ids)

    def delete_run(self, run_id):
        """Delete a run and (by cascade) all of its history."""

        result = self.session.execute(
            delete(SimulationRun).where(SimulationRun.id == run_id)
        )
        return result.rowcount > 0

    def _close_everything(self, run_id, sim_time):
        self.session.execute(
            update(Incident)
            .where(Incident.run_id == run_id, Incident.ended_sim_time.is_(None))
            .values(
                ended_sim_time=func.greatest(Incident.started_sim_time, sim_time),
                end_reason=IncidentEndReason.RUN_ENDED,
            )
        )
        self.session.execute(
            update(Emergency)
            .where(
                Emergency.run_id == run_id,
                Emergency.status == EmergencyStatus.DISPATCHED,
            )
            .values(status=EmergencyStatus.RUN_ENDED)
        )

    # ------------------------------------------------------------
    # Timeline
    # ------------------------------------------------------------

    def next_seq(self, run_id):
        return (
            self.session.scalar(
                select(func.max(RunEvent.seq)).where(RunEvent.run_id == run_id)
            )
            or 0
        ) + 1

    def add_event(self, run_id, event_type, sim_time, seq=None, **links):
        """Add an event; links are incident_id=, emergency_id=, ...

        seq can be passed by a caller that keeps count itself (saves a
        query per event); otherwise the next one is looked up.
        """

        event = RunEvent(
            run_id=run_id,
            seq=seq if seq is not None else self.next_seq(run_id),
            sim_time=sim_time,
            event_type=event_type,
            **links,
        )
        self.session.add(event)
        self.session.flush()

        return event

    def events(self, run_id, after_seq=0, limit=500):
        return self.session.scalars(
            select(RunEvent)
            .where(RunEvent.run_id == run_id, RunEvent.seq > after_seq)
            .order_by(RunEvent.seq)
            .limit(limit)
        ).all()

    # ------------------------------------------------------------
    # Stations and emergency vehicles
    # ------------------------------------------------------------

    def add_station(
        self, run_id, station_index, station_type, latitude, longitude, sim_time,
    ):
        station = Station(
            run_id=run_id,
            station_index=station_index,
            station_type=station_type,
            latitude=latitude,
            longitude=longitude,
            created_sim_time=sim_time,
        )
        self.session.add(station)
        self.session.flush()

        self.add_event(
            run_id, EventType.STATION_ADDED, sim_time, station_id=station.id
        )
        return station

    def station_by_index(self, run_id, station_index):
        return self.session.scalar(
            select(Station).where(
                Station.run_id == run_id, Station.station_index == station_index
            )
        )

    def add_emergency_vehicle(
        self, run_id, station_id, vehicle_index, vehicle_type, speed, sim_time,
    ):
        vehicle = EmergencyVehicle(
            run_id=run_id,
            station_id=station_id,
            vehicle_index=vehicle_index,
            vehicle_type=vehicle_type,
            speed=speed,
            created_sim_time=sim_time,
        )
        self.session.add(vehicle)
        self.session.flush()

        self.add_event(
            run_id, EventType.VEHICLE_ADDED, sim_time, vehicle_id=vehicle.id
        )
        return vehicle

    def vehicle_by_index(self, run_id, vehicle_index):
        return self.session.scalar(
            select(EmergencyVehicle).where(
                EmergencyVehicle.run_id == run_id,
                EmergencyVehicle.vehicle_index == vehicle_index,
            )
        )

    # ------------------------------------------------------------
    # Emergencies
    # ------------------------------------------------------------

    def dispatch_emergency(
        self, run_id, emergency_type, latitude, longitude, sim_time,
        vehicle_id, route_distance_m, nodes_explored, dispatched_sim_time=None,
    ):
        emergency = Emergency(
            run_id=run_id,
            emergency_type=emergency_type,
            latitude=latitude,
            longitude=longitude,
            status=EmergencyStatus.DISPATCHED,
            vehicle_id=vehicle_id,
            created_sim_time=sim_time,
            dispatched_sim_time=(
                sim_time if dispatched_sim_time is None else dispatched_sim_time
            ),
            route_distance_m=route_distance_m,
            nodes_explored=nodes_explored,
            reroutes=0,
        )
        self.session.add(emergency)
        self.session.flush()

        self.add_event(
            run_id, EventType.EMERGENCY_DISPATCHED,
            sim_time if dispatched_sim_time is None else dispatched_sim_time,
            emergency_id=emergency.id,
        )
        return emergency

    def cancel_emergency(
        self, run_id, emergency_type, latitude, longitude, sim_time, reason,
        vehicle_id=None,
    ):
        """An emergency nothing could be sent to (reason: no_vehicle or
        no_route). The API answers 404; the attempt is still history."""

        emergency = Emergency(
            run_id=run_id,
            emergency_type=emergency_type,
            latitude=latitude,
            longitude=longitude,
            status=EmergencyStatus.CANCELLED,
            cancel_reason=reason,
            vehicle_id=vehicle_id,
            created_sim_time=sim_time,
            reroutes=0,
        )
        self.session.add(emergency)
        self.session.flush()

        self.add_event(
            run_id, EventType.EMERGENCY_CANCELLED, sim_time,
            emergency_id=emergency.id,
        )
        return emergency

    def emergency_arrived(self, emergency_id, arrived_sim_time, reroutes, seq=None):
        emergency = self.session.get(Emergency, emergency_id)

        emergency.status = EmergencyStatus.ARRIVED
        emergency.arrived_sim_time = arrived_sim_time
        emergency.response_seconds = arrived_sim_time - emergency.created_sim_time
        emergency.reroutes = reroutes

        self.add_event(
            emergency.run_id, EventType.EMERGENCY_ARRIVED, arrived_sim_time,
            seq=seq, emergency_id=emergency.id,
        )
        return emergency

    def emergencies(self, run_id):
        return self.session.scalars(
            select(Emergency).where(Emergency.run_id == run_id).order_by(Emergency.id)
        ).all()

    # ------------------------------------------------------------
    # Incidents
    # ------------------------------------------------------------

    def open_incident(
        self, run_id, kind, road, sim_time, accident=None, emergency_id=None,
        seq=None,
    ):
        """Record a closure, or an accident (accident = the Accident object).

        A new accident on a road that already has one replaces it, as in
        the simulation: the old one ends with reason replaced.
        """

        if kind == IncidentKind.ACCIDENT:
            self._end_open(
                run_id, kind, road, sim_time, IncidentEndReason.REPLACED
            )

        incident = Incident(
            run_id=run_id,
            kind=kind,
            start_node=road.start_node,
            end_node=road.end_node,
            start_latitude=road.start[0],
            start_longitude=road.start[1],
            end_latitude=road.end[0],
            end_longitude=road.end[1],
            started_sim_time=sim_time,
            emergency_id=emergency_id,
        )

        if accident is not None:
            incident.severity = accident.severity
            incident.speed_factor = accident.speed_factor
            incident.capacity_factor = accident.capacity_factor
            incident.planned_end_sim_time = accident.clears_at

        self.session.add(incident)
        self.session.flush()

        self.add_event(
            run_id, INCIDENT_START_EVENTS[kind], sim_time,
            seq=seq, incident_id=incident.id,
        )
        return incident

    def close_incident(self, run_id, kind, road, sim_time, reason, seq=None):
        """End the open closure/accident on a road; None if there was none."""

        incident = self._end_open(run_id, kind, road, sim_time, reason)

        if incident is not None:
            self.add_event(
                run_id, INCIDENT_END_EVENTS[kind], sim_time,
                seq=seq, incident_id=incident.id,
            )

        return incident

    def _end_open(self, run_id, kind, road, sim_time, reason):
        incident = self.session.scalar(
            select(Incident)
            .where(
                Incident.run_id == run_id,
                Incident.kind == kind,
                Incident.ended_sim_time.is_(None),
                *_road_filter(road),
            )
            .with_for_update()
        )

        if incident is not None:
            incident.ended_sim_time = max(sim_time, incident.started_sim_time)
            incident.end_reason = reason
            self.session.flush()

        return incident

    def incidents(self, run_id, open_only=False):
        query = select(Incident).where(Incident.run_id == run_id)

        if open_only:
            query = query.where(Incident.ended_sim_time.is_(None))

        return self.session.scalars(query.order_by(Incident.id)).all()

    # ------------------------------------------------------------
    # Demand
    # ------------------------------------------------------------

    def add_surge(
        self, run_id, latitude, longitude, radius_metres, direction,
        vehicles_per_minute, starts_sim_time, ends_sim_time,
    ):
        surge = Surge(
            run_id=run_id,
            latitude=latitude,
            longitude=longitude,
            radius_metres=radius_metres,
            direction=SurgeDirection(direction),
            vehicles_per_minute=vehicles_per_minute,
            starts_sim_time=starts_sim_time,
            ends_sim_time=ends_sim_time,
            spawned=0,
            failed=0,
        )
        self.session.add(surge)
        self.session.flush()

        self.add_event(
            run_id, EventType.SURGE_STARTED, starts_sim_time, surge_id=surge.id
        )
        return surge

    def update_surge_counts(self, surge_id, spawned, failed):
        self.session.execute(
            update(Surge)
            .where(Surge.id == surge_id)
            .values(spawned=spawned, failed=failed)
        )

    def surges(self, run_id):
        return self.session.scalars(
            select(Surge).where(Surge.run_id == run_id).order_by(Surge.id)
        ).all()

    def add_fleet_spawn(
        self, run_id, sim_time, requested, spawned, failed, seed, wall_seconds,
    ):
        fleet = FleetSpawn(
            run_id=run_id,
            sim_time=sim_time,
            requested=requested,
            spawned=spawned,
            failed=failed,
            seed=seed,
            wall_seconds=wall_seconds,
        )
        self.session.add(fleet)
        self.session.flush()

        self.add_event(
            run_id, EventType.FLEET_ADDED, sim_time, fleet_spawn_id=fleet.id
        )
        return fleet

    def fleet_spawns(self, run_id):
        return self.session.scalars(
            select(FleetSpawn).where(FleetSpawn.run_id == run_id).order_by(FleetSpawn.id)
        ).all()

    # ------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------

    def add_metrics(self, run_id, sim_time, **values):
        self.session.add(RunMetric(run_id=run_id, sim_time=sim_time, **values))

    def metrics(self, run_id, from_time=None, to_time=None):
        query = select(RunMetric).where(RunMetric.run_id == run_id)

        if from_time is not None:
            query = query.where(RunMetric.sim_time >= from_time)

        if to_time is not None:
            query = query.where(RunMetric.sim_time <= to_time)

        return self.session.scalars(query.order_by(RunMetric.sim_time)).all()


# ============================================================
# Optimiser results
# ============================================================

def optimiser_request_key(
    kind, graph_sha256, candidates, closed_segments, scenarios, seed,
    target_seconds, speed, station_count=None, version=OPTIMISER_VERSION,
    distribution="uniform",
):
    """A fingerprint of everything an optimisation's answer depends on.

    The optimiser is deterministic, so two requests with the same key get
    the same answer: the same map, settings, candidates (the (lat, lon)
    points in the order given, since order can break ties), closed roads
    (in any order) and optimiser version.
    """

    key = {
        "version": int(version),
        "kind": OptimiserKind(kind).value,
        "graph": graph_sha256,
        "candidates": [[float(a), float(b)] for a, b in candidates],
        "closed": sorted(
            [[list(map(float, p)) for p in sorted(segment)] for segment in closed_segments]
        ),
        "scenarios": int(scenarios),
        "seed": int(seed),
        "target_seconds": float(target_seconds),
        "speed": float(speed),
        "stations": None if station_count is None else int(station_count),
    }

    # Left out when uniform, so earlier keys (and saved answers) still match
    if distribution != "uniform":
        key["distribution"] = distribution

    text = json.dumps(key, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


class OptimiserRepository:

    def __init__(self, session):
        self.session = session

    def _new_run(self, kind, response, **settings):
        return OptimiserRun(
            id=settings["optimiser_run_id"],
            kind=kind,
            simulation_run_id=settings.get("simulation_run_id"),
            scenarios=response["scenarios"],
            seed=response["seed"],
            target_seconds=response["target_seconds"],
            speed=response["speed"],
            candidate_source=CandidateSource(settings["candidate_source"]),
            candidates_considered=settings["candidates_considered"],
            closed_roads=response["closed_roads"],
            graph_sha256=settings["graph_sha256"],
            request_key=settings["request_key"],
            explanation=list(response["explanation"]),
            compute_ms=settings["compute_ms"],
        )

    def save_ranking(self, response, **settings):
        """Save an /optimiser/stations answer (one row per candidate)."""

        run = self._new_run(OptimiserKind.RANKING, response, **settings)

        run.candidates = [
            OptimiserCandidate(
                rank=result["rank"],
                node=result["node"],
                latitude=result["latitude"],
                longitude=result["longitude"],
                average_seconds=result["average_seconds"],
                worst_seconds=result["worst_seconds"],
                within_target_percent=result["within_target_percent"],
                unreachable=result["unreachable"],
                ranking_seconds=result["ranking_seconds"],
            )
            for result in response["results"]
        ]

        self.session.add(run)
        self.session.flush()
        return run

    def save_station_set(self, response, **settings):
        """Save an /optimiser/station-set answer: the set's figures, one
        row per chosen station and one per baseline."""

        run = self._new_run(OptimiserKind.STATION_SET, response, **settings)
        result = response["result"]

        run.station_count = response["count"]
        run.candidates_considered = response["candidates_considered"]
        run.set_average_seconds = result["average_seconds"]
        run.set_worst_seconds = result["worst_seconds"]
        run.set_within_target_percent = result["within_target_percent"]
        run.set_unreachable = result["unreachable"]
        run.set_ranking_seconds = result["ranking_seconds"]

        run.selected_stations = [
            OptimiserSelectedStation(
                pick_order=pick["order"],
                node=pick["node"],
                latitude=pick["latitude"],
                longitude=pick["longitude"],
                average_seconds_after=pick["average_seconds_after"],
                within_target_percent_after=pick["within_target_percent_after"],
            )
            for pick in response["stations"]
        ]

        run.baselines = [
            OptimiserBaseline(
                position=position,
                name=baseline["name"],
                label=baseline["label"],
                stations=baseline["stations"],
                average_seconds=baseline["average_seconds"],
                worst_seconds=baseline["worst_seconds"],
                within_target_percent=baseline["within_target_percent"],
                unreachable=baseline["unreachable"],
                ranking_seconds=baseline["ranking_seconds"],
            )
            for position, baseline in enumerate(response["baselines"])
        ]

        self.session.add(run)
        self.session.flush()
        return run

    def get(self, optimiser_run_id):
        return self.session.get(OptimiserRun, optimiser_run_id)

    def delete(self, optimiser_run_id):
        result = self.session.execute(
            delete(OptimiserRun).where(OptimiserRun.id == optimiser_run_id)
        )
        return result.rowcount > 0

    def latest_for_key(self, request_key):
        """The newest saved answer to an identical request, if any."""

        return self.session.scalar(
            select(OptimiserRun)
            .where(OptimiserRun.request_key == request_key)
            .order_by(OptimiserRun.created_at.desc())
            .limit(1)
        )

    def list(self, kind=None, simulation_run_id=None, limit=50, offset=0):
        query = select(OptimiserRun)
        count = select(func.count()).select_from(OptimiserRun)

        filters: Sequence = []
        if kind is not None:
            filters.append(OptimiserRun.kind == OptimiserKind(kind))
        if simulation_run_id is not None:
            filters.append(OptimiserRun.simulation_run_id == simulation_run_id)

        rows = self.session.scalars(
            query.where(*filters)
            .order_by(OptimiserRun.created_at.desc(), OptimiserRun.id)
            .limit(limit).offset(offset)
        ).all()

        return rows, self.session.scalar(count.where(*filters))
