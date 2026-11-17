"""Saving what happens in a live run to the database.

Each LiveRun has a recorder. Without a database it is a NullRecorder and
does nothing, so the API behaves exactly as before Stage 3. With one it is
a RunRecorder, which the endpoints call while they hold the run's lock:

* explicit calls for things only the endpoint knows about (a station or
  vehicle added, an emergency dispatched or refused, a fleet, a surge,
  rerouting switched on or off);
* sync() after anything that changes the simulation, including every
  step. It reads what the simulation itself logged since last time:
  roads closed and reopened and accidents started and cleared (the
  simulation's `events` list), ambulances that arrived, and every
  METRICS_INTERVAL simulated seconds a metrics snapshot.

A step where none of that happened costs no database work at all, so a
quiet simulation does not write a row per second. Counters that change
every step (the run's simulated time and vehicle counts, surge counts)
are saved along with those writes, not on their own; after a crash they
can be up to METRICS_INTERVAL simulated seconds behind (the benchmark in
docs/persistence_benchmark.md shows why: one write costs ~2 ms).

Every call is one transaction. If the database fails, the simulation
carries on (it is in memory and correct), but the run's history would now
have a gap, so the recorder stops recording that run and reports
recording = "failed" instead of pretending it is complete.
"""

import logging
import math

from backend.db.models import (
    IncidentEndReason,
    IncidentKind,
    RunStatus,
)
from backend.db.repository import Road, RunRepository


logger = logging.getLogger(__name__)

# Seconds of simulated time between metrics snapshots
METRICS_INTERVAL = 60


