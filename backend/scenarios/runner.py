"""Running one scenario: a full simulation, start to finish, headless.

run_scenario(world, spec) builds a Simulation and a TrafficManager (the
same classes the API uses, unchanged), applies the scenario's events at
their times, steps one simulated second at a time until every trip has
arrived (or max_seconds), and measures the result with ImpactTracker.

Reproducibility. Every random choice comes from a stream seeded from the
spec: trips from Random("<seed>:trips"), surge trips from
Random("<seed>:surge:<i>"), emergency locations from
Random("<seed>:emergencies"), and the traffic manager's own generator
from the seed. Separate streams mean that adding emergencies, say, does
not change the trips. Trips are drawn first and then routed, and a trip
that cannot be routed (a closure cut it off) keeps its place, so two
scenarios with the same trip_signature() have the same trips in the same
order and can be compared trip by trip.

The World holds what is built once per process from the map (the graph,
RoadNetwork with its landmark tables, the demand model, the junction
lookup, and a cache of free-flow trip times) and reused by every scenario
that process runs.
"""

import hashlib
import math
import random
import resource
import sys
import time

from backend.routing.travel_time import RoadNetwork
from backend.scenarios.impact import ImpactTracker, percentile
from backend.scenarios.spec import ScenarioSpec, load_stored_spec, validate_against_graph
from backend.simulation import dispatch, rerouting
from backend.simulation.demand import DemandModel, Zone
from backend.simulation.simulation import Simulation
from backend.simulation.population import Sampler
from backend.simulation.road_model import make_road_model
from backend.simulation.traffic_manager import ReroutePolicy, TrafficManager, car_speed


# Bump when a change here would change results for the same spec
# 2: speed limits, capacity and per-direction traffic (the osm traffic
# model, spec.traffic_model); specs saved before version 2 ran the simple
# model and still do
ENGINE_VERSION = 2

# Trip statuses in the per-trip arrays
FINISHED, UNFINISHED, UNROUTABLE = "finished", "unfinished", "unroutable"


class World:
    """The map and what is derived from it, shared by many scenarios."""

    def __init__(self, graph, graph_sha256=None):
        self.graph = graph
        self.graph_sha256 = graph_sha256
        self.network = RoadNetwork(graph)
        self.network.use_landmarks(8)
        self.demand = DemandModel(self.network)
        self.node_by_point = rerouting.build_node_lookup(graph)
        self._free_flow = {}

    def free_flow_seconds(self, origin, destination, speed, limits=False):
        """Fastest time for a trip with no traffic and no closures (cached:
        the same trips recur across scenarios with the same seed). With
        limits, each road is driven at most at its speed limit."""

        key = (origin, destination, speed, limits)
        if key not in self._free_flow:
            self._free_flow[key] = self.network.fastest_path(
                origin, destination, speed, limits=limits
            )[1]
        return self._free_flow[key]


class ScenarioError(ValueError):
    """The spec does not fit the map (unknown junctions, ...)."""


