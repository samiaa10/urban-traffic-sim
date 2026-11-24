"""The local AI analyst: a free model in Ollama, connected to the
simulation's own calculations, that cannot make figures up.

A question goes through five steps:

  1. PLAN     The model reads the question (and the conversation so far)
              and fills in a JSON form, which Ollama holds to a schema: which
              of the read-only calculations to run (tools.py), with which
              arguments, and whether the user wants something simulated.
              Small models follow a schema far more reliably than they make
              free-form tool calls.
  2. RUN      The backend validates each request against the tool's schema
              and runs it. The model never runs anything itself.
  3. FACTS    Each result is written up by the same templates the built-in
              rules use: these are the CALCULATED FACTS, exact by
              construction.
  4. EXPLAIN  The model explains the facts (why, trade-offs, what to do
              next) from the facts alone.
  5. CHECK    Every figure in the explanation must come from a calculation;
              if not, the model gets one retry, then the explanation is
              withheld and only the facts are shown.

Anything going wrong (Ollama down, too slow, an unusable plan) falls back
to the built-in rules. A request to simulate something becomes a proposal
(scenarios.py) that the user must confirm before anything runs.
"""

import json
import logging
import re
from functools import lru_cache

from backend.intelligence import analyst, scenarios
from backend.intelligence.providers import ProviderError
from backend.intelligence.tools import BY_NAME, ToolError, Toolbox, validate
from backend.optimiser.planning import CENTRAL_BRIDGES


logger = logging.getLogger(__name__)

MAX_CALCULATIONS = 3
HISTORY_TURNS = 6
PLAN_TOKENS = 400
EXPLAIN_TOKENS = 180

TOOL_NAMES = [name for name in BY_NAME if name != "city_overview"] + ["city_overview"]

TOOL_GUIDE = """\
coverage_gaps: where emergency response (fire, police or ambulance) is slowest, how long a service takes to reach places, how many residents are beyond a target time; why (nearest station by road)
recommend_station: the one extra station site that would help a service most, and the trade-off between sites
station_plan: the best layout of N stations for a service against the existing ones; how to improve a service's response times
critical_roads: the roads most trips depend on and what closing each costs (structural bottlenecks, which closures would disrupt most)
closure_impact: what closing roads or bridges the user names does to trip times (central_bridges true for the central bridges)
flood_vulnerability: which riverside places would disrupt the city most if flooded
live_bottlenecks: the most delayed roads in the simulation running now, and congestion anomalies
run_report: why the running simulation performs as it does (delay, reroutes, disruption, emergency response)
scenario_results: the ranking of saved scenario studies (which scenario did best)
city_overview: what the model of Norwich contains"""