class NullRecorder:
    """No database: nothing is saved."""

    status = "off"

    def started(self, graph_sha256):
        return True

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class RunRecorder:

    def __init__(self, database, run, node_by_point, metrics_interval=METRICS_INTERVAL):
        self.database = database
        self.run = run
        self.node_by_point = node_by_point
        self.metrics_interval = metrics_interval

        self.status = "on"

        # Simulation objects -> their database ids
        self.station_ids = {}       # station index -> stations.id
        self.vehicle_ids = {}       # vehicle index -> emergency_vehicles.id
        self.emergency_ids = {}     # Emergency -> emergencies.id (not yet arrived)
        self.surge_ids = {}         # Surge -> (surges.id, (spawned, failed))

        # How far through simulation.events we have recorded
        self.event_cursor = 0
        self.next_metrics_time = metrics_interval

    # ------------------------------------------------------------
    # Plumbing
    # ------------------------------------------------------------

    def _write(self, action, what):
        """Run action(repo) in one transaction; on failure stop recording."""

        if self.status != "on":
            return None

        try:
            with self.database.transaction() as session:
                return action(RunRepository(session))

        except Exception:
            self.status = "failed"
            logger.exception(
                "Could not save %s for simulation %s; no longer recording it",
                what, self.run.id,
            )
            return None

    def _progress(self, repo):
        vehicles = self.run.simulation.vehicles
        repo.record_progress(
            self.run.id_uuid,
            self.run.simulation.time,
            len(vehicles),
            sum(1 for vehicle in vehicles if vehicle.finished),
        )

    def _road(self, segment):
        return Road.from_segment(segment, self.node_by_point)

    # ------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------

    def started(self, graph_sha256):
        """Create the run's row. Returns False if the database failed (the
        API then refuses to start the run rather than run it unrecorded)."""

        run = self.run

        def create(repo):
            repo.create_run(
                run.id_uuid,
                run.start,
                run.destination,
                graph_sha256,
                rerouting_enabled=run.traffic.policy.enabled,
                vehicles_total=len(run.simulation.vehicles),
                restarted_from=run.restarted_from,
                traffic_model=run.simulation.road_model.name,
            )
            return True

        return bool(self._write(create, "the new run"))

    def ended(self, status):
        """The run stopped being live (stopped, replaced or interrupted)."""

        def end(repo):
            self._record_changes(repo)
            self._progress(repo)
            repo.end_run(self.run.id_uuid, RunStatus(status), self.run.simulation.time)

        if self.status == "on":
            self._write(end, "the end of the run")
        elif self.status == "failed":
            # Its history has a gap, but it should not look live forever
            self.status = "on"
            self._write(
                lambda repo: repo.end_run(
                    self.run.id_uuid, RunStatus(status), self.run.simulation.time
                ),
                "the end of the run",
            )
            self.status = "failed"
            return

        if self.status == "on":
            self.status = "ended"

    # ------------------------------------------------------------
    # What the simulation logged itself
    # ------------------------------------------------------------

    def sync(self, manual_clear=False, closure_emergency=None):
        """Save what changed since the last call.

        manual_clear: accidents cleared now were cleared by a user
        (otherwise they expired). closure_emergency: the Emergency a road
        closed now was closed for (/emergency/block-ahead).
        """

        if self.status != "on":
            return

        simulation = self.run.simulation

        new_events = len(simulation.events) > self.event_cursor
        arrivals = any(e.resolved for e in self.emergency_ids)
        metrics_due = simulation.time >= self.next_metrics_time

        # Surge counts change every step while a surge runs. Like sim_time
        # they are progress: saved with any other write and at each metrics
        # snapshot, but only forcing a write of their own once the surge
        # has ended (so the final counts are always saved).
        surge_finished = any(
            (surge.spawned, surge.failed) != counts
            and not surge.active(simulation.time)
            for surge, (_, counts) in self.surge_ids.items()
        )

        if not (new_events or arrivals or surge_finished or metrics_due):
            return

        def save(repo):
            self._record_changes(repo, manual_clear, closure_emergency)

            if metrics_due:
                self._record_metrics(repo)

            self._progress(repo)

        self._write(save, "simulation changes")

    def _record_changes(self, repo, manual_clear=False, closure_emergency=None):
        simulation = self.run.simulation
        run_id = self.run.id_uuid

        closure_emergency_id = (
            self.emergency_ids.get(closure_emergency)
            if closure_emergency is not None else None
        )

        for event in simulation.events[self.event_cursor:]:
            kind = event["type"]
            road = self._road(event["segment"])
            time = event["time"]

            if kind == "road_closed":
                repo.open_incident(
                    run_id, IncidentKind.CLOSURE, road, time,
                    emergency_id=closure_emergency_id,
                )

            elif kind == "road_reopened":
                repo.close_incident(
                    run_id, IncidentKind.CLOSURE, road, time,
                    IncidentEndReason.REOPENED,
                )

            elif kind == "accident":
                accident = simulation.accidents.get(tuple(sorted(event["segment"])))

                if accident is None:
                    # Started and cleared before we saw it (cannot happen
                    # through the API, which syncs after every request)
                    logger.warning("Accident on %s vanished before it was saved", road)
                    continue

                repo.open_incident(
                    run_id, IncidentKind.ACCIDENT, road, time, accident=accident
                )

            elif kind == "accident_cleared":
                repo.close_incident(
                    run_id, IncidentKind.ACCIDENT, road, time,
                    IncidentEndReason.CLEARED if manual_clear
                    else IncidentEndReason.EXPIRED,
                )

        for emergency in [e for e in self.emergency_ids if e.resolved]:
            vehicle = emergency.assigned_vehicle
            repo.emergency_arrived(
                self.emergency_ids.pop(emergency),
                emergency.resolved_at,
                vehicle.reroutes if vehicle is not None else 0,
            )

        for surge, (surge_id, counts) in list(self.surge_ids.items()):
            if (surge.spawned, surge.failed) != counts:
                repo.update_surge_counts(surge_id, surge.spawned, surge.failed)
                self.surge_ids[surge] = (surge_id, (surge.spawned, surge.failed))

        # Only move on once it is written (the transaction commits after
        # this returns; if it fails, recording stops anyway)
        self.event_cursor = len(simulation.events)

    def _record_metrics(self, repo):
        simulation = self.run.simulation
        vehicles = simulation.vehicles
        driving = [v for v in vehicles if not v.finished]

        factors = [
            v.speed_factor for v in driving
            if v.speed_factor is not None and math.isfinite(v.speed_factor)
        ]

        repo.add_metrics(
            self.run.id_uuid,
            simulation.time,
            vehicles_total=len(vehicles),
            vehicles_finished=len(vehicles) - len(driving),
            mean_speed_factor=(
                min(1.0, max(0.0, sum(factors) / len(factors))) if factors else None
            ),
            congested_roads=sum(
                1 for data in simulation.get_congestion().values()
                if data["level"] == "heavy"
            ),
            closed_roads=len(simulation.get_blocked_segments()),
            active_accidents=len(simulation.accidents),
            reroutes_total=sum(v.reroutes for v in vehicles),
        )

        interval = self.metrics_interval
        self.next_metrics_time = (simulation.time // interval + 1) * interval

    # ------------------------------------------------------------
    # Things only the endpoints know about
    # ------------------------------------------------------------

    def station_added(self, station_index, station):
        def save(repo):
            row = repo.add_station(
                self.run.id_uuid, station_index, station.station_type,
                station.latitude, station.longitude, self.run.simulation.time,
            )
            self.station_ids[station_index] = row.id

        self._write(save, "a station")

    def vehicle_added(self, station_index, vehicle_index, vehicle):
        def save(repo):
            row = repo.add_emergency_vehicle(
                self.run.id_uuid, self.station_ids[station_index], vehicle_index,
                vehicle.vehicle_type, vehicle.speed, self.run.simulation.time,
            )
            self.vehicle_ids[vehicle_index] = row.id
            self._progress(repo)

        self._write(save, "an emergency vehicle")

    def emergency_dispatched(self, emergency, vehicle_index, distance, nodes_explored):
        def save(repo):
            row = repo.dispatch_emergency(
                self.run.id_uuid, emergency.emergency_type,
                emergency.latitude, emergency.longitude, emergency.created_at,
                self.vehicle_ids[vehicle_index], distance, nodes_explored,
            )
            self.emergency_ids[emergency] = row.id

        self._write(save, "a dispatched emergency")

    def emergency_refused(self, emergency, reason, vehicle_index=None):
        """Nothing could be sent (reason: no_vehicle or no_route)."""

        def save(repo):
            repo.cancel_emergency(
                self.run.id_uuid, emergency.emergency_type,
                emergency.latitude, emergency.longitude, emergency.created_at,
                reason,
                vehicle_id=self.vehicle_ids.get(vehicle_index),
            )

        self._write(save, "a refused emergency")

    def fleet_added(self, outcome, seed):
        def save(repo):
            repo.add_fleet_spawn(
                self.run.id_uuid, self.run.simulation.time,
                outcome["requested"], outcome["spawned"], outcome["failed"],
                seed, outcome["seconds"],
            )
            self._progress(repo)

        self._write(save, "a fleet")

    def surge_started(self, surge):
        def save(repo):
            zone = surge.zone
            row = repo.add_surge(
                self.run.id_uuid, zone.latitude, zone.longitude, zone.radius_metres,
                surge.direction, surge.vehicles_per_minute,
                surge.starts_at, surge.ends_at,
            )
            self.surge_ids[surge] = (row.id, (0, 0))

        self._write(save, "a surge")

    def rerouting_changed(self, enabled):
        self._write(
            lambda repo: repo.set_rerouting(
                self.run.id_uuid, enabled, self.run.simulation.time
            ),
            "the rerouting setting",
        )
