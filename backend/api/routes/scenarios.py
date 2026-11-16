"""Scenario studies: create, follow, compare (Stage 4).

A study is a batch of scenarios (full simulations) run in the background
by the JobManager (backend/scenarios/jobs.py). Creating one answers 202
straight away; GET /scenarios/studies/{id} shows progress and, as results
arrive, the ranking and an explanation of it.

Everything that stores or reads results needs a database (503 without
one). /scenarios/metrics, /scenarios/presets and /scenarios/nearest-road
work without.
"""

import math
import uuid
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.api.context import AppContext
from backend.api.routes.common import get_context
from backend.scenarios import sweep
from backend.scenarios.compare import (
    DEFAULT_METRIC,
    METRICS,
    compare,
    explain,
    paired,
    rank,
    road_changes,
)
from backend.scenarios.runner import ENGINE_VERSION
from backend.scenarios.spec import ScenarioSpec, validate_against_graph
from backend.simulation.demand import CITY_CENTRE, Zone


router = APIRouter(prefix="/scenarios")


# ------------------------------------------------------------
# Requests
# ------------------------------------------------------------

class StudyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field("Study", min_length=1, max_length=120)
    scenarios: List[dict] = Field(min_length=1)
    # Index of the scenario the others are compared with, if any
    baseline: Optional[int] = Field(None, ge=0)
    metric: str = DEFAULT_METRIC


class ZoneRequest(BaseModel):
    latitude: float
    longitude: float
    radius_metres: float = Field(1000, ge=100, le=5000)


class LayoutRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    stations: List[dict] = Field(min_length=1, max_length=20)


