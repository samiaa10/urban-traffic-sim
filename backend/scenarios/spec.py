"""What a scenario is: everything needed to run it again, exactly.

A scenario is a full simulation on the road network, described by a
ScenarioSpec: the demand (how many trips, when they leave), surges,
road closures and accidents (each with a start time and an end), whether
dynamic rerouting is on, emergency stations and emergencies, a time limit
and a seed. The same spec on the same map always gives the same result:
every random choice comes from a stream seeded from `seed` (see runner.py).

Times are simulated seconds from the start of the scenario.

Roads are named by their two junction ids, as in the API
(/incident/block?start=&destination=). They are checked against the map
by validate_against_graph before a scenario runs.
"""

import hashlib
import json
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


# Limits that keep one scenario's cost bounded (see docs/scenario_benchmark.md:
# 2,000 vehicles on Norwich took about 90 s to run to completion)
MAX_VEHICLES = 2000
MAX_SECONDS = 7200


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Road(Strict):
    """A road between two adjacent junctions (either direction)."""

    start: int
    destination: int


class Closure(Road):
    start_at: float = Field(0, ge=0, le=MAX_SECONDS)
    # None: closed until the end of the scenario
    end_at: Optional[float] = Field(None, gt=0, le=MAX_SECONDS)

    @model_validator(mode="after")
    def _ends_after_start(self):
        if self.end_at is not None and self.end_at <= self.start_at:
            raise ValueError("end_at must be after start_at")
        return self


class Accident(Road):
    severity: Literal["minor", "moderate", "severe"] = "moderate"
    start_at: float = Field(0, ge=0, le=MAX_SECONDS)
    # None: the severity's default duration
    duration_seconds: Optional[float] = Field(None, gt=0, le=86400)


class Surge(Strict):
    """Extra trips into (inbound) or out of (outbound) a circular zone."""

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    radius_metres: float = Field(800, ge=100, le=5000)
    direction: Literal["inbound", "outbound"] = "inbound"
    vehicles_per_minute: float = Field(60, gt=0, le=600)
    start_at: float = Field(0, ge=0, le=MAX_SECONDS)
    duration_seconds: float = Field(900, gt=0, le=MAX_SECONDS)


class Demand(Strict):
    """Background trips across the network (the demand model)."""

    vehicles: int = Field(500, ge=0, le=MAX_VEHICLES)
    # Departures spread evenly over this many seconds (0: all at once)
    spread_seconds: float = Field(0, ge=0, le=MAX_SECONDS)


class Station(Strict):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    ambulances: int = Field(1, ge=1, le=10)
    speed: float = Field(20, gt=0, le=60)


class Emergencies(Strict):
    """Emergencies at random junctions (chosen by the seed), one every
    interval_seconds from first_at, each answered by the nearest station
    with an ambulance free (as /emergency/create does)."""

    count: int = Field(0, ge=0, le=200)
    first_at: float = Field(60, ge=0, le=MAX_SECONDS)
    interval_seconds: float = Field(60, gt=0, le=MAX_SECONDS)
    target_seconds: float = Field(480, gt=0, le=3600)


class ScenarioSpec(Strict):
    name: str = Field("Scenario", min_length=1, max_length=120)
    seed: int = Field(1, ge=0, le=2**31 - 1)
    demand: Demand = Demand()
    surges: List[Surge] = Field(default_factory=list, max_length=10)
    closures: List[Closure] = Field(default_factory=list, max_length=50)
    accidents: List[Accident] = Field(default_factory=list, max_length=50)
    rerouting: bool = True
    # How roads behave (simulation/road_model.py): "osm" uses OpenStreetMap
    # speed limits, lanes and per-direction traffic; "simple" is the
    # original rules. Not given: the API fills in the server's default
    # (TRAFFIC_MODEL) before saving the study; a spec still without one
    # (saved before this field existed, or built directly in code) runs
    # the simple model it always ran.
    traffic_model: Optional[Literal["osm", "simple"]] = None
    stations: List[Station] = Field(default_factory=list, max_length=20)
    emergencies: Emergencies = Emergencies()
    # Stop here even if trips are still on their way
    max_seconds: float = Field(3600, ge=60, le=MAX_SECONDS)

    @model_validator(mode="after")
    def _emergencies_need_stations(self):
        if self.emergencies.count and not self.stations:
            raise ValueError("emergencies need at least one station")
        return self

    @property
    def effective_traffic_model(self):
        return self.traffic_model or "simple"

    def fingerprint(self):
        """SHA-256 of everything that affects the result (not the name).
        The simple traffic model is left out, so a spec keeps the
        fingerprint it had before traffic_model existed."""

        exclude = {"name"}
        if self.effective_traffic_model == "simple":
            exclude.add("traffic_model")

        body = self.model_dump(mode="json", exclude=exclude)
        text = json.dumps(body, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode()).hexdigest()

    def trip_signature(self):
        """Scenarios with the same signature have the same trips (same
        seed, demand and surges), so they can be compared trip by trip."""

        body = self.model_dump(mode="json", include={"seed", "demand", "surges"})
        text = json.dumps(body, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode()).hexdigest()[:16]


def load_stored_spec(spec_dict):
    """A ScenarioSpec from a stored spec (one saved before traffic_model
    existed has none, and so runs the simple model it ran with)."""

    return ScenarioSpec.model_validate(spec_dict)


def validate_against_graph(spec, graph):
    """Problems with the spec on this map (empty list if none)."""

    problems = []

    def check_road(kind, index, road):
        for label, node in (("start", road.start), ("destination", road.destination)):
            if node not in graph.nodes:
                problems.append(f"{kind}[{index}].{label}: junction {node} is not on the map")
                return
        if not (
            graph.has_edge(road.start, road.destination)
            or graph.has_edge(road.destination, road.start)
        ):
            problems.append(
                f"{kind}[{index}]: junctions {road.start} and {road.destination} "
                "are not directly connected by a road"
            )

    for index, closure in enumerate(spec.closures):
        check_road("closures", index, closure)

    for index, accident in enumerate(spec.accidents):
        check_road("accidents", index, accident)

    return problems
