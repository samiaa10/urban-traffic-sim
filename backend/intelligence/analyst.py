"""The AI City Analyst: answers questions about Norwich and the simulation
from the backend's own calculations.

With an AI provider (providers.py) the model reads the question, asks for
the calculations it needs (tools.py: read-only, validated) and writes the
answer from their results. Without one, or if the provider fails, built-in
rules pick the calculation from the question's words and write the answer
from a template. Either way every number comes from a calculation, and the
answer says which parts are calculated and which are the model's wording.
"""

import json
import logging
import re
from functools import lru_cache

from backend.intelligence.providers import ProviderError, get_provider
from backend.intelligence.tools import TOOLS, ToolError, Toolbox


logger = logging.getLogger(__name__)

MAX_ROUNDS = 5
MAX_QUESTION = 1000
MAX_RESULT_CHARS = 24000

SYSTEM = """You are the City Analyst inside NORWICH//SIM, a simulation of Norwich's real road network \
(OpenStreetMap), its residents (Census 2021) and its emergency services.

Answer the user's question using the tools. They are the simulation's own calculations: call the ones \
the question needs, then answer from their results.

Rules:
- Every number you give must come from a tool result in this conversation. Never invent figures, places, \
roads or stations.
- Say what kind of statement each part is: results the tools calculated, estimates or interpretation of \
your own (label them "my interpretation"), and anything the model cannot tell (say so plainly).
- The model's limits: response times are road distance over a fixed service speed (no traffic) unless a \
tool says it comes from a live simulation; trips use free-flow speeds; nothing here predicts real \
emergency response times or real traffic.
- If a tool returns an error, say what could not be calculated and why.
- Be concise: a short answer first, then the key figures as a brief list. British English. No headings."""

TOOL_TITLES = {
    "city_overview": "City model figures",
    "coverage_gaps": "Emergency coverage gaps",
    "recommend_station": "Best extra station",
    "station_plan": "Station layout plan",
    "critical_roads": "Roads trips depend on",
    "closure_impact": "Road closure impact",
    "flood_vulnerability": "Flood vulnerability",
    "live_bottlenecks": "Live bottlenecks",
    "run_report": "Simulation report",
    "scenario_results": "Scenario study ranking",
}

SERVICE_WORDS = {
    "ambulance": ("ambulance", "medical", "paramedic"),
    "police": ("police", "crime"),
    "fire": ("fire", "fire engine", "firefighter"),
}


