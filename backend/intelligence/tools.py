"""The operations an AI model may ask for: a fixed list of read-only
analyses (analysis.py), each with a JSON Schema. The model never runs code
or queries the database; it names one of these and its arguments, which
are checked against the schema here before anything runs, and it gets
back the calculation's result. Nothing here changes a simulation.
"""

from backend.intelligence import analysis
from backend.simulation.services import SERVICES


SERVICE = {"type": "string", "enum": sorted(SERVICES)}
MINUTES = {"type": ["number", "null"], "minimum": 1, "maximum": 30,
           "description": "target response time in minutes; null for the service's own target"}
DISRUPTION = {"type": "string", "enum": ["none", "flood", "bridges"],
              "description": "none, flood (the Wensum by the city centre) or bridges (the central bridges closed)"}
RUN = {"type": ["string", "null"], "description": "a live simulation's id; null for the one the user is watching"}


def _object(properties):
    return {"type": "object", "properties": properties, "required": sorted(properties), "additionalProperties": False}


TOOLS = [
    {
        "name": "city_overview",
        "description": "The city model: road network size, mapped emergency stations, census population, "
                       "signalised junctions and live runs.",
        "input_schema": _object({}),
    },
    {
        "name": "coverage_gaps",
        "description": "Where emergency response from the existing stations is slowest: the areas with the most "
                       "residents beyond the target time, and the share of residents within it. Road-network "
                       "distance over service speed, no traffic.",
        "input_schema": _object({"service": SERVICE, "target_minutes": MINUTES, "disruption": DISRUPTION}),
    },
    {
        "name": "recommend_station",
        "description": "Tries every candidate site as one extra station beside the existing ones and returns the "
                       "best by residents brought within the target.",
        "input_schema": _object({"service": SERVICE, "target_minutes": MINUTES, "disruption": DISRUPTION}),
    },
    {
        "name": "station_plan",
        "description": "The optimiser's best layout of a number of stations for a service, against the existing "
                       "stations (OpenStreetMap maps no road ambulance station), and what each added station gives. "
                       "For questions about improving a service's response times.",
        "input_schema": _object({
            "service": SERVICE,
            "stations": {"type": "integer", "minimum": 1, "maximum": 8},
            "target_minutes": MINUTES,
        }),
    },
    {
        "name": "flood_vulnerability",
        "description": "Which riverside places (Wensum and Yare) would hurt trips across the city most if flooded: "
                       "every road near each place closed in turn. Closures, not a hydrological model.",
        "input_schema": _object({}),
    },
    {
        "name": "critical_roads",
        "description": "The roads most trips across Norwich depend on (trips drawn where people live), and what "
                       "closing each one costs those trips. Free-flow speeds.",
        "input_schema": _object({}),
    },
    {
        "name": "closure_impact",
        "description": "What closing roads does to trip times (and, for the central bridges, to fire coverage). "
                       "Give road names as on the map, or central_bridges true.",
        "input_schema": _object({
            "roads": {"type": "array", "items": {"type": "string", "maxLength": 80}, "maxItems": 6},
            "central_bridges": {"type": "boolean"},
        }),
    },
    {
        "name": "live_bottlenecks",
        "description": "A live simulation's most delayed roads now and any congestion anomalies (roads far above "
                       "their own recent traffic).",
        "input_schema": _object({"simulation_id": RUN}),
    },
    {
        "name": "run_report",
        "description": "A live simulation's figures (vehicles, delay, reroutes, disruption, emergency response) "
                       "and what they show.",
        "input_schema": _object({"simulation_id": RUN}),
    },
    {
        "name": "scenario_results",
        "description": "The ranking of a saved scenario study (the newest completed one if no id is given) and "
                       "why the leader leads.",
        "input_schema": _object({"study_id": {"type": ["string", "null"]}}),
    },
]

BY_NAME = {tool["name"]: tool for tool in TOOLS}


class ToolError(Exception):
    pass


