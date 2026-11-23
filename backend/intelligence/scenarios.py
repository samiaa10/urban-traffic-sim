"""Scenarios from ordinary language.

    plan(text)       ->  a PROPOSAL: a list of supported actions, each with
                         real places (PLACES) and real road names, shown to
                         the user before anything happens
    validate(...)    ->  the proposal checked and resolved to coordinates
                         and road segments (also done again before running)
    run(...)         ->  a new live run of the simulation engine with the
                         actions applied by the engine's own code (floods,
                         closures, surges, the command centre's incidents),
                         plus the closures' measured effect on trip times

With an AI provider the request is translated by the model, which may only
answer through the propose_scenario tool: places from the PLACES list and
roads that exist on the map, so it cannot invent coordinates. Without one,
built-in rules parse the request. Either way the proposal is validated
here, and nothing runs until the user confirms it.
"""

import random
import re

from backend.intelligence.analysis import PLACES, _sample_trips, _trip_times, plain, segments_named
from backend.intelligence.providers import ProviderError, get_provider
from backend.optimiser.planning import CENTRAL_BRIDGES, FLOOD_AREA
from backend.simulation.command import KINDS as INCIDENT_KINDS


PLACE_NAMES = sorted(PLACES)
ACTION_TYPES = ("traffic", "surge", "flood", "close_roads", "close_bridges", "incidents")
LIMITS = {
    "vehicles": (0, 2000),
    "radius_metres": (50, 1500),
    "minutes": (5, 180),
    "vehicles_per_minute": (10, 300),
    "incidents": (1, 8),
}
SEVERITIES = ("minor", "moderate", "severe", "closed")
FLOOD_DEFAULT = "the city centre"


class ScenarioError(ValueError):
    pass


PROPOSE_TOOL = {
    "name": "propose_scenario",
    "description": "Propose a simulation scenario made only of supported actions. Places must be names from the "
                   "list; roads must be road names in Norwich. The user confirms before it runs.",
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["title", "actions", "notes"],
        "properties": {
            "title": {"type": "string", "maxLength": 80},
            "notes": {"type": "string", "maxLength": 400,
                      "description": "assumptions you made, e.g. sizes or durations the request did not give"},
            "actions": {
                "type": "array", "maxItems": 8,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["type"],
                    "properties": {
                        "type": {"type": "string", "enum": list(ACTION_TYPES)},
                        "place": {"type": "string", "enum": PLACE_NAMES},
                        "vehicles": {"type": "integer", "minimum": 0, "maximum": 2000},
                        "radius_metres": {"type": "integer", "minimum": 50, "maximum": 1500},
                        "minutes": {"type": "integer", "minimum": 5, "maximum": 180},
                        "severity": {"type": "string", "enum": list(SEVERITIES)},
                        "direction": {"type": "string", "enum": ["inbound", "outbound"]},
                        "vehicles_per_minute": {"type": "integer", "minimum": 10, "maximum": 300},
                        "roads": {"type": "array", "items": {"type": "string", "maxLength": 80}, "maxItems": 6},
                        "incidents": {
                            "type": "array", "maxItems": 8,
                            "items": {
                                "type": "object", "additionalProperties": False, "required": ["kind", "place"],
                                "properties": {"kind": {"type": "string", "enum": list(INCIDENT_KINDS)},
                                               "place": {"type": "string", "enum": PLACE_NAMES}},
                            },
                        },
                    },
                },
            },
        },
    },
}

PLANNER_SYSTEM = f"""You turn requests for traffic and emergency scenarios in Norwich into a proposal for \
NORWICH//SIM's simulation engine, by calling propose_scenario once.

Supported actions:
- traffic: background vehicles on the roads (vehicles, 0-2000; include ~800 unless the request says otherwise)
- surge: a rush hour into (inbound) or out of (outbound) a place (place, radius_metres, vehicles_per_minute, minutes)
- flood: every road within radius_metres of a place closed (severity "closed") or slowed, for minutes
- close_roads: close named roads (roads: names as they appear in Norwich)
- close_bridges: close the central river bridges ({", ".join(CENTRAL_BRIDGES)})
- incidents: emergency incidents (kind: {", ".join(INCIDENT_KINDS)}; place) answered by the emergency fleet

Use only places from the enum. If the request asks for something the engine cannot do (weather, \
earthquakes, rail, buses, pedestrians...), do not call the tool: say in one or two sentences what is not \
supported and what the closest supported scenario would be. State any assumption (sizes, durations, \
places) in notes. British English."""


def _number(action, key, default):
    value = action.get(key, default)
    low, high = LIMITS[key]
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not low <= value <= high:
        raise ScenarioError(f"{key.replace('_', ' ')} must be between {low} and {high}")
    return value


