"""The AI City Analyst, natural-language scenarios and the analysis panel
(backend/intelligence/). Questions and scenario requests go through the
configured AI provider when there is one, and through built-in rules when
there is not; every figure comes from the backend's calculations."""

from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.api.context import AppContext
from backend.api.routes.common import get_context


router = APIRouter(tags=["Intelligence"])


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    simulation_id: Optional[str] = Field(None, max_length=64)
    history: List[Turn] = Field(default_factory=list, max_length=12)


@router.get("/intelligence/status")
def intelligence_status():
    """Which AI provider and model answer (if any), and what the analyst
    can calculate."""

    from backend.intelligence.providers import provider_status
    from backend.intelligence.tools import TOOLS

    return {
        **provider_status(),
        "fallback": "built-in rules over the same calculations",
        "capabilities": [{"name": t["name"], "description": t["description"]} for t in TOOLS],
    }


@router.post("/intelligence/ask")
def ask(body: Question, context: AppContext = Depends(get_context)):
    """Answer a question about Norwich or a live simulation. The answer is
    written by the AI model from the backend's calculations (mode "ai"), or
    by the built-in rules (mode "rules"); `sources` lists every calculation
    used, with its inputs and results."""

    from backend.intelligence.analyst import answer

    try:
        return answer(body.question, context, body.simulation_id, [t.model_dump() for t in body.history])
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None


@router.get("/intelligence/insights")
def insights(
    simulation_id: Optional[str] = None,
    context: AppContext = Depends(get_context),
):
    """The analysis panel: coverage gaps and the best extra station for fire
    and police (4-minute target), the roads trips depend on, and, for a
    live run, its bottlenecks, congestion anomalies and findings. All
    calculated; each part says how."""

    from backend.intelligence import analysis

    graph, network = context.graph, context.network
    out = {
        "coverage": {s: analysis.coverage_gaps(graph, network, s, 4, top=3) for s in ("fire", "police")},
        "stations": {s: analysis.recommend_station(graph, network, s, 4, top=2) for s in ("fire", "police")},
        "critical_roads": analysis.critical_roads(graph, network, top=6),
        "run": None,
    }
    run = context.registry.get(simulation_id) if simulation_id else context.registry.default()
    if run is not None:
        with run.lock:
            out["run"] = {
                "simulation_id": run.id,
                "report": analysis.run_report(run, context),
                "bottlenecks": analysis.bottlenecks(run, context, top=6),
            }
    return analysis.plain(out)


class ScenarioRequest(BaseModel):
    text: str = Field(min_length=1, max_length=600)


class ScenarioRun(BaseModel):
    proposal: dict
    confirm: bool = False


@router.post("/intelligence/scenario/plan")
def plan_scenario(body: ScenarioRequest, context: AppContext = Depends(get_context)):
    """Turn a description into a PROPOSED scenario (nothing runs): supported
    actions only, in real Norwich places and roads, with a line describing
    each. Read by the AI model when one is configured, else by built-in
    rules. 422 if nothing in the request can be simulated."""

    from backend.intelligence.scenarios import ScenarioError, plan

    try:
        return plan(body.text, context)
    except ScenarioError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None


@router.post("/intelligence/scenario/run", status_code=201)
def run_scenario(body: ScenarioRun, context: AppContext = Depends(get_context)):
    """Run a proposal the user has confirmed (confirm must be true): a new
    live run of the simulation engine with its actions applied, and the
    closures' measured effect on trip times. The proposal is validated
    again here; 422 if anything in it is not supported."""

    from backend.intelligence.scenarios import ScenarioError, run

    if not body.confirm:
        raise HTTPException(status_code=400, detail="Confirm the scenario before running it (confirm: true)")
    try:
        return run(body.proposal, context)
    except ScenarioError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