def _check(value, schema, path):
    kind = schema.get("type")
    kinds = kind if isinstance(kind, list) else [kind]
    if value is None:
        if "null" not in kinds:
            raise ToolError(f"{path} is required")
        return
    if "enum" in schema and value not in schema["enum"]:
        raise ToolError(f"{path} must be one of {', '.join(map(str, schema['enum']))}")
    if "string" in kinds and isinstance(value, str):
        if len(value) > schema.get("maxLength", 200):
            raise ToolError(f"{path} is too long")
        return
    if "boolean" in kinds and isinstance(value, bool):
        return
    if "integer" in kinds and isinstance(value, int) and not isinstance(value, bool):
        if value < schema.get("minimum", float("-inf")) or value > schema.get("maximum", float("inf")):
            raise ToolError(f"{path} is out of range")
        return
    if "number" in kinds and isinstance(value, (int, float)) and not isinstance(value, bool):
        if value < schema.get("minimum", float("-inf")) or value > schema.get("maximum", float("inf")):
            raise ToolError(f"{path} is out of range")
        return
    if "array" in kinds and isinstance(value, list):
        if len(value) > schema.get("maxItems", 20):
            raise ToolError(f"{path} has too many items")
        for i, item in enumerate(value):
            _check(item, schema["items"], f"{path}[{i}]")
        return
    if "object" in kinds and isinstance(value, dict):
        validate(value, schema, path)
        return
    raise ToolError(f"{path} has the wrong type")


def validate(arguments, schema, path="input"):
    if not isinstance(arguments, dict):
        raise ToolError(f"{path} must be an object")
    extra = set(arguments) - set(schema["properties"])
    if extra:
        raise ToolError(f"unknown argument(s): {', '.join(sorted(extra))}")
    missing = [name for name in schema.get("required", []) if name not in arguments]
    if missing:
        raise ToolError(f"missing argument(s): {', '.join(missing)}")
    for name, sub in schema["properties"].items():
        _check(arguments.get(name), sub, f"{path}.{name}" if path != "input" else name)


class Toolbox:
    """Runs the tools for one request: knows the app's context and which
    live run the user is watching."""

    def __init__(self, context, simulation_id=None):
        self.context = context
        self.simulation_id = simulation_id

    def _run(self, simulation_id):
        registry = self.context.registry
        run = registry.get(simulation_id or self.simulation_id) if (simulation_id or self.simulation_id) else None
        if run is None:
            run = registry.default() if hasattr(registry, "default") else None
        if run is None:
            raise ToolError("No live simulation is running: start one (or the command centre) first")
        return run

    def run(self, name, arguments):
        """The tool's result as plain JSON; raises ToolError for a bad name
        or arguments, or when the tool cannot answer."""
        tool = BY_NAME.get(name)
        if tool is None:
            raise ToolError(f"there is no tool called {name}")
        validate(arguments, tool["input_schema"])
        context = self.context
        graph, network = context.graph, context.network
        try:
            if name == "city_overview":
                from backend.api.routes.city import city_overview

                result = city_overview(context)
            elif name == "coverage_gaps":
                result = analysis.coverage_gaps(graph, network, arguments["service"], arguments["target_minutes"], arguments["disruption"])
            elif name == "recommend_station":
                result = analysis.recommend_station(graph, network, arguments["service"], arguments["target_minutes"], arguments["disruption"])
            elif name == "critical_roads":
                result = analysis.critical_roads(graph, network)
            elif name == "station_plan":
                result = analysis.station_plan(graph, network, arguments["service"], arguments["stations"], arguments["target_minutes"])
            elif name == "flood_vulnerability":
                result = analysis.flood_vulnerability(graph, network)
            elif name == "closure_impact":
                result = analysis.closure_impact(graph, network, roads=arguments["roads"], bridges=arguments["central_bridges"])
            elif name == "live_bottlenecks":
                run = self._run(arguments["simulation_id"])
                with run.lock:
                    result = analysis.bottlenecks(run, context)
            elif name == "run_report":
                run = self._run(arguments["simulation_id"])
                with run.lock:
                    result = analysis.run_report(run, context)
            else:
                result = analysis.scenario_results(context, arguments["study_id"])
        except ValueError as error:
            raise ToolError(str(error)) from None
        return analysis.plain(result)