class SweepRequest(BaseModel):
    """A study generated from one scenario (see backend/scenarios/sweep.py)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field("Sweep", min_length=1, max_length=120)
    base: dict
    kind: Literal["seeds", "each_closure", "random_closures", "station_layouts"]
    count: int = Field(10, ge=1, le=5000)
    first_seed: int = Field(1, ge=0)
    roads: List[List[int]] = Field(default_factory=list)
    closures_per_scenario: int = Field(1, ge=1, le=50)
    zone: Optional[ZoneRequest] = None
    sweep_seed: int = 1
    layouts: List[LayoutRequest] = Field(default_factory=list)
    metric: str = DEFAULT_METRIC


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

def require_jobs(context):
    if context.database is None or context.jobs is None:
        raise HTTPException(
            status_code=503,
            detail="Scenario studies need a database (DATABASE_URL is not set)",
        )
    return context.jobs


def parse_id(value, what):
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"{what} not found")


def check_metric(metric):
    if metric not in METRICS:
        raise HTTPException(
            status_code=400, detail="metric must be one of " + ", ".join(METRICS)
        )


def parse_specs(context, raw_specs):
    """Validate every scenario; 400 listing the problems by position."""

    specs, problems = [], []
    world = context.scenario_world()

    for index, raw in enumerate(raw_specs):
        try:
            spec = ScenarioSpec.model_validate(raw)
        except ValidationError as error:
            for item in error.errors():
                where = ".".join(str(part) for part in item["loc"])
                problems.append(f"scenarios[{index}].{where}: {item['msg']}")
            continue

        for problem in validate_against_graph(spec, context.graph):
            problems.append(f"scenarios[{index}].{problem}")

        for number, surge in enumerate(spec.surges):
            zone = Zone(surge.latitude, surge.longitude, surge.radius_metres)
            if not world.demand.nodes_in(zone):
                problems.append(
                    f"scenarios[{index}].surges[{number}]: no usable road junction inside the zone"
                )

        specs.append(spec)

    if problems:
        raise HTTPException(status_code=400, detail=problems[:50])

    return unique_names(specs)


def unique_names(specs):
    """Scenarios in one study need different names (comparisons use them)."""

    seen = {}
    named = []

    for spec in specs:
        count = seen.get(spec.name, 0) + 1
        seen[spec.name] = count
        if count > 1:
            spec = spec.model_copy(update={"name": f"{spec.name} ({count})"[:120]})
        named.append(spec)

    return named


def create_study(context, name, specs, metric, baseline=None):
    jobs = require_jobs(context)
    check_metric(metric)

    if len(specs) > context.max_study_scenarios:
        raise HTTPException(
            status_code=400,
            detail=f"A study can have at most {context.max_study_scenarios} scenarios",
        )

    if baseline is not None and baseline >= len(specs):
        raise HTTPException(status_code=400, detail="baseline is not one of the scenarios")

    from backend.db.scenario_repository import ScenarioRepository

    with context.database.transaction() as session:
        study = ScenarioRepository(session).create_study(
            name, specs, metric, max(1, jobs.workers), context.graph_sha256,
            ENGINE_VERSION, baseline_position=baseline,
        )
        study_id = study.id

    jobs.submit_study(study_id)

    return {
        "study_id": str(study_id),
        "status": "queued",
        "scenario_count": len(specs),
        "workers": jobs.workers,
        "links": {
            "study": f"/scenarios/studies/{study_id}",
            "cancel": f"/scenarios/studies/{study_id}/cancel",
        },
    }


def scenario_row(scenario):
    from backend.db.scenario_repository import summary_from_rows

    light = summary_from_rows(scenario)
    return {
        "scenario_id": str(scenario.id),
        "position": scenario.position,
        "name": scenario.name,
        "is_baseline": scenario.is_baseline,
        "seed": scenario.seed,
        "status": scenario.status.value,
        "error": scenario.error,
        "metrics": None if light is None else _metrics(light),
        "wall_seconds": None if light is None else light["execution"]["wall_seconds"],
    }


def _metrics(result):
    from backend.scenarios.compare import metric_values

    return metric_values(result)


def _time(value):
    return value.isoformat() if value is not None else None


def study_json(study, scenarios):
    done = study.completed_count + study.failed_count
    return {
        "study_id": str(study.id),
        "name": study.name,
        "status": study.status.value,
        "metric": study.metric,
        "scenario_count": study.scenario_count,
        "completed": study.completed_count,
        "failed": study.failed_count,
        "progress_percent": 100 * done / study.scenario_count,
        "workers": study.workers,
        "engine_version": study.engine_version,
        "created_at": _time(study.created_at),
        "started_at": _time(study.started_at),
        "finished_at": _time(study.finished_at),
        "wall_seconds": study.wall_seconds,
        "scenarios": [scenario_row(s) for s in scenarios],
    }


def full_item(repo, scenario):
    from backend.db.scenario_repository import result_from_rows

    loaded = repo.get_scenario(scenario.id, full=True)
    return {
        "id": str(scenario.id),
        "name": scenario.name,
        "spec": scenario.spec,
        "result": result_from_rows(loaded),
    }


def ranking_for(repo, scenarios, metric):
    """Ranking of the completed scenarios (summaries only) and an
    explanation of the top two (loading just those two in full)."""

    from backend.db.scenario_repository import summary_from_rows

    done = [s for s in scenarios if s.result is not None]
    if not done:
        return None, []

    light = [
        {"id": str(s.id), "name": s.name, "spec": s.spec, "result": summary_from_rows(s)}
        for s in done
    ]
    ranked = rank(light, metric)

    explanation = []
    if len(done) >= 2:
        by_id = {str(s.id): s for s in done}
        top = [full_item(repo, by_id[row["id"]]) for row in ranked[:2]]
        explanation = explain(top, metric)

    return ranked, explanation


# ------------------------------------------------------------
# Reference
# ------------------------------------------------------------

@router.get("/metrics")
def list_metrics():
    """What each comparison metric means and which direction is better."""

    return {"default": DEFAULT_METRIC, "metrics": [{"key": k, **v} for k, v in METRICS.items()]}


@router.get("/presets")
def presets():
    """Starting points for the scenario builder (edit before running)."""

    centre = {"latitude": CITY_CENTRE[0], "longitude": CITY_CENTRE[1]}

    return {"presets": [
        {
            "key": "baseline",
            "label": "Baseline traffic",
            "description": "Background trips across Norwich, nothing disrupted.",
            "spec": {"name": "Baseline", "seed": 1, "demand": {"vehicles": 500, "spread_seconds": 300}},
        },
        {
            "key": "closure",
            "label": "Road closure",
            "description": "The baseline with roads closed. Pick the roads on the map.",
            "spec": {"name": "Road closure", "seed": 1, "demand": {"vehicles": 500, "spread_seconds": 300},
                     "closures": []},
        },
        {
            "key": "accident",
            "label": "Accident",
            "description": "A severe accident two minutes in, for 15 minutes. Pick the road on the map.",
            "spec": {"name": "Accident", "seed": 1, "demand": {"vehicles": 500, "spread_seconds": 300},
                     "accidents": []},
            "accident_defaults": {"severity": "severe", "start_at": 120, "duration_seconds": 900},
        },
        {
            "key": "surge",
            "label": "Rush-hour surge",
            "description": "120 extra trips a minute into the city centre for 15 minutes.",
            "spec": {"name": "City-centre surge", "seed": 1, "demand": {"vehicles": 500, "spread_seconds": 300},
                     "surges": [{**centre, "radius_metres": 1000, "direction": "inbound",
                                 "vehicles_per_minute": 120, "start_at": 0, "duration_seconds": 900}]},
        },
        {
            "key": "stations",
            "label": "Emergency stations",
            "description": "One central station answering 20 emergencies; add or move stations to compare.",
            "spec": {"name": "One central station", "seed": 1, "demand": {"vehicles": 300, "spread_seconds": 300},
                     "stations": [{**centre, "ambulances": 2}],
                     "emergencies": {"count": 20, "first_at": 60, "interval_seconds": 45,
                                     "target_seconds": 480}},
        },
    ]}


@router.get("/nearest-road")
def nearest_road(
    latitude: float,
    longitude: float,
    context: AppContext = Depends(get_context),
):
    """The road (two adjacent junctions) closest to a point, for picking
    closures and accidents on the map."""

    from backend.simulation.dispatch import nearest_junction

    graph = context.graph
    position = context.network.position
    near = nearest_junction(graph, latitude, longitude)

    # Flat coordinates around the point (metres), good enough to compare
    # distances to a few nearby roads
    scale = math.cos(math.radians(latitude))

    def flat(point):
        return ((point[1] - longitude) * 111_320 * scale, (point[0] - latitude) * 111_320)

    def distance_to(a, b):
        (ax, ay), (bx, by) = flat(position[a]), flat(position[b])
        dx, dy = bx - ax, by - ay
        length = dx * dx + dy * dy
        t = 0 if length == 0 else max(0, min(1, -(ax * dx + ay * dy) / length))
        x, y = ax + t * dx, ay + t * dy
        return math.hypot(x, y)

    # Roads either way (one-way streets have an edge in one direction)
    neighbours = {b for (a, b) in context.network.length if a == near}
    neighbours |= {a for (a, b) in context.network.length if b == near}
    neighbours.discard(near)

    if not neighbours:
        raise HTTPException(status_code=404, detail="No road near that point")

    other = min(sorted(neighbours), key=lambda node: distance_to(near, node))
    start, destination = sorted((near, other))

    return {
        "start": start,
        "destination": destination,
        "start_point": {"latitude": position[start][0], "longitude": position[start][1]},
        "end_point": {"latitude": position[destination][0], "longitude": position[destination][1]},
        "distance_metres": distance_to(start, destination),
    }


# ------------------------------------------------------------
# Studies
# ------------------------------------------------------------

@router.post("/studies", status_code=202)
def create_study_endpoint(
    request: StudyRequest,
    context: AppContext = Depends(get_context),
):
    """Run a list of scenarios in the background and compare them."""

    require_jobs(context)
    specs = parse_specs(context, request.scenarios)
    return create_study(context, request.name, specs, request.metric, request.baseline)


@router.post("/studies/sweep", status_code=202)
def create_sweep(
    request: SweepRequest,
    context: AppContext = Depends(get_context),
):
    """A study generated from one scenario: several seeds, each road
    closed in turn, random closures, or station layouts."""

    require_jobs(context)
    base = parse_specs(context, [request.base])[0]

    if request.kind == "seeds":
        specs, baseline = sweep.seeds(base, request.count, request.first_seed), None

    elif request.kind == "each_closure":
        if not request.roads:
            raise HTTPException(status_code=400, detail="each_closure needs roads")
        specs, baseline = sweep.each_closure(base, [tuple(r) for r in request.roads]), 0

    elif request.kind == "random_closures":
        zone = (
            Zone(request.zone.latitude, request.zone.longitude, request.zone.radius_metres)
            if request.zone else None
        )
        roads = sweep.candidate_roads(context.network, zone)
        try:
            specs = sweep.random_closures(
                base, roads, request.count, request.closures_per_scenario, request.sweep_seed
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error))
        baseline = 0

    else:
        if not request.layouts:
            raise HTTPException(status_code=400, detail="station_layouts needs layouts")
        specs = sweep.station_layouts(
            base, [(layout.name, layout.stations) for layout in request.layouts]
        )
        baseline = None

    # Generated specs are checked like submitted ones (roads exist, ...)
    specs = parse_specs(context, [spec.model_dump(mode="json") for spec in specs])
    return create_study(context, request.name, specs, request.metric, baseline)


@router.get("/studies")
def list_studies(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    context: AppContext = Depends(get_context),
):
    require_jobs(context)
    from backend.db.scenario_repository import ScenarioRepository

    with context.database.transaction() as session:
        rows, total = ScenarioRepository(session).list_studies(limit, offset)
        studies = [
            {k: v for k, v in study_json(study, []).items() if k != "scenarios"}
            for study in rows
        ]

    return {"total": total, "limit": limit, "offset": offset, "studies": studies}


@router.get("/studies/{study_id}")
def get_study(
    study_id: str,
    metric: Optional[str] = None,
    context: AppContext = Depends(get_context),
):
    """Progress, every scenario's status and headline metrics, and once
    results are in, the ranking and why the leader leads."""

    require_jobs(context)
    from backend.db.scenario_repository import ScenarioRepository

    with context.database.transaction() as session:
        repo = ScenarioRepository(session)
        study = repo.get_study(parse_id(study_id, "Study"))
        if study is None:
            raise HTTPException(status_code=404, detail="Study not found")

        metric = metric or study.metric
        check_metric(metric)

        scenarios = repo.scenarios(study.id, with_results=True)
        body = study_json(study, scenarios)
        ranking, explanation = ranking_for(repo, scenarios, metric)

    body["ranking_metric"] = {"key": metric, **METRICS[metric]}
    body["ranking"] = ranking
    body["explanation"] = explanation
    return body


@router.post("/studies/{study_id}/cancel")
def cancel_study(study_id: str, context: AppContext = Depends(get_context)):
    jobs = require_jobs(context)
    if not jobs.cancel_study(parse_id(study_id, "Study")):
        raise HTTPException(status_code=409, detail="The study has already finished or does not exist")
    return {"status": "study cancelled", "study_id": study_id}


@router.delete("/studies/{study_id}")
def delete_study(study_id: str, context: AppContext = Depends(get_context)):
    jobs = require_jobs(context)
    from backend.db.scenario_repository import ScenarioRepository

    study_uuid = parse_id(study_id, "Study")
    jobs.cancel_study(study_uuid)

    with context.database.transaction() as session:
        if not ScenarioRepository(session).delete_study(study_uuid):
            raise HTTPException(status_code=404, detail="Study not found")

    return {"status": "study deleted", "study_id": study_id}


# ------------------------------------------------------------
# Scenarios
# ------------------------------------------------------------

@router.get("/compare")
def compare_scenarios(
    ids: str,
    metric: str = DEFAULT_METRIC,
    baseline: Optional[str] = None,
    context: AppContext = Depends(get_context),
):
    """Compare up to 20 completed scenarios (from any studies): ranking,
    explanation, and each one against the baseline, trip by trip where
    they have the same trips. ids and baseline are scenario ids."""

    require_jobs(context)
    check_metric(metric)
    from backend.db.scenario_repository import ScenarioRepository

    wanted = [parse_id(value, "Scenario") for value in ids.split(",") if value.strip()]
    if not 2 <= len(wanted) <= 20:
        raise HTTPException(status_code=400, detail="Give between 2 and 20 scenario ids")

    with context.database.transaction() as session:
        repo = ScenarioRepository(session)
        items = []
        for scenario_id in wanted:
            scenario = repo.get_scenario(scenario_id)
            if scenario is None:
                raise HTTPException(status_code=404, detail=f"Scenario {scenario_id} not found")
            if scenario.result is None:
                raise HTTPException(status_code=409, detail=f"Scenario {scenario.name} has no result yet")
            items.append(full_item(repo, scenario))

    # Names from different studies can repeat
    for item, spec in zip(items, unique_names([ScenarioSpec.model_validate({**i["spec"]}) for i in items])):
        item["name"] = spec.name

    baseline_name = None
    if baseline is not None:
        match = [i for i in items if i["id"] == str(parse_id(baseline, "Scenario"))]
        if not match:
            raise HTTPException(status_code=400, detail="baseline must be one of ids")
        baseline_name = match[0]["name"]

    outcome = compare(items, metric, baseline_name)
    outcome["scenarios"] = [{"scenario_id": i["id"], "name": i["name"]} for i in items]
    return outcome


@router.get("/{scenario_id}")
def get_scenario(scenario_id: str, context: AppContext = Depends(get_context)):
    """One scenario: its spec, status, and full result (roads, areas, time
    series, emergencies, execution), plus, if its study has a completed
    baseline, the difference from it trip by trip and road by road."""

    require_jobs(context)
    from backend.db.scenario_repository import ScenarioRepository, result_from_rows

    with context.database.transaction() as session:
        repo = ScenarioRepository(session)
        scenario = repo.get_scenario(parse_id(scenario_id, "Scenario"), full=True)
        if scenario is None:
            raise HTTPException(status_code=404, detail="Scenario not found")

        result = result_from_rows(scenario)
        versus = None

        if result is not None and not scenario.is_baseline:
            base = next(
                (s for s in repo.scenarios(scenario.study_id) if s.is_baseline), None
            )
            if base is not None and base.result is not None:
                base_result = result_from_rows(repo.get_scenario(base.id, full=True))
                versus = {
                    "baseline_id": str(base.id),
                    "baseline_name": base.name,
                    "paired": paired(base_result, result),
                    "roads": road_changes(base_result, result),
                }

        body = {
            "scenario_id": str(scenario.id),
            "study_id": str(scenario.study_id),
            "position": scenario.position,
            "name": scenario.name,
            "is_baseline": scenario.is_baseline,
            "status": scenario.status.value,
            "error": scenario.error,
            "spec": scenario.spec,
            "spec_fingerprint": scenario.spec_fingerprint,
            "trip_signature": scenario.trip_signature,
            "started_at": _time(scenario.started_at),
            "finished_at": _time(scenario.finished_at),
        }

    if result is None:
        return {**body, "result": None, "vs_baseline": None}

    trips = result.pop("trips")
    result["trip_counts"] = {
        status: trips["status"].count(status)
        for status in ("finished", "unfinished", "unroutable")
    }
    result["metrics"] = _metrics(result)
    return {**body, "result": result, "vs_baseline": versus}