def _place(name):
    if name not in PLACES:
        raise ScenarioError(f"unknown place {name!r}")
    return PLACES[name]


def validate(proposal, graph, network):
    """The proposal checked and resolved: places to coordinates, roads to
    segments. Raises ScenarioError naming the first problem."""
    if not isinstance(proposal, dict) or not isinstance(proposal.get("actions"), list):
        raise ScenarioError("a scenario needs a list of actions")
    if not 1 <= len(proposal["actions"]) <= 8:
        raise ScenarioError("a scenario has between 1 and 8 actions")
    resolved = []
    for action in proposal["actions"]:
        kind = action.get("type") if isinstance(action, dict) else None
        if kind not in ACTION_TYPES:
            raise ScenarioError(f"unsupported action {kind!r}")
        if kind == "traffic":
            resolved.append({"type": kind, "vehicles": int(_number(action, "vehicles", 800))})
        elif kind == "surge":
            place = action.get("place", FLOOD_DEFAULT)
            lat, lon = _place(place)
            direction = action.get("direction", "inbound")
            if direction not in ("inbound", "outbound"):
                raise ScenarioError("a surge is inbound or outbound")
            resolved.append({"type": kind, "place": place, "latitude": lat, "longitude": lon, "direction": direction,
                             "radius_metres": _number(action, "radius_metres", 1000),
                             "vehicles_per_minute": int(_number(action, "vehicles_per_minute", 120)),
                             "minutes": int(_number(action, "minutes", 30))})
        elif kind == "flood":
            place = action.get("place", FLOOD_DEFAULT)
            lat, lon = _place(place)
            severity = action.get("severity", "closed")
            if severity not in SEVERITIES:
                raise ScenarioError(f"flood severity must be one of {', '.join(SEVERITIES)}")
            from backend.simulation.disruptions import segments_in_area

            radius = _number(action, "radius_metres", 300)
            if place == FLOOD_DEFAULT:
                # The city-centre flood is the resilience experiments' area by the Wensum
                lat, lon = FLOOD_AREA["latitude"], FLOOD_AREA["longitude"]
            roads = segments_in_area(network, lat, lon, radius)
            if not roads:
                raise ScenarioError(f"no road lies within {radius:.0f} m of {place}: make the flood bigger")
            resolved.append({"type": kind, "place": place, "latitude": lat, "longitude": lon, "radius_metres": radius,
                             "minutes": int(_number(action, "minutes", 30)), "severity": severity, "roads": len(roads)})
        elif kind == "close_roads":
            names = [r for r in action.get("roads") or [] if isinstance(r, str) and r.strip()]
            if not names:
                raise ScenarioError("name the roads to close")
            found = {name: len(segments_named(network, graph, [name])) for name in names[:6]}
            missing = [n for n, count in found.items() if count == 0]
            if missing:
                raise ScenarioError(f"no road on the map is called {', '.join(missing)}")
            resolved.append({"type": kind, "roads": list(found), "segments": sum(found.values())})
        elif kind == "close_bridges":
            resolved.append({"type": kind, "roads": list(CENTRAL_BRIDGES)})
        else:
            items = action.get("incidents") or []
            if not 1 <= len(items) <= LIMITS["incidents"][1]:
                raise ScenarioError("between 1 and 8 incidents")
            incidents = []
            for item in items:
                if not isinstance(item, dict) or item.get("kind") not in INCIDENT_KINDS:
                    raise ScenarioError(f"incident kinds are {', '.join(INCIDENT_KINDS)}")
                lat, lon = _place(item.get("place"))
                incidents.append({"kind": item["kind"], "place": item["place"], "latitude": lat, "longitude": lon})
            resolved.append({"type": kind, "incidents": incidents})
    if not any(a["type"] == "traffic" for a in resolved):
        resolved.insert(0, {"type": "traffic", "vehicles": 800})
    return {"title": str(proposal.get("title") or "Scenario")[:80], "notes": str(proposal.get("notes") or "")[:400],
            "actions": resolved}


def describe(action):
    """One line a person can check before confirming."""
    t = action["type"]
    if t == "traffic":
        return f"{action['vehicles']} vehicles of ordinary traffic across Norwich"
    if t == "surge":
        return (f"Rush hour {'into' if action['direction'] == 'inbound' else 'out of'} {action['place']} "
                f"(within {action['radius_metres']:.0f} m): {action['vehicles_per_minute']} vehicles a minute for {action['minutes']} min")
    if t == "flood":
        what = "closed" if action["severity"] == "closed" else f"slowed ({action['severity']})"
        return (f"Flood at {action['place']}: {action['roads']} roads within {action['radius_metres']:.0f} m {what} "
                f"for {action['minutes']} min, then recovering")
    if t == "close_roads":
        return f"Close {', '.join(action['roads'])} ({action['segments']} road segments)"
    if t == "close_bridges":
        return f"Close the central bridges: {', '.join(action['roads'])}"
    return "Emergency incidents: " + "; ".join(f"{i['kind'].replace('_', ' ')} at {i['place']}" for i in action["incidents"])