def _tool_definitions():
    return [{"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]} for t in TOOLS]


def _clip(result):
    text = json.dumps(result, ensure_ascii=False)
    return text if len(text) <= MAX_RESULT_CHARS else text[:MAX_RESULT_CHARS] + "…(truncated)"


def _service(question):
    lowered = question.lower()
    for service, words in SERVICE_WORDS.items():
        if any(w in lowered for w in words):
            return service
    return "fire"


def _minutes(question):
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:min|minute)", question.lower())
    return float(match.group(1)) if match and 1 <= float(match.group(1)) <= 30 else None


@lru_cache(maxsize=2)
def _road_names(graph_id, graph):
    names = set()
    for (a, b), info in getattr(graph, "road_info", {}).items():
        name = getattr(info, "name", None)
        if name and len(name) > 4:
            names.add(name)
    return sorted(names, key=len, reverse=True)


def roads_in(question, graph):
    """Road names on the map that the question mentions."""
    lowered = question.lower()
    found = []
    for name in _road_names(id(graph), graph):
        if name.lower() in lowered and not any(name.lower() in f.lower() for f in found):
            found.append(name)
    return found[:6]


def choose_tool(question, graph, has_run):
    """The built-in rules: which calculation a question asks for."""
    q = question.lower()
    if re.search(r"flood", q) and re.search(r"vulnerab|most at risk|where|which areas|worst", q):
        return "flood_vulnerability", {}
    if re.search(r"improve|better|faster|reduce", q) and re.search(r"response", q) and not re.search(r"new station|extra station", q):
        return "station_plan", {"service": _service(question) if re.search(r"fire|police", q) else "ambulance",
                                "stations": 3, "target_minutes": _minutes(question)}
    if re.search(r"\bscenario|\bstud(y|ies)|which .* (best|worst) result", q):
        return "scenario_results", {"study_id": None}
    if re.search(r"\bbridge|\bclos(e|ed|ing|ure)\b|what (would|happens) if", q):
        roads = roads_in(question, graph)
        if roads:
            return "closure_impact", {"roads": roads, "central_bridges": False}
        return "closure_impact", {"roads": [], "central_bridges": True}
    if re.search(r"new station|extra station|where should|which (emergency )?station location|improve (emergency )?coverage|add(ing)? a station", q):
        return "recommend_station", {"service": _service(question), "target_minutes": _minutes(question) or 4, "disruption": "none"}
    if re.search(r"coverage|\bcover(ed)?\b|response time|worst (areas|served)|underserved|slowest (areas|response)"
                 r"|(take|takes|long) .*\bto (reach|get to)\b", q):
        return "coverage_gaps", {"service": _service(question), "target_minutes": _minutes(question) or 4, "disruption": "none"}
    if re.search(r"why|poor(ly)?|explain|perform|went wrong|summar", q):
        return "run_report", {"simulation_id": None}
    if re.search(r"bottleneck|congest|jam|slow road|busiest|vulnerab|critical road|weak|depend on|most used|important road", q):
        structural = any(word in q for word in ("vulnerab", "critical", "depend on", "most used", "important"))
        return ("live_bottlenecks", {"simulation_id": None}) if has_run and not structural \
            else ("critical_roads", {})
    return "city_overview", {}


def _minutes_text(seconds):
    return "-" if seconds is None else f"{seconds / 60:.1f} min"


def _areas_by_place(areas, key="residents_beyond_target"):
    """Areas (hexagons) grouped by the place they are named after, in the
    order of their worst area."""
    groups = {}
    for area in areas:
        groups.setdefault(area["where"], []).append(area)
    if key == "residents":
        return list(groups.items())
    return sorted(groups.items(), key=lambda item: -sum(a[key] for a in item[1]))


def _range(values, text):
    low, high = text(min(values)), text(max(values))
    if low == high:
        return low
    for unit in (" min", " km"):
        if low.endswith(unit) and high.endswith(unit) and low.count(" ") == 1:
            return f"{low[:-len(unit)]} to {high}"
    return f"{low} to {high}"


def rule_answer(tool, result):
    """A plain answer written from a calculation's result."""
    if tool == "coverage_gaps":
        s = result["summary"]
        lines = [
            f"From the {result['layout']} ({', '.join(result['stations']) or 'none'}), "
            f"{s['population_within_target_percent']:.1f}% of residents are within {result['target_seconds'] / 60:.0f} min "
            f"by road ({round(s['population_beyond_target']):,} beyond it).",
        ]
        areas = _areas_by_place(result["worst_areas"])
        if not areas:
            lines.append("No residents are beyond the target.")
            slowest = result.get("slowest_areas") or []
            if slowest:
                lines.append("The places it takes longest to reach:")
                for place, rows in _areas_by_place(slowest, key="residents"):
                    medians = [a["median_seconds"] for a in rows]
                    text = f"- {place}: median {_range(medians, _minutes_text)}"
                    kms = [a["road_km"] for a in rows if a.get("road_km")]
                    stations = sorted({a["nearest_station"] for a in rows if a.get("nearest_station")})
                    if kms and len(stations) == 1:
                        text += f", {_range(kms, lambda v: f'{v} km')} by road from {stations[0]}"
                    lines.append(text)
            return "\n".join(lines)
        lines.append("The places with the most residents beyond the target:")
        for place, rows in areas:
            residents = sum(a["residents_beyond_target"] for a in rows)
            medians = [a["median_seconds"] for a in rows]
            text = f"- {place}: {residents:,} residents beyond it" + (f" in {len(rows)} areas" if len(rows) > 1 else "")
            text += f", median {_range(medians, _minutes_text)}"
            kms = [a["road_km"] for a in rows if a.get("road_km")]
            stations = sorted({a["nearest_station"] for a in rows if a.get("nearest_station")})
            if kms and len(stations) == 1:
                text += f", {_range(kms, lambda v: f'{v} km')} by road from {stations[0]}"
            lines.append(text)
        return "\n".join(lines)
    if tool == "recommend_station":
        best = result["best"][0]
        before = result["before"]
        head = (f"Of {result['candidates_tried']} candidate sites, one {result['service']} station {best['where']} would help most: "
                f"{best['population_within_target_percent']:.1f}% of residents within {result['target_seconds'] / 60:.0f} min")
        if before:
            head += f", up from {before['population_within_target_percent']:.1f}% ({best['residents_brought_within_target']:,} more residents)."
        else:
            head += "."
        # Sites that would sit next to the best one add nothing to compare
        rest = [f"- {o['where']}: {o['population_within_target_percent']:.1f}% within target"
                for o in result["best"][1:] if o["where"] != best["where"]]
        lines = [head, "Next best:"] + rest if rest else [head]
        mean_best = result.get("best_for_mean_response")
        if mean_best and not result.get("same_site_best_for_both"):
            lines.append(f"For the lowest average response instead, the best site is {mean_best['where']} "
                         f"(mean {_minutes_text(mean_best['mean_seconds'])}).")
        return "\n".join(lines)
    if tool == "station_plan":
        target = result["target_seconds"] / 60
        lines = []
        if result["existing"]:
            lines.append(f"The existing {result['service']} stations ({', '.join(result['existing_stations'])}) reach "
                         f"{result['existing']['population_within_target_percent']:.1f}% of residents within {target:.0f} min.")
        else:
            lines.append(f"OpenStreetMap maps no road {result['service']} station in Norwich, so there is no existing layout to compare.")
        lines.append(f"The optimiser's best {len(result['optimised_sites'])} sites ({'; '.join(result['optimised_sites'])}) reach "
                     f"{result['optimised']['population_within_target_percent']:.1f}% within {target:.0f} min, mean "
                     f"{_minutes_text(result['optimised']['mean_seconds'])}, out of {result['sets_compared']:,} sets compared.")
        lines += [f"- With {step['stations']} station{'s' if step['stations'] > 1 else ''}: mean {_minutes_text(step['mean_seconds'])}, "
                  f"{step['population_within_target_percent']:.1f}% within target" for step in result["each_added_station"]]
        return "\n".join(lines)
    if tool == "flood_vulnerability":
        lines = [f"Closing every road within {result['radius_metres']} m of each riverside place in turn, the worst for trips across the city:"]
        lines += [f"- {r['place']}: {r['roads_flooded']} roads, {r['trips_cut_off']} trip{'' if r['trips_cut_off'] == 1 else 's'} cut off, "
                  f"{r['trips_longer_percent']}% longer by {r['mean_extra_seconds']:.0f} s on average" for r in result["places"][:5]]
        lines.append("The map has no elevation or river data, so this ranks the effect of closures, not the chance of flooding.")
        return "\n".join(lines)
    if tool == "critical_roads":
        lines = [f"Of {result['trips_sampled']} trips drawn where people live, these roads carry the most, and closing each costs its trips:"]
        lines += [f"- {r['road']} ({r['where']}): {r['trips_using_it_percent']}% of trips, +{r['mean_extra_seconds_if_closed']:.0f} s on average if closed"
                  + (f", {r['trips_cut_off_if_closed']} cut off" if r["trips_cut_off_if_closed"] else "") for r in result["roads"]]
        return "\n".join(lines)
    if tool == "closure_impact":
        text = (f"Closing {', '.join(result['roads_closed'])} ({result['segments_closed']} road segments) makes "
                f"{result['trips_longer_percent']}% of {result['trips_sampled']} sampled trips longer, by "
                f"{result['mean_extra_seconds_for_affected_trips']:.0f} s on average (worst {result['worst_extra_seconds']:.0f} s)"
                + (f"; {result['trips_cut_off']} trips have no route at all." if result["trips_cut_off"] else "."))
        fire = result.get("fire_coverage")
        if fire:
            text += (f"\nFire cover within {fire['target_seconds'] / 60:.0f} min goes from "
                     f"{fire['before']['population_within_target_percent']:.1f}% to {fire['after']['population_within_target_percent']:.1f}% of residents.")
        return text
    if tool == "live_bottlenecks":
        rows = result["bottlenecks"]
        if not rows:
            return "No road in the live simulation is delayed right now."
        lines = [f"At T+{result['simulation_time']} s, the most delayed roads:"]
        lines += [f"- {r['road']} near {r['where']}: {r['vehicles']} vehicles at {r['speed_percent']}% of free-flow speed" for r in rows[:6]]
        if result["anomalies"]:
            lines.append("Unusually busy compared with their own recent traffic: " + "; ".join(
                f"{a['road']} ({a['vehicles']} vehicles, usually {a['usual']})" for a in result["anomalies"][:4]))
        return "\n".join(lines)
    if tool == "run_report":
        return "\n".join(result["findings"]) or "The simulation has no traffic to report yet."
    if tool == "scenario_results":
        if not result.get("available"):
            return result["reason"] + "."
        ranking = result["ranking"]
        top = ranking[0] if ranking else None
        lines = [f"In the study \"{result['study']['name']}\", ranked by {result['study']['metric'].get('label', result['study']['metric']['key'])}:"]
        lines += [f"- {i + 1}. {row['name']}" for i, row in enumerate(ranking[:5])]
        if top and result["explanation"]:
            lines.append(" ".join(str(e) if not isinstance(e, dict) else e.get("text", "") for e in result["explanation"][:2]))
        return "\n".join(lines)
    o = result
    stations = o.get("osm_stations") or {}
    return (f"NORWICH//SIM models Norwich's {o['road_km']} km of roads ({o['junctions']:,} junctions), "
            f"{(o.get('population') or {}).get('residents', 0):,} residents (Census 2021) and "
            f"{stations.get('fire', 0)} fire and {stations.get('police', 0)} police stations from OpenStreetMap. "
            "Ask about emergency coverage, where a new station would help, the roads trips depend on, "
            "a bridge closure, live bottlenecks, why a simulation performed as it did, or saved scenario studies.")


NUMBER = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?")
UNIT = re.compile(r"\s?-?\s?(%|per ?cent|min\b|mins\b|minutes?\b|s\b|secs?\b|seconds?\b|m\b|metres?\b|km\b|"
                  r"kilomet|hours?\b|vehicles?\b|residents?\b|people\b|trips?\b|roads?\b)", re.IGNORECASE)


def _numbers(value, out):
    """Every number in a calculation's result (values, and figures inside its text)."""
    if isinstance(value, bool) or value is None:
        return out
    if isinstance(value, (int, float)):
        out.add(float(value))
    elif isinstance(value, str):
        for match in NUMBER.findall(value):
            try:
                out.add(float(match.replace(",", "")))
            except ValueError:
                pass
    elif isinstance(value, dict):
        for item in value.values():
            _numbers(item, out)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _numbers(item, out)
    return out


def _matches(figure, value):
    """A figure written from a value: as it is, rounded, or seconds as minutes."""
    for candidate in (value, value / 60):
        for places in (0, 1, 2):
            if abs(round(candidate, places) - figure) < 1e-9:
                return True
    return False


def ungrounded_figures(text, sources, question=""):
    """Figures in an answer that no calculation produced (small whole
    numbers, which also number lists, are allowed). Empty when every figure
    comes from a tool result or the question."""
    allowed = set()
    for source in sources:
        _numbers(source["result"], allowed)
        _numbers(source["input"], allowed)
    _numbers(question, allowed)
    bad = []
    for found in NUMBER.finditer(text):
        match = found.group()
        try:
            figure = float(match.replace(",", ""))
        except ValueError:
            continue
        # Small whole numbers also number lists and count things ("two
        # bridges"); with a unit after them they are measurements and must
        # come from a calculation like any other
        measured = UNIT.match(text, found.end()) is not None
        if figure.is_integer() and 0 <= figure <= 10 and not measured:
            continue
        if not any(_matches(figure, value) for value in allowed):
            bad.append(match)
    return bad


def _source(tool, arguments, result):
    return {"tool": tool, "input": arguments, "provenance": "calculated", "result": result}


def answer(question, context, simulation_id=None, history=None, provider=None):
    """{"answer", "mode": "ai" or "rules", "model", "sources", "facts",
    "explanation", "proposal", "notice"}."""
    final = None
    for event in answer_stream(question, context, simulation_id, history, provider):
        if event["stage"] == "done":
            final = event["answer"]
    return final


def answer_stream(question, context, simulation_id=None, history=None, provider=None):
    """The answer in stages (assistant.steps for a local model); the last
    event is always {"stage": "done", "answer"}. Raises ValueError for an
    unusable question before anything is yielded."""
    question = (question or "").strip()
    if not question:
        raise ValueError("Ask a question")
    if len(question) > MAX_QUESTION:
        raise ValueError(f"Questions are limited to {MAX_QUESTION} characters")
    toolbox = Toolbox(context, simulation_id)
    provider = provider or get_provider()
    notice = None

    if provider.available and getattr(provider, "supports_structured", False):
        # A local model (Ollama): plan, calculate, explain, check (assistant.py)
        ok, reason = provider.check()
        if ok:
            from backend.intelligence import assistant

            try:
                yield from assistant.steps(question, context, provider, simulation_id, history)
                return
            except ProviderError as error:
                notice = (f"The local AI model could not answer ({error.reason}), so this answer comes from "
                          "the built-in rules.")
                logger.info("Local AI fell back to rules: %s", error.reason)
        else:
            notice = f"The local AI model is unavailable ({reason}), so this answer comes from the built-in rules."
    elif provider.available:
        try:
            result = _ask_model(question, toolbox, provider, history or [])
            bad = ungrounded_figures(result["answer"], result["sources"], question)
            if result["sources"] and not bad:
                yield {"stage": "done", "answer": result}
                return
            # Never pass on an answer no calculation backs: the rules answer instead
            if not result["sources"]:
                notice = ("The AI model answered without running any calculation, so its answer was "
                          "replaced by the built-in rules' answer.")
            else:
                shown = ", ".join(bad[:4]) + ("…" if len(bad) > 4 else "")
                notice = (f"The AI model's answer gave figures no calculation produced ({shown}), so it was "
                          "replaced by the built-in rules' answer.")
            logger.info("AI analyst answer rejected: %s", notice)
        except ProviderError as error:
            notice = f"The AI model could not answer ({error.reason}), so this answer comes from the built-in rules."
            logger.info("AI analyst fell back to rules: %s", error.reason)
    else:
        notice = "No AI model is configured, so this answer comes from the built-in rules (docs/AI.md)."

    has_run = bool(simulation_id and context.registry.get(simulation_id)) or context.registry.default() is not None
    tool, arguments = choose_tool(question, context.graph, has_run)
    try:
        result = toolbox.run(tool, arguments)
    except ToolError as error:
        yield {"stage": "done", "answer": {
            "answer": f"That could not be calculated: {error}.", "mode": "rules", "model": None,
            "sources": [], "facts": [], "explanation": None, "explanation_note": None, "proposal": None,
            "notice": notice}}
        return
    text = rule_answer(tool, result)
    if tool == "critical_roads" and not has_run and re.search(r"bottleneck|congest|jam|busiest", question.lower()):
        text = ("No live simulation is running, so these are the network's structural bottlenecks: the roads "
                "most trips depend on at free-flow speed. Start a simulation to see live congestion.\n" + text)
    yield {"stage": "done", "answer": {
        "answer": text, "mode": "rules", "model": None,
        "sources": [_source(tool, arguments, result)],
        "facts": [{"tool": tool, "text": text}], "explanation": None, "explanation_note": None, "proposal": None,
        "notice": notice}}


def _ask_model(question, toolbox, provider, history):
    messages = []
    for turn in history[-6:]:
        if turn.get("role") in ("user", "assistant") and isinstance(turn.get("content"), str):
            messages.append({"role": turn["role"], "content": turn["content"][:4000]})
    if messages and messages[0]["role"] != "user":
        messages = messages[1:]
    messages.append({"role": "user", "content": question})
    sources = []
    reply = None
    for _ in range(MAX_ROUNDS):
        reply = provider.respond(SYSTEM, messages, _tool_definitions())
        messages.append({"role": "assistant", "content": reply.content})
        if not reply.tool_calls:
            break
        results = []
        for call in reply.tool_calls:
            try:
                result = toolbox.run(call.name, call.input)
                sources.append(_source(call.name, call.input, result))
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": _clip(result)})
            except ToolError as error:
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": str(error), "is_error": True})
        messages.append({"role": "user", "content": results})
    text = (reply.text if reply else "").strip()
    if not text or (reply and reply.tool_calls):
        text = (text + "\n\n" if text else "") + "(The analysis needed more steps than allowed; the calculations so far are listed below.)"
    return {"answer": text, "mode": "ai", "model": provider.model, "sources": sources, "notice": None}