def _stream(seed, name):
    """A random generator for one named stream of a scenario."""

    digest = hashlib.sha256(f"{seed}:{name}".encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _road_points(world, road):
    position = world.network.position
    return position[road.start], position[road.destination]


def _peak_rss_mb():
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports kilobytes, macOS bytes
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024


class _Trip:
    __slots__ = ("origin", "destination", "depart_at", "vehicle", "status", "source")

    def __init__(self, origin, destination, depart_at, source):
        self.origin = origin
        self.destination = destination
        self.depart_at = depart_at
        self.source = source
        self.vehicle = None
        self.status = None


def run_scenario(world, spec, progress=None, measure=True):
    """Run `spec` (a ScenarioSpec or a dict) to the end. Returns a plain,
    picklable dict (see the bottom of this function).

    progress(sim_time) is called every simulated minute if given.
    measure=False runs the same simulation without the impact accounting
    (only to benchmark what the accounting costs; the results are empty).
    """

    if not isinstance(spec, ScenarioSpec):
        spec = load_stored_spec(spec)

    problems = validate_against_graph(spec, world.graph)
    if problems:
        raise ScenarioError("; ".join(problems))

    wall_started = time.perf_counter()
    cpu_started = time.process_time()

    network = world.network

    # A simulation needs a first vehicle; park one where it cannot matter
    parked = network.point(world.demand.nodes[0])
    simulation = Simulation(
        [parked, parked],
        road_model=make_road_model(spec.effective_traffic_model, network),
    )
    simulation.vehicles[0].finished = True
    trip_speed = car_speed(simulation)

    traffic = TrafficManager(
        network,
        simulation,
        policy=ReroutePolicy(enabled=spec.rerouting),
        seed=int.from_bytes(hashlib.sha256(f"{spec.seed}:traffic".encode()).digest()[:4], "big"),
        demand=world.demand,
    )

    tracker = ImpactTracker(simulation, world.node_by_point, measure=measure)

    # ---- Trips: drawn first, routed when they leave -----------------
    trips = []
    trip_stream = _stream(spec.seed, "trips")
    count = spec.demand.vehicles

    for index in range(count):
        drawn = world.demand.random_trip(trip_stream)
        depart = (
            math.floor(index * spec.demand.spread_seconds / count)
            if count and spec.demand.spread_seconds else 0
        )
        trip = _Trip(drawn[0], drawn[1], depart, "demand") if drawn else _Trip(None, None, depart, "demand")
        if drawn is None:
            trip.status = UNROUTABLE
        trips.append(trip)

    pending = sorted(
        (t for t in trips if t.status is None), key=lambda t: t.depart_at
    )
    pending_index = 0

    # Surges, run here (not by the traffic manager) so they can start at
    # their own time and each surge trip keeps its place in the order
    surges = []
    for number, surge in enumerate(spec.surges):
        zone = Zone(surge.latitude, surge.longitude, surge.radius_metres)
        if not world.demand.nodes_in(zone):
            raise ScenarioError(f"surges[{number}]: no usable road junction inside the zone")
        surges.append({
            "spec": surge,
            "zone": zone,
            "stream": _stream(spec.seed, f"surge:{number}"),
            "due": 0.0,
            "trips": [],
        })

    # ---- Events at their times -------------------------------------
    events = []   # (time, order, kind, item)
    for number, closure in enumerate(spec.closures):
        events.append((closure.start_at, 0, number, "close", closure))
        if closure.end_at is not None:
            events.append((closure.end_at, 1, number, "reopen", closure))
    for number, accident in enumerate(spec.accidents):
        events.append((accident.start_at, 2, number, "accident", accident))
    events.sort(key=lambda event: event[:3])
    event_index = 0

    # ---- Emergency stations and the emergencies' schedule ----------
    for station_spec in spec.stations:
        station = simulation.add_station(
            station_spec.latitude, station_spec.longitude, station_spec.service
        )
        location = station.get_location()
        for _ in range(station_spec.ambulances):
            simulation.add_emergency_vehicle(
                [location, location], station, speed=station_spec.speed,
                vehicle_type=station_spec.service,
            )

    # Each group has its own stream (the first keeps the original name, so
    # a one-group spec draws exactly the locations it always did) and its
    # own place sampler (uniform, or weighted by population)
    emergency_plan = []
    for number, group in enumerate(spec.emergency_groups):
        stream = _stream(spec.seed, "emergencies" if number == 0 else f"emergencies:{number}")
        sampler = Sampler(world.network, world.demand.nodes, group.distribution)
        emergency_plan += [
            (group.first_at + n * group.interval_seconds, number, n, sampler.draw(stream))
            for n in range(group.count)
        ]
    emergency_plan.sort(key=lambda item: item[:3])
    emergency_index = 0
    emergencies = []   # dicts, filled in as they happen

    reroutes_before = traffic.stats["reroutes"]

    def depart(trip):
        """Route a trip under current traffic and closures and put it on
        the road; a trip with no route stays unroutable."""

        path, _, _ = traffic.plan(trip.origin, trip.destination, trip_speed)
        if not path:
            trip.status = UNROUTABLE
            return
        vehicle = simulation.add_vehicle(network.points(path), trip_speed)
        vehicle.origin, vehicle.destination = trip.origin, trip.destination
        trip.vehicle = vehicle
        tracker.track(vehicle)

    def segment(item):
        a, b = _road_points(world, item)
        return tuple(sorted((a, b)))

    # ---- The simulation loop ---------------------------------------
    last_surge_end = max(
        (s.start_at + s.duration_seconds for s in spec.surges), default=0
    )
    last_departure = max((t.depart_at for t in pending), default=0)

    while True:
        now = simulation.time

        # 1. Events due now
        while event_index < len(events) and events[event_index][0] <= now:
            _, _, _, kind, item = events[event_index]
            event_index += 1

            if kind == "close":
                simulation.block_segment(_road_points(world, item))
                rerouting.reroute_affected_vehicles(
                    world.graph, simulation, world.node_by_point,
                    route_finder=traffic.route_finder(),
                )
            elif kind == "reopen":
                simulation.unblock_segment(_road_points(world, item))
            else:
                simulation.add_accident(segment(item), item.severity, item.duration_seconds)

            traffic.process_events()

        # 2. Departures
        while pending_index < len(pending) and pending[pending_index].depart_at <= now:
            depart(pending[pending_index])
            pending_index += 1

        for surge in surges:
            s = surge["spec"]
            if not (s.start_at <= now < s.start_at + s.duration_seconds):
                continue
            surge["due"] += s.vehicles_per_minute / 60
            due = int(surge["due"])
            surge["due"] -= due
            zones = (
                {"destination_zone": surge["zone"]} if s.direction == "inbound"
                else {"origin_zone": surge["zone"]}
            )
            for _ in range(due):
                drawn = world.demand.random_trip(surge["stream"], **zones)
                trip = _Trip(
                    drawn[0] if drawn else None, drawn[1] if drawn else None,
                    now, f"surge:{len(surge['trips'])}",
                )
                surge["trips"].append(trip)
                if drawn is None:
                    trip.status = UNROUTABLE
                else:
                    depart(trip)

        # 3. Emergencies due now
        while emergency_index < len(emergency_plan) and emergency_plan[emergency_index][0] <= now:
            _, group, _, node = emergency_plan[emergency_index]
            emergency_index += 1
            latitude, longitude = network.position[node]
            service = spec.emergency_groups[group].service
            outcome = dispatch.dispatch_emergency(
                world.graph, simulation, latitude, longitude, service
            )
            emergencies.append({
                "at": now,
                "node": node,
                "group": group,
                "status": outcome["status"],
                "emergency": outcome["emergency"],
                "distance": outcome.get("distance"),
            })

        # 4. Stop when nothing is left to happen
        trips_done = tracker.all_finished
        emergencies_done = all(
            e["status"] not in ("dispatched", "queued") or e["emergency"].resolved
            for e in emergencies
        )
        if (
            now >= spec.max_seconds
            or (
                pending_index >= len(pending)
                and now >= last_departure
                and now >= last_surge_end
                and emergency_index >= len(emergency_plan)
                and trips_done
                and emergencies_done
            )
        ):
            break

        # 5. One second
        tracker.before_step()
        traffic.step()
        tracker.after_step()

        if progress is not None and simulation.time % 60 == 0:
            progress(simulation.time)

    tracker.finish()

    return _result(
        spec, world, simulation, traffic, tracker, trips, surges, emergencies,
        reroutes_before, wall_started, cpu_started,
    )


def _sent(entry):
    """Whether a vehicle was sent (at once, or later from the queue)."""

    return entry["status"] == "dispatched" or (
        entry["status"] == "queued" and entry["emergency"].assigned_vehicle is not None
    )


def _emergency_figures(entries, requested, target_of):
    responses = [
        e["emergency"].response_time for e in entries if _sent(e) and e["emergency"].resolved
    ]
    within = sum(
        1 for e in entries
        if _sent(e) and e["emergency"].resolved and e["emergency"].response_time <= target_of(e)
    )
    return {
        "requested": requested,
        "dispatched": sum(1 for e in entries if _sent(e)),
        "refused": sum(1 for e in entries if e["status"] in ("no_vehicle", "no_route")),
        "arrived": len(responses),
        "mean_response_seconds": float(sum(responses) / len(responses)) if responses else None,
        "worst_response_seconds": float(max(responses)) if responses else None,
        "within_target_percent": 100 * within / requested if requested else None,
    }


def summarise_emergencies(spec, emergencies):
    """The emergency figures of a scenario: the headline numbers (over all
    groups, each emergency judged against its own group's target) and,
    under "detail", the queue and each service separately."""

    groups = spec.emergency_groups
    requested = sum(group.count for group in groups)
    summary = _emergency_figures(
        emergencies, requested, lambda e: groups[e["group"]].target_seconds
    )

    waits = [
        e["emergency"].dispatched_at - e["emergency"].created_at
        for e in emergencies if _sent(e) and e["emergency"].dispatched_at is not None
    ]
    by_service = []
    for service in sorted({group.service for group in groups if group.count}):
        entries = [e for e in emergencies if groups[e["group"]].service == service]
        figures = _emergency_figures(
            entries,
            sum(group.count for group in groups if group.service == service),
            lambda e: groups[e["group"]].target_seconds,
        )
        by_service.append({"service": service, **figures})

    summary["detail"] = {
        "queued": sum(1 for e in emergencies if e["status"] == "queued"),
        "still_queued": sum(
            1 for e in emergencies
            if e["status"] == "queued" and e["emergency"].assigned_vehicle is None
        ),
        "mean_wait_seconds": float(sum(waits) / len(waits)) if waits else None,
        "max_wait_seconds": float(max(waits)) if waits else None,
        "by_service": by_service,
    }
    return summary


def _result(spec, world, simulation, traffic, tracker, trips, surges,
            emergencies, reroutes_before, wall_started, cpu_started):
    all_trips = trips + [t for s in surges for t in s["trips"]]

    journey, free_flow, delay, status = [], [], [], []
    origins = [trip.origin for trip in all_trips]
    destinations = [trip.destination for trip in all_trips]
    finished_increase, finished_delay = [], []
    total_delay = 0.0
    total_travel = 0.0

    for trip in all_trips:
        vehicle = trip.vehicle

        if vehicle is None:
            journey.append(None)
            free_flow.append(None)
            delay.append(None)
            status.append(UNROUTABLE)
            continue

        lost = float(getattr(vehicle, "scenario_delay", 0.0))
        reference = float(world.free_flow_seconds(
            trip.origin, trip.destination,
            car_speed(simulation), simulation.road_model.has_limits,
        ))

        journey.append(vehicle.journey_seconds)
        free_flow.append(reference)
        delay.append(lost)
        total_delay += lost
        total_travel += vehicle.journey_seconds

        if vehicle.finished:
            status.append(FINISHED)
            finished_delay.append(lost)
            finished_increase.append(vehicle.journey_seconds - reference)
        else:
            status.append(UNFINISHED)

    spawned = sum(1 for s in status if s != UNROUTABLE)
    finished = status.count(FINISHED)
    vehicle_seconds = sum(row["vehicle_seconds"] for row in tracker.timeseries)

    emergency_summary = summarise_emergencies(spec, emergencies)

    wall = time.perf_counter() - wall_started

    summary = {
        "trips_requested": len(all_trips),
        "trips_spawned": spawned,
        "trips_unroutable": len(all_trips) - spawned,
        "trips_finished": finished,
        "completion_percent": 100 * finished / spawned if spawned else None,
        "total_delay_vehicle_hours": total_delay / 3600,
        "total_travel_vehicle_hours": total_travel / 3600,
        "mean_delay_seconds": sum(finished_delay) / len(finished_delay) if finished_delay else None,
        "p95_delay_seconds": percentile(finished_delay, 0.95),
        "max_delay_seconds": max(finished_delay) if finished_delay else None,
        "mean_increase_seconds": (
            sum(finished_increase) / len(finished_increase) if finished_increase else None
        ),
        "p95_increase_seconds": percentile(finished_increase, 0.95),
        "max_increase_seconds": max(finished_increase) if finished_increase else None,
        "reroutes": traffic.stats["reroutes"] - reroutes_before,
        "simulated_seconds": simulation.time,
        "hit_time_limit": simulation.time >= spec.max_seconds,
    }

    return {
        "engine_version": ENGINE_VERSION,
        "graph_sha256": world.graph_sha256,
        "spec_fingerprint": spec.fingerprint(),
        "trip_signature": spec.trip_signature(),
        "summary": summary,
        "emergencies": emergency_summary,
        "trips": {
            "origin": origins,
            "destination": destinations,
            "journey_seconds": journey,
            "free_flow_seconds": free_flow,
            "delay_seconds": delay,
            "status": status,
        },
        "roads": tracker.top_roads(),
        "areas": tracker.top_areas(),
        "timeseries": tracker.timeseries,
        "execution": {
            "wall_seconds": wall,
            "cpu_seconds": time.process_time() - cpu_started,
            "peak_rss_mb": _peak_rss_mb(),
            "vehicle_seconds": vehicle_seconds,
            "vehicle_seconds_per_second": vehicle_seconds / wall if wall > 0 else None,
        },
    }