# ------------------------------------------------------------
# Reading the request
# ------------------------------------------------------------

def _place_in(text, default=FLOOD_DEFAULT):
    lowered = text.lower()
    for name in sorted(PLACE_NAMES, key=len, reverse=True):
        bare = name.lower().removeprefix("the ")
        if bare in lowered:
            return name
    if "centre" in lowered or "center" in lowered:
        return "the city centre"
    return default


def _duration(text, default):
    match = re.search(r"(\d+(?:\.\d+)?)\s*(hour|hr|h\b|min)", text.lower())
    if not match:
        return default
    value = float(match.group(1)) * (60 if match.group(2).startswith("h") else 1)
    return int(min(180, max(5, value)))


INCIDENT_SPREAD = [
    ("medical", "Earlham"), ("fire", "Lakenham"), ("road_accident", "the city centre"),
    ("crime", "Mile Cross"), ("medical", "Sprowston"), ("fire", "Thorpe Hamlet"),
    ("road_accident", "Eaton"), ("crime", "Heartsease"),
]


def rule_plan(text, graph):
    """The built-in rules' proposal, or ScenarioError if nothing in the
    request is supported."""
    from backend.intelligence.analyst import roads_in

    q = text.lower()
    actions, notes = [], []
    vehicles = re.search(r"(\d{2,4})\s*(?:vehicles|cars)", q)
    actions.append({"type": "traffic", "vehicles": min(2000, int(vehicles.group(1))) if vehicles else 800})
    if not vehicles:
        notes.append("800 vehicles of background traffic")
    if "flood" in q:
        radius = 450 if re.search(r"major|large|big|severe|widespread", q) else 150 if re.search(r"minor|small", q) else 300
        place = _place_in(text)
        actions.append({"type": "flood", "place": place, "radius_metres": radius, "minutes": _duration(text, 45), "severity": "closed"})
        notes.append(f"flood of {radius} m radius at {place}, roads closed")
    if re.search(r"rush[- ]hour|surge|peak", q):
        outbound = bool(re.search(r"evening|leav|out of|outbound|home", q))
        place = _place_in(text)
        actions.append({"type": "surge", "place": place, "direction": "outbound" if outbound else "inbound",
                        "radius_metres": 1200, "vehicles_per_minute": 150 if re.search(r"major|heavy|big", q) else 100,
                        "minutes": _duration(text, 30)})
    roads = [r for r in roads_in(text, graph) if "bridge" not in r.lower() or "street" in r.lower()]
    if re.search(r"\bclos", q) and roads:
        actions.append({"type": "close_roads", "roads": roads})
    elif "bridge" in q:
        actions.append({"type": "close_bridges"})
        notes.append("the central bridges (" + ", ".join(CENTRAL_BRIDGES) + ")")
    match = re.search(r"(\d+|two|three|four|five|six|several|multiple)\s+(?:simultaneous\s+)?(?:emergenc|incident)", q)
    if match or re.search(r"(multiple|several|simultaneous)\s+.*(emergenc|incident)", q):
        words = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
        word = match.group(1) if match else "multiple"
        count = int(word) if word.isdigit() else words.get(word, 4)
        count = max(1, min(8, count))
        actions.append({"type": "incidents", "incidents": [{"kind": k, "place": p} for k, p in INCIDENT_SPREAD[:count]]})
        notes.append(f"{count} incidents spread across the city (places chosen by the rules)")
    if len(actions) == 1 and not vehicles:
        raise ScenarioError(
            "The built-in rules found nothing they can simulate in that request. Supported: traffic, rush-hour "
            "surges, floods, road or bridge closures and emergency incidents, in named Norwich places"
        )
    title = text.strip().rstrip(".")[:80]
    return {"title": title[:1].upper() + title[1:], "notes": "Assumed: " + "; ".join(notes) if notes else "", "actions": actions}


