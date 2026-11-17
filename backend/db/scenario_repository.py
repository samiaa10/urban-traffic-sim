"""Reading and writing scenario studies and their results.

Like repository.py, nothing here commits: the caller owns the
transaction. result_from_rows() turns stored rows back into the dict
run_scenario returns, so backend.scenarios.compare works the same on
results fresh from a run and results loaded from the database.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.orm import selectinload, undefer_group

from backend.db.models import (
    TRIP_STATUS_CODES,
    Scenario,
    ScenarioAreaImpact,
    ScenarioResult,
    ScenarioRoadImpact,
    ScenarioStatus,
    ScenarioStudy,
    ScenarioTimeseries,
    StudyStatus,
)


STATUS_NAMES = {code: name for name, code in TRIP_STATUS_CODES.items()}

UNFINISHED_STUDY = (StudyStatus.QUEUED, StudyStatus.RUNNING)
UNFINISHED_SCENARIO = (ScenarioStatus.QUEUED, ScenarioStatus.RUNNING)


def _now():
    return datetime.now(timezone.utc)


class ScenarioRepository:

    def __init__(self, session):
        self.session = session

    # ------------------------------------------------------------
    # Studies
    # ------------------------------------------------------------

    def create_study(
        self, name, specs, metric, workers, graph_sha256, engine_version,
        baseline_position=None,
    ):
        """A study and its scenarios (all queued). specs are ScenarioSpecs,
        run in the order given."""

        study = ScenarioStudy(
            id=uuid.uuid4(),
            name=name,
            status=StudyStatus.QUEUED,
            metric=metric,
            scenario_count=len(specs),
            completed_count=0,
            failed_count=0,
            workers=workers,
            graph_sha256=graph_sha256,
            engine_version=engine_version,
        )

        study.scenarios = [
            Scenario(
                id=uuid.uuid4(),
                position=position,
                name=spec.name,
                is_baseline=position == baseline_position,
                spec=spec.model_dump(mode="json"),
                seed=spec.seed,
                spec_fingerprint=spec.fingerprint(),
                trip_signature=spec.trip_signature(),
                status=ScenarioStatus.QUEUED,
            )
            for position, spec in enumerate(specs)
        ]

        self.session.add(study)
        self.session.flush()
        return study

    def get_study(self, study_id):
        return self.session.get(ScenarioStudy, study_id)

    def _lock_for_scenario(self, scenario_id):
        """Lock a scenario's study, then the scenario (read fresh).

        Every write that touches both locks them in this order, study
        first: saving a result while the study is being cancelled would
        otherwise deadlock (the two would wait for each other's lock).
        """

        study_id = self.session.scalar(
            select(Scenario.study_id).where(Scenario.id == scenario_id)
        )
        if study_id is None:
            return None

        self._locked_study(study_id)
        return self.session.get(
            Scenario, scenario_id, with_for_update=True, populate_existing=True
        )

    def _locked_study(self, study_id):
        """The study row, locked and read fresh: its counts are changed by
        UPDATE statements, so the copy in the session may be stale."""

        return self.session.get(
            ScenarioStudy, study_id, with_for_update=True, populate_existing=True
        )

    def list_studies(self, limit=50, offset=0):
        rows = self.session.scalars(
            select(ScenarioStudy)
            .order_by(ScenarioStudy.created_at.desc(), ScenarioStudy.id)
            .limit(limit).offset(offset)
        ).all()
        total = self.session.scalar(select(func.count()).select_from(ScenarioStudy))
        return rows, total

    def scenarios(self, study_id, with_results=False):
        query = select(Scenario).where(Scenario.study_id == study_id).order_by(Scenario.position)
        if with_results:
            query = query.options(
                selectinload(Scenario.result),
                selectinload(Scenario.roads),
            )
        return self.session.scalars(query).all()

    def get_scenario(self, scenario_id, full=False):
        query = select(Scenario).where(Scenario.id == scenario_id)
        if full:
            query = query.options(
                selectinload(Scenario.result).options(undefer_group("trips")),
                selectinload(Scenario.roads),
                selectinload(Scenario.areas),
                selectinload(Scenario.timeseries),
            )
        return self.session.scalar(query)

    def queued_scenarios(self, study_id):
        return self.session.scalars(
            select(Scenario)
            .where(Scenario.study_id == study_id, Scenario.status == ScenarioStatus.QUEUED)
            .order_by(Scenario.position)
        ).all()

    def start_study(self, study_id):
        study = self._locked_study(study_id)
        if study is not None and study.status == StudyStatus.QUEUED:
            study.status = StudyStatus.RUNNING
            study.started_at = _now()
            self.session.flush()
        return study

    def finish_study_if_done(self, study_id):
        """Mark the study completed once every scenario has finished."""

        study = self._locked_study(study_id)
        if study is None or study.status not in UNFINISHED_STUDY:
            return study

        if study.completed_count + study.failed_count == study.scenario_count:
            study.status = StudyStatus.COMPLETED
            study.finished_at = _now()
            if study.started_at is not None:
                study.wall_seconds = (study.finished_at - study.started_at).total_seconds()
            self.session.flush()

        return study

    def end_study(self, study_id, status):
        """Cancel or interrupt a study: scenarios not yet finished get the
        same status. Returns False if it had already ended."""

        study = self._locked_study(study_id)
        if study is None or study.status not in UNFINISHED_STUDY:
            return False

        scenario_status = (
            ScenarioStatus.CANCELLED if status == StudyStatus.CANCELLED
            else ScenarioStatus.INTERRUPTED
        )

        self.session.execute(
            update(Scenario)
            .where(Scenario.study_id == study_id, Scenario.status.in_(UNFINISHED_SCENARIO))
            .values(status=scenario_status, finished_at=_now())
        )

        study.status = status
        study.finished_at = _now()
        if study.started_at is not None:
            study.wall_seconds = (study.finished_at - study.started_at).total_seconds()
        self.session.flush()
        return True

    def resume_study(self, study_id):
        """Put an interrupted or cancelled study back in the queue.

        Its unfinished scenarios (interrupted or cancelled) are queued
        again; completed and failed ones keep their results. Scenarios are
        deterministic, so a re-run gives the result the first run would
        have. Returns how many scenarios were queued again, or None if the
        study cannot be resumed (unknown, still running or completed).
        """

        study = self._locked_study(study_id)
        if study is None or study.status not in (
            StudyStatus.INTERRUPTED, StudyStatus.CANCELLED
        ):
            return None

        result = self.session.execute(
            update(Scenario)
            .where(
                Scenario.study_id == study_id,
                Scenario.status.in_((ScenarioStatus.INTERRUPTED, ScenarioStatus.CANCELLED)),
            )
            .values(status=ScenarioStatus.QUEUED, started_at=None, finished_at=None)
        )

        study.status = StudyStatus.QUEUED
        study.finished_at = None
        study.wall_seconds = None
        self.session.flush()

        # Nothing left to run (every scenario had finished): done again
        if result.rowcount == 0:
            self.finish_study_if_done(study_id)

        return result.rowcount

    def interrupt_unfinished(self):
        """At start-up: studies left queued or running by a server that
        stopped are interrupted. Returns their ids."""

        study_ids = self.session.scalars(
            select(ScenarioStudy.id)
            .where(ScenarioStudy.status.in_(UNFINISHED_STUDY))
            .with_for_update()
        ).all()

        for study_id in study_ids:
            self.end_study(study_id, StudyStatus.INTERRUPTED)

        return list(study_ids)

    def delete_study(self, study_id):
        study = self.session.get(ScenarioStudy, study_id)
        if study is None:
            return False
        self.session.delete(study)
        self.session.flush()
        return True

    # ------------------------------------------------------------
    # Scenarios
    # ------------------------------------------------------------

    def start_scenario(self, scenario_id):
        """Queued -> running. False if it was cancelled meanwhile."""

        study_id = self.session.scalar(
            select(Scenario.study_id).where(Scenario.id == scenario_id)
        )
        if study_id is None:
            return False
        self._locked_study(study_id)      # study first (see _lock_for_scenario)

        result = self.session.execute(
            update(Scenario)
            .where(Scenario.id == scenario_id, Scenario.status == ScenarioStatus.QUEUED)
            .values(status=ScenarioStatus.RUNNING, started_at=_now())
        )
        return result.rowcount == 1

    def save_result(self, scenario_id, result):
        """Store a run_scenario result and count it in its study.

        Does nothing (returns False) if the scenario is no longer running,
        for example because its study was cancelled meanwhile.
        """

        scenario = self._lock_for_scenario(scenario_id)
        if scenario is None or scenario.status != ScenarioStatus.RUNNING:
            return False

        summary = result["summary"]
        emergencies = result["emergencies"]
        execution = result["execution"]
        trips = result["trips"]

        self.session.add(ScenarioResult(
            scenario_id=scenario_id,
            engine_version=result["engine_version"],
            trips_requested=summary["trips_requested"],
            trips_spawned=summary["trips_spawned"],
            trips_unroutable=summary["trips_unroutable"],
            trips_finished=summary["trips_finished"],
            completion_percent=summary["completion_percent"],
            total_delay_vehicle_hours=summary["total_delay_vehicle_hours"],
            total_travel_vehicle_hours=summary["total_travel_vehicle_hours"],
            mean_delay_seconds=summary["mean_delay_seconds"],
            p95_delay_seconds=summary["p95_delay_seconds"],
            max_delay_seconds=summary["max_delay_seconds"],
            mean_increase_seconds=summary["mean_increase_seconds"],
            p95_increase_seconds=summary["p95_increase_seconds"],
            max_increase_seconds=summary["max_increase_seconds"],
            reroutes=summary["reroutes"],
            simulated_seconds=summary["simulated_seconds"],
            hit_time_limit=summary["hit_time_limit"],
            emergencies_requested=emergencies["requested"],
            emergencies_dispatched=emergencies["dispatched"],
            emergencies_refused=emergencies["refused"],
            emergencies_arrived=emergencies["arrived"],
            emergency_mean_response_seconds=emergencies["mean_response_seconds"],
            emergency_worst_response_seconds=emergencies["worst_response_seconds"],
            emergency_within_target_percent=emergencies["within_target_percent"],
            emergency_detail=emergencies.get("detail"),
            wall_seconds=execution["wall_seconds"],
            cpu_seconds=execution["cpu_seconds"],
            peak_rss_mb=execution["peak_rss_mb"],
            vehicle_seconds=execution["vehicle_seconds"],
            vehicle_seconds_per_second=execution["vehicle_seconds_per_second"],
            trip_origin=trips["origin"],
            trip_destination=trips["destination"],
            trip_journey_seconds=trips["journey_seconds"],
            trip_free_flow_seconds=trips["free_flow_seconds"],
            trip_delay_seconds=trips["delay_seconds"],
            trip_status=[TRIP_STATUS_CODES[s] for s in trips["status"]],
        ))

        self.session.add_all(
            ScenarioRoadImpact(
                scenario_id=scenario_id,
                rank=road["rank"],
                start_node=road["start_node"],
                end_node=road["end_node"],
                start_latitude=road["start"]["latitude"],
                start_longitude=road["start"]["longitude"],
                end_latitude=road["end"]["latitude"],
                end_longitude=road["end"]["longitude"],
                delay_vehicle_seconds=road["delay_vehicle_seconds"],
                vehicle_seconds=road["vehicle_seconds"],
                peak_vehicles=road["peak_vehicles"],
                delay_share=road["delay_share"],
            )
            for road in result["roads"]
        )

        self.session.add_all(
            ScenarioAreaImpact(scenario_id=scenario_id, **area)
            for area in result["areas"]
        )

        self.session.add_all(
            ScenarioTimeseries(
                scenario_id=scenario_id,
                sim_time=row["time"],
                **{k: v for k, v in row.items() if k != "time"},
            )
            for row in result["timeseries"]
        )

        scenario.status = ScenarioStatus.COMPLETED
        scenario.finished_at = _now()

        self.session.execute(
            update(ScenarioStudy)
            .where(ScenarioStudy.id == scenario.study_id)
            .values(completed_count=ScenarioStudy.completed_count + 1)
        )
        self.session.flush()
        self.finish_study_if_done(scenario.study_id)
        return True

    def fail_scenario(self, scenario_id, error):
        scenario = self._lock_for_scenario(scenario_id)
        if scenario is None or scenario.status not in UNFINISHED_SCENARIO:
            return False

        scenario.status = ScenarioStatus.FAILED
        scenario.error = (error or "unknown error")[:2000]
        scenario.finished_at = _now()

        self.session.execute(
            update(ScenarioStudy)
            .where(ScenarioStudy.id == scenario.study_id)
            .values(failed_count=ScenarioStudy.failed_count + 1)
        )
        self.session.flush()
        self.finish_study_if_done(scenario.study_id)
        return True


# ------------------------------------------------------------
# Rows -> the dict run_scenario returns
# ------------------------------------------------------------

def summary_from_rows(scenario):
    """Enough of a completed scenario to rank it (summary, emergencies,
    execution), without loading its per-trip arrays; None if no result."""

    return result_from_rows(scenario, trips=False)


def result_from_rows(scenario, trips=True):
    """A completed scenario (loaded with result, roads, areas, timeseries)
    as run_scenario's result dict; None if it has no result. trips=False
    leaves out the per-trip arrays (and roads, areas, time series)."""

    r = scenario.result
    if r is None:
        return None

    if not trips:
        return {
            "trip_signature": scenario.trip_signature,
            "summary": _summary(r),
            "emergencies": _emergencies(r),
            "execution": _execution(r),
        }

    return {
        "engine_version": r.engine_version,
        "graph_sha256": None,
        "spec_fingerprint": scenario.spec_fingerprint,
        "trip_signature": scenario.trip_signature,
        "summary": _summary(r),
        "emergencies": _emergencies(r),
        "trips": {
            "origin": list(r.trip_origin),
            "destination": list(r.trip_destination),
            "journey_seconds": list(r.trip_journey_seconds),
            "free_flow_seconds": list(r.trip_free_flow_seconds),
            "delay_seconds": list(r.trip_delay_seconds),
            "status": [STATUS_NAMES[code] for code in r.trip_status],
        },
        "roads": [
            {
                "rank": road.rank,
                "start_node": road.start_node,
                "end_node": road.end_node,
                "start": {"latitude": road.start_latitude, "longitude": road.start_longitude},
                "end": {"latitude": road.end_latitude, "longitude": road.end_longitude},
                "delay_vehicle_seconds": road.delay_vehicle_seconds,
                "vehicle_seconds": road.vehicle_seconds,
                "peak_vehicles": road.peak_vehicles,
                "delay_share": road.delay_share,
            }
            for road in scenario.roads
        ],
        "areas": [
            {
                "rank": area.rank,
                "cell_row": area.cell_row,
                "cell_col": area.cell_col,
                "south": area.south,
                "west": area.west,
                "north": area.north,
                "east": area.east,
                "delay_vehicle_seconds": area.delay_vehicle_seconds,
                "roads_affected": area.roads_affected,
            }
            for area in scenario.areas
        ],
        "timeseries": [
            {
                "time": row.sim_time,
                "active_vehicles": row.active_vehicles,
                "finished_vehicles": row.finished_vehicles,
                "delay_vehicle_seconds": row.delay_vehicle_seconds,
                "vehicle_seconds": row.vehicle_seconds,
                "mean_speed_factor": row.mean_speed_factor,
                "closed_roads": row.closed_roads,
                "accidents": row.accidents,
            }
            for row in scenario.timeseries
        ],
        "execution": _execution(r),
    }


def _summary(r):
    return {
        "trips_requested": r.trips_requested,
        "trips_spawned": r.trips_spawned,
        "trips_unroutable": r.trips_unroutable,
        "trips_finished": r.trips_finished,
        "completion_percent": r.completion_percent,
        "total_delay_vehicle_hours": r.total_delay_vehicle_hours,
        "total_travel_vehicle_hours": r.total_travel_vehicle_hours,
        "mean_delay_seconds": r.mean_delay_seconds,
        "p95_delay_seconds": r.p95_delay_seconds,
        "max_delay_seconds": r.max_delay_seconds,
        "mean_increase_seconds": r.mean_increase_seconds,
        "p95_increase_seconds": r.p95_increase_seconds,
        "max_increase_seconds": r.max_increase_seconds,
        "reroutes": r.reroutes,
        "simulated_seconds": r.simulated_seconds,
        "hit_time_limit": r.hit_time_limit,
    }


def _emergencies(r):
    return {
        "requested": r.emergencies_requested,
        "dispatched": r.emergencies_dispatched,
        "refused": r.emergencies_refused,
        "arrived": r.emergencies_arrived,
        "mean_response_seconds": r.emergency_mean_response_seconds,
        "worst_response_seconds": r.emergency_worst_response_seconds,
        "within_target_percent": r.emergency_within_target_percent,
        # Queue and per-service figures (None for results saved before them)
        "detail": r.emergency_detail,
    }


def _execution(r):
    return {
        "wall_seconds": r.wall_seconds,
        "cpu_seconds": r.cpu_seconds,
        "peak_rss_mb": r.peak_rss_mb,
        "vehicle_seconds": r.vehicle_seconds,
        "vehicle_seconds_per_second": r.vehicle_seconds_per_second,
    }