EXAMPLES = """\
Q: Which areas have the worst ambulance coverage?
A: {"intent":"answer","calculations":[{"tool":"coverage_gaps","service":"ambulance","target_minutes":0,"stations":3,"roads":[],"central_bridges":false,"disruption":"none"}],"scenario":"","reply":""}
Q: How could we improve fire response times, and what would one new station give?
A: {"intent":"answer","calculations":[{"tool":"station_plan","service":"fire","target_minutes":4,"stations":3,"roads":[],"central_bridges":false,"disruption":"none"},{"tool":"recommend_station","service":"fire","target_minutes":4,"stations":3,"roads":[],"central_bridges":false,"disruption":"none"}],"scenario":"","reply":""}
Q: What would happen if Fye Bridge Street and Duke Street closed?
A: {"intent":"answer","calculations":[{"tool":"closure_impact","service":"fire","target_minutes":0,"stations":3,"roads":["Fye Bridge Street","Duke Street"],"central_bridges":false,"disruption":"none"}],"scenario":"","reply":""}
Q: Which roads would hurt the most to lose?
A: {"intent":"answer","calculations":[{"tool":"critical_roads","service":"fire","target_minutes":0,"stations":3,"roads":[],"central_bridges":false,"disruption":"none"}],"scenario":"","reply":""}
Q: How long would a police car take to reach Lakenham?
A: {"intent":"answer","calculations":[{"tool":"coverage_gaps","service":"police","target_minutes":4,"stations":3,"roads":[],"central_bridges":false,"disruption":"none"}],"scenario":"","reply":""}
Q: Simulate a flood in the city centre during rush hour.
A: {"intent":"simulate","calculations":[{"tool":"flood_vulnerability","service":"fire","target_minutes":0,"stations":3,"roads":[],"central_bridges":false,"disruption":"none"}],"scenario":"A flood in the city centre during an inbound rush hour","reply":""}
Q: Why is traffic so slow in this simulation?
A: {"intent":"answer","calculations":[{"tool":"run_report","service":"fire","target_minutes":0,"stations":3,"roads":[],"central_bridges":false,"disruption":"none"},{"tool":"live_bottlenecks","service":"fire","target_minutes":0,"stations":3,"roads":[],"central_bridges":false,"disruption":"none"}],"scenario":"","reply":""}
Q: (after a question about fire cover) And for police?
A: {"intent":"answer","calculations":[{"tool":"coverage_gaps","service":"police","target_minutes":4,"stations":3,"roads":[],"central_bridges":false,"disruption":"none"}],"scenario":"","reply":""}"""


@lru_cache(maxsize=2)
def bridge_roads(graph_id, graph):
    """Bridge roads on the map: the central bridges and any road named Bridge."""
    names = set(CENTRAL_BRIDGES)
    for info in getattr(graph, "road_info", {}).values():
        name = getattr(info, "name", None)
        if name and "bridge" in name.lower():
            names.add(name)
    return sorted(names)