def plan(text, context, provider=None):
    """{"proposal", "summary", "mode", "model", "notice"} or ScenarioError."""
    text = (text or "").strip()
    if not text:
        raise ScenarioError("Describe the scenario")
    if len(text) > 600:
        raise ScenarioError("Describe the scenario in at most 600 characters")
    provider = provider or get_provider()
    notice = None
    raw, mode, model = None, "rules", None
    if provider.available:
        try:
            reply = provider.respond(PLANNER_SYSTEM, [{"role": "user", "content": text}], [PROPOSE_TOOL])
            call = next((c for c in reply.tool_calls if c.name == "propose_scenario"), None)
            if call is None:
                raise ScenarioError(reply.text.strip() or "The AI model could not turn that into a supported scenario")
            raw, mode, model = call.input, "ai", provider.model
        except ProviderError as error:
            notice = f"The AI model could not answer ({error.reason}), so the built-in rules read the request."
    else:
        notice = "No AI model is configured, so the built-in rules read the request (docs/AI.md)."
    if raw is None:
        raw = rule_plan(text, context.graph)
    proposal = validate(raw, context.graph, context.network)
    return plain({
        "proposal": proposal,
        "summary": [describe(a) for a in proposal["actions"]],
        "mode": mode, "model": model, "notice": notice,
    })


# ------------------------------------------------------------
# Running it
# ------------------------------------------------------------

def run(proposal, context):
    """Start a live run with the proposal applied (validated again first).
    Returns its simulation_id, what was applied and the closures' measured
    effect on trip times."""
    from backend.api.routes.command import SHIFT_END, SHIFT_START, mobilise
    from backend.api.routes.emergencies import record_outcome
    from backend.api.routes.simulation import add_run, build_run
    from backend.simulation import dispatch, rerouting
    from backend.simulation.demand import Zone
    from backend.optimiser.planning import disruption_segments

    checked = validate(proposal, context.graph, context.network)
    graph = context.graph
    start = dispatch.nearest_junction(graph, *SHIFT_START)
    end = dispatch.nearest_junction(graph, *SHIFT_END)
    live, _ = build_run(context, start, end, traffic_model="osm")
    add_run(context, live, make_default=False)
    applied, closed = [], set()

    with live.lock:
        simulation = live.simulation
        command = mobilise(context, live, 2)
        for action in checked["actions"]:
            t = action["type"]
            if t == "traffic" and action["vehicles"]:
                outcome = live.traffic.spawn_vehicles(action["vehicles"], random.Random(1))
                live.recorder.fleet_added(outcome, 1)
                applied.append(f"{outcome['spawned']} vehicles added")
            elif t == "surge":
                surge = live.traffic.add_surge(
                    Zone(action["latitude"], action["longitude"], action["radius_metres"]),
                    action["direction"], action["vehicles_per_minute"], action["minutes"] * 60,
                )
                live.recorder.surge_started(surge)
                applied.append(describe(action))
            elif t == "flood":
                from backend.simulation.disruptions import segments_in_area

                roads = segments_in_area(context.network, action["latitude"], action["longitude"], action["radius_metres"])
                flood = simulation.add_flood(roads, action["severity"], action["minutes"] * 60, 600)
                if flood.closes_roads:
                    closed |= set(flood.segments)
                applied.append(describe(action))
            elif t in ("close_roads", "close_bridges"):
                segments = segments_named(context.network, graph, action["roads"]) if t == "close_roads" \
                    else set(disruption_segments(context.network, "bridges"))
                for segment in segments:
                    simulation.block_segment(segment, source="scenario")
                closed |= set(segments)
                applied.append(describe(action))
            elif t == "incidents":
                for item in action["incidents"]:
                    try:
                        _, outcomes = command.report(item["kind"], item["latitude"], item["longitude"], graph)
                    except ValueError as error:
                        applied.append(f"Not placed: {item['kind'].replace('_', ' ')} at {item['place']} ({error})")
                        continue
                    for outcome in outcomes:
                        record_outcome(live, outcome)
                applied.append(describe(action))
        if closed:
            rerouting.reroute_affected_vehicles(graph, simulation, context.node_by_point,
                                                route_finder=live.traffic.route_finder())
        live.traffic.process_events()
        live.recorder.sync()
        command.update()

    impact = None
    if closed:
        sample = _sample_trips(graph, context.network, 200, 7)
        before = _trip_times(context.network, sample)
        after = _trip_times(context.network, sample, blocked=closed)
        pairs = [(b[1], a[1]) for b, a in zip(before, after) if b[1] is not None]
        longer = [a - b for b, a in pairs if a is not None and a - b > 0.5]
        impact = {
            "provenance": "calculated",
            "method": "200 trips drawn in proportion to Census 2021 residents, fastest routes at free-flow speed, "
                      "with and without the scenario's closed roads",
            "roads_closed": len(closed),
            "trips_longer_percent": round(100 * len(longer) / max(1, len(pairs)), 1),
            "mean_extra_seconds": round(sum(longer) / len(longer), 1) if longer else 0.0,
            "trips_cut_off": sum(1 for _, a in pairs if a is None),
        }
    return plain({"simulation_id": live.id, "title": checked["title"], "applied": applied, "impact": impact})