def plan_schema():
    calculation = {
        "type": "object",
        "additionalProperties": False,
        "required": ["tool", "service", "target_minutes", "stations", "roads", "central_bridges", "disruption"],
        "properties": {
            "tool": {"type": "string", "enum": TOOL_NAMES},
            "service": {"type": "string", "enum": ["fire", "police", "ambulance"]},
            "target_minutes": {"type": "integer", "minimum": 0, "maximum": 30},
            "stations": {"type": "integer", "minimum": 1, "maximum": 8},
            "roads": {"type": "array", "maxItems": 4, "items": {"type": "string", "maxLength": 60}},
            "central_bridges": {"type": "boolean"},
            "disruption": {"type": "string", "enum": ["none", "flood", "bridges"]},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["intent", "calculations", "scenario", "reply"],
        "properties": {
            "intent": {"type": "string", "enum": ["answer", "simulate", "chat"]},
            "calculations": {"type": "array", "maxItems": MAX_CALCULATIONS, "items": calculation},
            "scenario": {"type": "string", "maxLength": 300},
            "reply": {"type": "string", "maxLength": 400},
        },
    }


def plan_system(graph):
    return f"""You plan answers for the City Analyst of the Norwich Urban Traffic Simulator, a simulation of Norwich's real roads \
(OpenStreetMap), residents (Census 2021) and emergency services (fire, police, ambulance).

Choose the calculations that answer the user's latest question, from this list:
{TOOL_GUIDE}

Rules:
- Pick 1 to {MAX_CALCULATIONS} calculations. Combine them when the question has several parts.
- Use the conversation to resolve follow-ups ("and for police?", "what about there?").
- target_minutes: the minutes the user gives, or 4 for fire and police, or 0 for the service's own target.
- roads: only for closure_impact, with road names as the user wrote them. Bridge roads on the map: \
{", ".join(bridge_roads(id(graph), graph))}. For "the central bridges" or "major bridges" with no names, \
set central_bridges true.
- intent "simulate" when the user wants a scenario run (simulate, run, set up, create a scenario); describe it \
in "scenario" and still pick calculations that measure it now. intent "chat" only for greetings or questions \
that are not about Norwich's roads, traffic or emergency services, with a one-sentence "reply" that has no numbers.
- Unused fields keep their defaults (service "fire", target_minutes 0, stations 3, roads [], central_bridges false, \
disruption "none").

Examples:
{EXAMPLES}"""


EXPLAIN_SYSTEM = """You are the City Analyst of the Norwich Urban Traffic Simulator. You explain calculated results about Norwich's roads, \
traffic and emergency services to a planner.

Write a short explanation (2 to 4 sentences, plain prose, British English, no headings) of the CALCULATED FACTS \
below, answering the user's question:
- Say what the facts mean and, where the facts show it, why (for example an area's distance by road from its \
nearest station, or how many trips use a road).
- Point out trade-offs between options, and what the user could look at or simulate next.
- Use ONLY numbers that appear in the facts. Never introduce a new number, percentage, time, place or road.
- Keep each number with what the facts attach it to (the same road, place, station, service or option); \
never move a figure from one to another, and never add figures together.
- If the facts cannot answer part of the question, say so plainly.
- The facts come from a simulation model of Norwich, not from real-world records: never present a figure as a \
real or official statistic, or as belonging to a year.
- If the question is not about Norwich's roads, traffic or emergency services, say in one sentence that the \
analyst cannot help with that, and nothing else.
- Do not repeat every fact: interpret them."""


def _calculation_arguments(item):
    """A planned calculation's arguments for its tool (validated later)."""
    tool = item.get("tool")
    service = item.get("service") or "fire"
    minutes = item.get("target_minutes") or None
    disruption = item.get("disruption") or "none"
    if tool in ("coverage_gaps", "recommend_station"):
        # The same 4-minute target as the built-in rules and the City
        # intelligence panel, unless the user gave one
        if minutes is None and service in ("fire", "police"):
            minutes = 4
        return {"service": service, "target_minutes": minutes, "disruption": disruption}
    if tool == "station_plan":
        return {"service": service, "stations": item.get("stations") or 3, "target_minutes": minutes}
    if tool == "closure_impact":
        roads = [r.strip() for r in item.get("roads") or [] if isinstance(r, str) and r.strip()][:4]
        return {"roads": roads, "central_bridges": bool(item.get("central_bridges")) or not roads}
    if tool in ("live_bottlenecks", "run_report"):
        return {"simulation_id": None}
    if tool == "scenario_results":
        return {"study_id": None}
    return {}


def _history(history):
    turns = []
    for turn in (history or [])[-HISTORY_TURNS:]:
        if turn.get("role") in ("user", "assistant") and isinstance(turn.get("content"), str):
            turns.append({"role": turn["role"], "content": turn["content"][:1500]})
    while turns and turns[0]["role"] != "user":
        turns.pop(0)
    return turns


def _fact(tool, arguments, result):
    return {"tool": tool, "text": analyst.rule_answer(tool, result)}


def answer(question, context, provider, simulation_id=None, history=None):
    """The analyst's answer from the local model. Raises ProviderError when
    the model cannot plan (the caller then uses the built-in rules)."""

    final = None
    for event in steps(question, context, provider, simulation_id, history):
        if event["stage"] == "done":
            final = event["answer"]
    return final


def steps(question, context, provider, simulation_id=None, history=None):
    """The answer in stages, for a streaming reply: {"stage": "planning"},
    {"stage": "calculated", "answer": the facts so far}, {"stage":
    "proposing"}, {"stage": "explaining"}, then {"stage": "done", "answer"}.
    Raises ProviderError only before anything is calculated (planning)."""

    yield {"stage": "planning"}
    toolbox = Toolbox(context, simulation_id)
    turns = _history(history)
    plan = provider.structured(plan_system(context.graph), turns + [{"role": "user", "content": question}],
                               plan_schema(), max_tokens=PLAN_TOKENS)

    intent = plan.get("intent") if plan.get("intent") in ("answer", "simulate", "chat") else "answer"
    planned = [item for item in plan.get("calculations") or [] if isinstance(item, dict)][:MAX_CALCULATIONS]
    if intent == "chat" and not planned:
        reply = str(plan.get("reply") or "").strip()
        if reply and not analyst.ungrounded_figures(reply, [], question):
            yield {"stage": "done", "answer": {
                "answer": reply, "mode": "ai", "model": provider.model, "sources": [], "facts": [],
                "explanation": reply, "explanation_note": None, "proposal": None, "notice": None,
                "plan": {"intent": intent, "calculations": []}}}
            return
        planned = []

    sources, facts, refused, seen = [], [], [], set()
    named_by_user = False
    for item in planned:
        tool = item.get("tool")
        if tool not in BY_NAME:
            continue
        arguments = _calculation_arguments(item)
        key = (tool, json.dumps(arguments, sort_keys=True))
        if key in seen:
            continue
        seen.add(key)
        try:
            validate(arguments, BY_NAME[tool]["input_schema"])
            result = toolbox.run(tool, arguments)
        except ToolError as error:
            refused.append(f"{analyst.TOOL_TITLES.get(tool, tool)}: {error}")
            # A road the user named that is not on the map: say so, rather
            # than answer about other roads
            named_by_user |= any(r.lower() in question.lower() for r in arguments.get("roads") or [])
            continue
        sources.append({"tool": tool, "input": arguments, "provenance": "calculated", "result": result})
        facts.append(_fact(tool, arguments, result))

    # Nothing usable planned: the rules' choice of calculation, so there is
    # always something calculated to explain
    if not sources and intent != "simulate" and not named_by_user:
        has_run = bool(simulation_id and context.registry.get(simulation_id)) or context.registry.default() is not None
        tool, arguments = analyst.choose_tool(question, context.graph, has_run)
        try:
            result = toolbox.run(tool, arguments)
            sources.append({"tool": tool, "input": arguments, "provenance": "calculated", "result": result})
            facts.append(_fact(tool, arguments, result))
        except ToolError as error:
            refused.append(f"{analyst.TOOL_TITLES.get(tool, tool)}: {error}")

    refused_note = ("Not calculated: " + "; ".join(refused)) if refused and facts else None
    out = {
        "answer": "\n\n".join(f["text"] for f in facts) or "Nothing could be calculated for that question.",
        "mode": "ai",
        "model": provider.model,
        "sources": sources,
        "facts": facts,
        "explanation": None,
        "explanation_note": None,
        "proposal": None,
        "notice": refused_note,
        "plan": {"intent": intent, "calculations": [{"tool": s["tool"], "input": s["input"]} for s in sources]},
    }
    if facts:
        yield {"stage": "calculated", "answer": {**out, "pending": True}}

    proposal_note = None
    if intent == "simulate":
        yield {"stage": "proposing"}
        request = str(plan.get("scenario") or "").strip() or question
        try:
            out["proposal"] = scenarios.plan(request, context, provider=provider)
        except scenarios.ScenarioError as error:
            proposal_note = f"No scenario could be proposed: {error}"

    if facts:
        yield {"stage": "explaining"}
        out["explanation"], out["explanation_note"] = _explain(question, turns, facts, sources, provider)
        if out["explanation"]:
            out["answer"] += "\n\n" + out["explanation"]
    elif refused:
        out["explanation_note"] = "Nothing could be calculated: " + "; ".join(refused)
        out["answer"] = out["explanation_note"]
    if not facts and out["proposal"]:
        out["answer"] = "A scenario is proposed below. Nothing has run yet: check it, then run it."
    elif not facts and (not refused or intent == "simulate"):
        out["answer"] = out["explanation_note"] = (
            "Nothing in that request can be calculated or simulated. The analyst only runs the simulation's "
            "read-only calculations, and proposes scenarios for you to confirm; it cannot change or delete anything.")

    out["notice"] = " ".join(n for n in (proposal_note, refused_note) if n) or None
    yield {"stage": "done", "answer": out}


def _explain(question, turns, facts, sources, provider):
    """(explanation, note): the model's explanation if every figure in it
    comes from the calculations, else None and why.

    A sentence with a figure no calculation produced is removed (and the
    note says so); only when too little is left is the model asked once to
    rewrite it, which costs a second generation."""
    facts_text = "\n\n".join(f["text"] for f in facts)
    allowed = sources + [{"tool": "facts", "input": {}, "result": {"text": facts_text}}]
    # Earlier questions give context ("and for fire?"); earlier answers are
    # left out, so only this answer's calculations supply figures
    earlier = [t["content"] for t in turns if t["role"] == "user"][-2:]
    context = "".join(f"Earlier question: {q}\n" for q in earlier)
    prompt = f"{context}Question: {question}\n\nCALCULATED FACTS:\n{facts_text}"
    messages = [{"role": "user", "content": prompt}]
    try:
        draft = provider.generate(EXPLAIN_SYSTEM, messages, max_tokens=EXPLAIN_TOKENS, temperature=0)
        bad = _unsupported(draft, allowed, question, facts_text)
        if bad:
            kept, dropped = _grounded_sentences(draft, allowed, question, facts_text)
            if len(kept) >= 2:
                logger.info("Local AI explanation: %d sentence(s) with %s removed", dropped, bad)
                return " ".join(kept), (f"{dropped} sentence{'s' if dropped > 1 else ''} of the explanation used "
                                        f"figures or roads no calculation produced ({', '.join(bad[:4])}) and "
                                        f"{'were' if dropped > 1 else 'was'} removed.")
            retry = messages + [
                {"role": "assistant", "content": draft},
                {"role": "user", "content": f"Your explanation used figures or roads that are not in the facts ({', '.join(bad[:6])}). "
                                            "Rewrite it using only numbers and roads that appear in the facts."},
            ]
            draft = provider.generate(EXPLAIN_SYSTEM, retry, max_tokens=EXPLAIN_TOKENS, temperature=0)
            bad = _unsupported(draft, allowed, question, facts_text)
        if bad:
            logger.info("Local AI explanation withheld: figures %s", bad)
            return None, ("The local model's explanation used figures or roads no calculation produced "
                          f"({', '.join(bad[:4])}), so only the calculated facts are shown.")
        if not draft:
            return None, "The local model gave no explanation."
        return draft, None
    except ProviderError as error:
        return None, f"No explanation: {error.reason}."


SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


YEAR = re.compile(r"\b(?:19|20)\d{2}\b")


ROAD = re.compile(r"\b[AB]\d{1,4}\b|\b(?:[A-Z][\w']+ )+(?:Road|Street|Lane|Avenue|Way|Hill|Bridge|Drive|Close|Roundabout)\b")


def _known_road(match, known):
    """Whether a road name (or the end of it: "Closing Dereham Road" is
    Dereham Road) is in the facts or the question."""
    words = match.split()
    return any(" ".join(words[i:]).lower() in known for i in range(len(words) - 1 if len(words) > 1 else 1))


def _unsupported(text, allowed, question, facts_text):
    """What in the text no calculation supports: figures no calculation
    produced, years the facts do not mention (a year turns a simulated
    figure into a claim about the real world, for example "the response
    time in 2024 was 3.8 min"), and roads that are in neither the facts
    nor the question (small models name real-sounding roads)."""
    known = (facts_text + " " + question).lower()
    years = [y for y in YEAR.findall(text) if y not in facts_text]
    roads = [r for r in ROAD.findall(text) if not _known_road(r, known)]
    return list(dict.fromkeys(analyst.ungrounded_figures(text, allowed, question) + years + roads))


def _grounded_sentences(text, allowed, question, facts_text):
    """(the sentences with nothing unsupported in them, how many were
    removed)."""
    sentences = [s.strip() for s in SENTENCE.split(text.replace("\n", " ")) if s.strip()]
    kept = [s for s in sentences if not _unsupported(s, allowed, question, facts_text)]
    return kept, len(sentences) - len(kept)
