"""
Traffic manager: the simulation plus the road network, working together
------------------------------------------------------------------------

Simulation (simulation.py) moves vehicles but knows nothing about the road
graph. This class owns both and adds what needs the graph:

  * Fleets: spawn N vehicles with trips from the demand model (demand.py),
    each given the fastest route under current traffic.
  * Traffic surges: for a while, keep adding trips into (or out of) a zone,
    like a rush hour.
  * Dynamic rerouting: vehicles re-plan when a meaningfully faster route
    exists, using the same travel-time costs the simulation moves them
    with (Simulation.road_speed_factor).
  * Reacting to events: an accident or closure on a vehicle's route makes
    it re-plan at once; a road reopening or an accident clearing makes
    nearby vehicles that might benefit re-plan at once.

How rerouting avoids constant flip-flopping (ReroutePolicy):

  * A vehicle only switches if the new route saves at least
    min_saving_seconds AND min_saving_fraction of its remaining time.
  * Routine checks are spread out: each vehicle is checked once every
    check_interval seconds, with vehicles staggered so they are not all
    checked in the same second.
  * After switching, a vehicle waits cooldown_seconds before its next
    routine switch (events can still make it switch: an accident on the
    new route should not be ignored).
  * A route is only searched for when one could possibly save enough:
    if the remaining time is already within the threshold of the
    straight-line time to the destination (which no route can beat),
    the search is skipped.

Costs: a road costs length / (speed x speed factor), where the speed
factor is the one the vehicle would have if it joined the road (vehicles
on it + 1, and any accident). Both the current route and the alternative
are costed the same way, from the same traffic, so they are compared
fairly.

Which traffic? By default routes are planned with the traffic on each road
this second, the same traffic vehicles move by. smoothing_seconds > 1
plans with an exponential moving average over about that many seconds
instead (as navigation services use recent average speeds). It was
expected to stop drivers chasing short-lived queues, but on both the test
grid and Norwich it made journeys longer (docs/traffic_benchmark.md), so
it is off by default. Accidents and closures are never averaged: they take
effect in planning immediately.

The default thresholds (check every 10 s, 60 s cooldown, save at least
20 s and 10%) were chosen from a comparison on Norwich: they gave most of
the journey-time benefit of more eager settings with about half as many
route changes (see the README).
"""

import math
import random
import time

from backend.simulation import dispatch, rerouting
from backend.simulation.demand import DemandModel, Zone


DEFAULT_SPEED = 13.9   # m/s, the same default car speed as Vehicle


def car_speed(simulation, speed=DEFAULT_SPEED):
    """Top speed for a new car: in the osm traffic model the road's speed
    limit decides (road_model.CAR_TOP_SPEED), otherwise `speed`."""

    from backend.simulation.road_model import CAR_TOP_SPEED

    return CAR_TOP_SPEED if simulation.road_model.has_limits else speed


class ReroutePolicy:

    def __init__(
        self,
        enabled=True,
        check_interval=10,
        min_saving_seconds=20,
        min_saving_fraction=0.1,
        cooldown_seconds=60,
        smoothing_seconds=1,
    ):
        if not check_interval >= 1:
            raise ValueError("check_interval must be at least 1 second")

        if not smoothing_seconds >= 1:
            raise ValueError("smoothing_seconds must be at least 1 (1 = no smoothing)")

        # Traffic used for planning is averaged over about this many
        # seconds (1 = plan with this second's traffic only)
        self.smoothing_seconds = smoothing_seconds

        self.enabled = enabled
        self.check_interval = int(check_interval)
        self.min_saving_seconds = min_saving_seconds
        self.min_saving_fraction = min_saving_fraction
        self.cooldown_seconds = cooldown_seconds

    def describe(self):
        return {
            "enabled": self.enabled,
            "check_interval": self.check_interval,
            "min_saving_seconds": self.min_saving_seconds,
            "min_saving_fraction": self.min_saving_fraction,
            "cooldown_seconds": self.cooldown_seconds,
            "smoothing_seconds": self.smoothing_seconds,
        }


class Surge:
    """Extra trips into (inbound) or out of (outbound) a zone for a while."""

    DIRECTIONS = ("inbound", "outbound")

    def __init__(self, zone, direction, vehicles_per_minute, starts_at, duration):
        if direction not in self.DIRECTIONS:
            raise ValueError("direction must be inbound or outbound")

        if not vehicles_per_minute > 0 or vehicles_per_minute == math.inf:
            raise ValueError("vehicles_per_minute must be a positive number")

        if not duration > 0 or duration == math.inf:
            raise ValueError("duration must be a positive number of seconds")

        self.zone = zone
        self.direction = direction
        self.vehicles_per_minute = vehicles_per_minute
        self.starts_at = starts_at
        self.ends_at = starts_at + duration
        self.due = 0.0          # vehicles owed but not yet spawned
        self.spawned = 0
        self.failed = 0

    def active(self, now):
        return self.starts_at <= now < self.ends_at

    def describe(self, now):
        return {
            "zone": self.zone.describe(),
            "direction": self.direction,
            "vehicles_per_minute": self.vehicles_per_minute,
            "starts_at": self.starts_at,
            "ends_at": self.ends_at,
            "active": self.active(now),
            "spawned": self.spawned,
            "failed": self.failed,
        }


class TrafficManager:

    def __init__(
        self, network, simulation, policy=None, seed=1, demand=None, landmarks=8
    ):
        self.network = network
        self.simulation = simulation
        self.policy = policy or ReroutePolicy()
        self.demand = demand or DemandModel(network)
        self.random = random.Random(seed)

        # Landmark lower bounds speed up every search and let hopeless
        # searches be skipped (computed once per network, then shared)
        if landmarks and not network.landmarks:
            network.use_landmarks(landmarks)

        # Vehicles drive the same road lengths the router measures
        simulation.use_road_lengths(network.length_between_points)

        self.surges = []

        # Simulation events already handled
        self._events_seen = len(simulation.events)

        # Vehicles (by list index) that must be checked at the next pass,
        # whatever their routine schedule, and when each last switched
        # Vehicles (by list index) to check at the next pass whatever their
        # routine schedule: index -> None (something on their route got
        # worse: take any better route) or a set of roads that got better
        # (switch only to a route that uses one of them)
        self._urgent = {}
        self._last_switch = {}

        self._slow_cache = {}
        self._slow_cache_key = None

        # Emergencies the queue sent a vehicle to in the last step
        self.dispatched_from_queue = []

        # Batched rerouting (see _batched_route): per reroute pass, how many
        # vehicles head to each destination, and the shared searches
        self._batch_groups = {}
        self._batch_trees = {}

        # Recent average number of vehicles on each road (see the module
        # docstring); starts from the traffic right now
        self.recent_traffic = {
            segment: float(count)
            for segment, count in simulation.get_traffic_density().items()
        }

        self.stats = {
            "routine_checks": 0,
            "urgent_checks": 0,
            "searches": 0,
            "skipped_no_possible_saving": 0,
            "reroutes": 0,
            "search_seconds": 0.0,
            # Batched rerouting: shared searches, and vehicles they served
            "batched_searches": 0,
            "batched_vehicles": 0,
        }

    # ------------------------------------------------------------
    # Costs and searches
    # ------------------------------------------------------------

    def update_recent_traffic(self, traffic=None):
        """Fold this second's traffic into the recent average (each step)."""

        if traffic is None:
            traffic = self.simulation.get_traffic_density()

        weight = 1.0 / self.policy.smoothing_seconds
        recent = self.recent_traffic

        for segment in set(recent) | set(traffic):
            value = recent.get(segment, 0.0)
            value += weight * (traffic.get(segment, 0) - value)

            if value < 0.05:
                recent.pop(segment, None)
            else:
                recent[segment] = value

    def _joining_factor(self, segment, count):
        """Speed factor for a vehicle joining a road that has `count` on it."""

        return self.simulation.road_speed_factor(segment, round(count) + 1)

    @property
    def road_model(self):
        return self.simulation.road_model

    def _accident_keys(self):
        """Traffic keys of every road with an accident (both directions in
        the osm model)."""

        keys_of = self.road_model.keys_of_segment
        return {key for segment in self.simulation.incident_segments() for key in keys_of(segment)}

    def _slow_roads(self, traffic):
        """{road: joining speed factor} for every road that is not free flowing.

        Only roads with vehicles on them or an accident can be slower than
        1.0, so this is all a search needs: every other road is 1.0. It is
        worked out once per traffic state (cached until the simulated time,
        the traffic or the accidents change) instead of once per road per
        search, which was the main cost of rerouting a large fleet.
        """

        def build():
            slow = {}

            for segment in set(traffic) | self._accident_keys():
                factor = self._joining_factor(segment, traffic.get(segment, 0))

                if factor != 1.0:
                    slow[segment] = factor

            return slow

        # Only the manager's own traffic table is cached: it changes only
        # inside step(), when the simulated time changes too. Any other
        # table a caller passes in is worked out fresh.
        if traffic is not self.recent_traffic:
            return build()

        key = (self.simulation.time, len(self.simulation.events))

        if self._slow_cache_key != key:
            self._slow_cache = build()
            self._slow_cache_key = key

        return self._slow_cache

    def planning_traffic(self):
        """The traffic routes are planned with, brought up to date.

        Without smoothing (the default) this is the live count of vehicles
        on each road, refreshed now: vehicles may have changed road since
        the last step (a closure reroute, a fleet being added), and planning
        with the old count once kept a vehicle on a detour because two cars
        that had already left a road were still counted on it. With
        smoothing it is the running average, updated once per step.
        """

        if self.policy.smoothing_seconds <= 1:
            live = self.simulation.get_traffic_density()
            self.recent_traffic.clear()
            self.recent_traffic.update(live)
            self._slow_cache_key = None

        return self.recent_traffic

    def joining_speed_factor(self, traffic, own_segment=None):
        """Speed factor a vehicle would get if it joined each road.

        traffic maps road -> vehicles (a recent average may be fractional;
        it is rounded to whole vehicles, which the speed rules work with).
        own_segment is the road the vehicle is on: it is already counted
        in `traffic` there, so it must not be counted twice.
        """

        lookup = self._slow_roads(traffic).get

        if own_segment is None:
            return lambda segment: lookup(segment, 1.0)

        own_factor = self._joining_factor(
            own_segment, max(0, traffic.get(own_segment, 0) - 1)
        )

        def factor(segment):
            if segment == own_segment:
                return own_factor
            return lookup(segment, 1.0)

        return factor

    def _own_segment(self, vehicle):
        if vehicle is None or not self._active(vehicle):
            return None

        route = vehicle.route
        return self.simulation.traffic_key(
            route[vehicle.position], route[vehicle.position + 1]
        )

    def _obeys_limits(self, vehicle):
        """Whether searches for this vehicle obey speed limits (osm model,
        and not an emergency vehicle; None = a new car)."""

        if not self.road_model.has_limits:
            return False
        return vehicle is None or vehicle.obeys_speed_limits

    def plan(self, start, destination, speed, traffic=None, vehicle=None):
        """Fastest route under current traffic, accidents and closures.

        Pass the vehicle when re-planning one already on the road, so its
        own presence is not counted as traffic in its way (and so an
        emergency vehicle is planned without speed limits).
        """

        if traffic is None:
            traffic = self.recent_traffic

        started = time.perf_counter()

        result = self.network.fastest_path(
            start,
            destination,
            speed,
            speed_factor=self.joining_speed_factor(
                traffic, self._own_segment(vehicle)
            ),
            blocked_segments=self.simulation.get_blocked_segments(),
            directed=self.road_model.directed,
            limits=self._obeys_limits(vehicle),
        )

        self.stats["searches"] += 1
        self.stats["search_seconds"] += time.perf_counter() - started

        return result

    def route_finder(self, traffic=None):
        """Search in the shape rerouting.reroute_vehicle expects (metres)."""

        if traffic is None:
            traffic = self.planning_traffic()

        def find(vehicle, start, destination):
            path, _, explored = self.plan(
                start, destination, vehicle.speed, traffic, vehicle
            )
            if path:
                # Called before the route is replaced, so vehicle.route is
                # still the old one
                self._record_avoided(vehicle, path)
            metres = self.network.path_length(path) if path else math.inf
            return path, metres, explored

        return find

    # ------------------------------------------------------------
    # Fleets and surges
    # ------------------------------------------------------------

    def spawn_vehicles(
        self,
        count,
        generator=None,
        origin_zone=None,
        destination_zone=None,
        speed=None,
    ):
        """Add `count` vehicles with demand-model trips and fastest routes.

        speed defaults to car_speed() (the speed limits decide in the osm
        traffic model, DEFAULT_SPEED otherwise).

        Every new vehicle is routed with the same traffic snapshot (taken
        once, before any of them are added), which keeps spawning a large
        fleet fast; they spread out over the following seconds.
        """

        generator = generator or self.random
        speed = speed if speed is not None else car_speed(self.simulation)
        traffic = self.planning_traffic()
        started = time.perf_counter()

        spawned = []
        failed = 0

        for _ in range(count):
            trip = self.demand.random_trip(generator, origin_zone, destination_zone)

            if trip is None:
                failed += 1
                continue

            path, _, _ = self.plan(trip[0], trip[1], speed, traffic)

            if not path:
                failed += 1      # e.g. closed roads cut the trip off
                continue

            vehicle = self.simulation.add_vehicle(self.network.points(path), speed)
            vehicle.origin = trip[0]
            vehicle.destination = trip[1]
            spawned.append(vehicle)

        return {
            "requested": count,
            "spawned": len(spawned),
            "failed": failed,
            "seconds": time.perf_counter() - started,
            "vehicles": spawned,
        }

    def add_surge(self, zone, direction="inbound", vehicles_per_minute=60, duration=900):
        surge = Surge(
            zone, direction, vehicles_per_minute, self.simulation.time, duration
        )

        # Fail now, not on the first step, if the zone has no junctions
        if not self.demand.nodes_in(zone):
            raise ValueError("No usable road junction inside the zone")

        self.surges.append(surge)
        return surge

    def _run_surges(self):
        now = self.simulation.time

        for surge in self.surges:

            if not surge.active(now):
                continue

            surge.due += surge.vehicles_per_minute / 60
            count = int(surge.due)

            if count == 0:
                continue

            surge.due -= count

            zones = (
                {"destination_zone": surge.zone}
                if surge.direction == "inbound"
                else {"origin_zone": surge.zone}
            )

            outcome = self.spawn_vehicles(count, **zones)
            surge.spawned += outcome["spawned"]
            surge.failed += outcome["failed"]

    # ------------------------------------------------------------
    # Events: accidents, closures, reopenings
    # ------------------------------------------------------------

    def _active(self, vehicle):
        return not vehicle.finished and vehicle.position < len(vehicle.route) - 1

    def _uses_segment(self, vehicle, segment):
        route = vehicle.route
        return any(
            rerouting.segment_key(route[i], route[i + 1]) == segment
            for i in range(vehicle.position, len(route) - 1)
        )

    def _remaining_seconds(self, vehicle, nodes, factor):
        """Seconds to drive the junctions `nodes` (infinite if a road is closed)."""

        blocked = self.simulation.get_blocked_segments()

        if any(
            self.network.segment[(a, b)] in blocked
            for a, b in zip(nodes, nodes[1:])
        ):
            return math.inf

        return self.network.path_seconds(
            nodes,
            vehicle.speed,
            factor,
            directed=self.road_model.directed,
            limits=self._obeys_limits(vehicle),
        )

    def _record_avoided(self, vehicle, new_nodes):
        """Remember the closed or accident roads the vehicle just routed round.

        These are roads on its old remaining route that are closed or have
        an accident now and are not on its new route. When one of them
        reopens or clears, this vehicle is re-checked at once (see
        process_events).
        """

        trouble = set(self.simulation.get_blocked_segments()) | self.simulation.incident_segments()

        if not trouble:
            return

        route = vehicle.route
        old = {
            rerouting.segment_key(route[i], route[i + 1])
            for i in range(vehicle.position, len(route) - 1)
        }
        new = {
            self.network.segment[(a, b)]
            for a, b in zip(new_nodes, new_nodes[1:])
        }

        avoided = (old & trouble) - new

        if avoided:
            if not hasattr(vehicle, "avoided_roads"):
                vehicle.avoided_roads = set()
            vehicle.avoided_roads |= avoided

    def process_events(self):
        """Flag vehicles to re-check because the road network changed.

        Returns the number of vehicles flagged. Events are read from
        Simulation.events, so it does not matter whether a change came
        from the API, a step, or a test.
        """

        events = self.simulation.events[self._events_seen:]
        self._events_seen = len(self.simulation.events)

        flagged = 0

        for event in events:
            segment = event["segment"]

            for index, vehicle in enumerate(self.simulation.vehicles):

                if not self._active(vehicle):
                    continue

                worse = event["type"] in ("accident", "road_closed", "flood")

                if index in self._urgent and (worse or self._urgent[index] is None):
                    # Already flagged in a way that covers this event
                    if worse:
                        self._urgent[index] = None
                    continue

                if worse:
                    # Only vehicles that were going to use that road
                    affected = self._uses_segment(vehicle, segment)
                else:
                    # A road got better: the vehicles that changed route to
                    # avoid it. (Anyone else who might now gain finds it at
                    # their next routine check, at most check_interval
                    # seconds later. Re-checking every vehicle that could in
                    # theory gain flagged most of a 2,000-vehicle fleet, and
                    # their urgent checks skip the cooldown.)
                    avoided = getattr(vehicle, "avoided_roads", None)
                    affected = bool(avoided) and segment in avoided

                    if affected:
                        avoided.discard(segment)

                if affected:
                    if worse:
                        self._urgent[index] = None
                    else:
                        self._urgent.setdefault(index, set()).add(segment)
                    flagged += 1

        return flagged

    # ------------------------------------------------------------
    # Dynamic rerouting
    # ------------------------------------------------------------

    def _remaining_nodes(self, vehicle):
        """(prefix points, junctions still to drive) or (None, None).

        A vehicle part-way along a road must finish it, so its remaining
        trip starts at the next junction (the same rule closures use).
        """

        route = vehicle.route
        node_at = self.network.node_at
        start = vehicle.position

        if vehicle.distance_on_segment > 0 or rerouting.point_key(route[start]) not in node_at:
            start += 1
            while start < len(route) and rerouting.point_key(route[start]) not in node_at:
                start += 1

        if start >= len(route) - 1:
            return None, None

        nodes = [node_at.get(rerouting.point_key(point)) for point in route[start:]]

        if any(node is None for node in nodes):
            return None, None

        prefix = [vehicle.get_current_location()] if start > vehicle.position else []
        return prefix, nodes

    def consider(self, vehicle, traffic, urgent=False, must_use=None):
        """Re-plan one vehicle. Returns True if it switched route.

        must_use: roads that just got better; if given, the vehicle only
        switches to a route that uses one of them (it is being re-checked
        because of that road, outside its routine schedule and cooldown,
        so other gains wait for its next routine check).
        """

        prefix, nodes = self._remaining_nodes(vehicle)

        if nodes is None:
            return False

        start, destination = nodes[0], nodes[-1]
        factor = self.joining_speed_factor(traffic, self._own_segment(vehicle))
        current = self._remaining_seconds(vehicle, nodes, factor)

        needed = self.policy.min_saving_seconds

        # No route can beat the free-flow lower bound (landmarks + straight
        # line, at top speed), so if even that would not save enough, do not
        # search at all
        best_possible = self.network.lower_bound_seconds(
            start, destination, vehicle.speed, self._obeys_limits(vehicle)
        )

        if (
            current - best_possible < needed
            or current - best_possible < self.policy.min_saving_fraction * current
        ):
            self.stats["skipped_no_possible_saving"] += 1
            return False

        batched = self._batched_route(vehicle, prefix, start, destination, traffic)

        if batched is not None:
            path, seconds = batched
        else:
            path, seconds, _ = self.plan(
                start, destination, vehicle.speed, traffic, vehicle
            )

        if not path:
            return False      # nothing better: keep the route (and wait)

        saving = current - seconds

        if saving < needed or saving < self.policy.min_saving_fraction * current:
            return False

        if must_use and not any(
            self.network.segment[(a, b)] in must_use
            for a, b in zip(path, path[1:])
        ):
            return False

        self._record_avoided(vehicle, path)
        vehicle.set_route(prefix + self.network.points(path))
        vehicle.reroutes += 1
        self.stats["reroutes"] += 1
        return True

    def reroute_pass(self, traffic=None, urgent_only=False):
        """Routine and urgent checks for this second. Returns switches made.

        urgent_only=True handles only vehicles flagged by events (step()
        does this before moving anyone, so they react at once).
        """

        if not self.policy.enabled:
            self._urgent.clear()
            return 0

        if traffic is None:
            traffic = self.planning_traffic()

        now = self.simulation.time
        interval = self.policy.check_interval
        switched = 0

        due = []

        for index, vehicle in enumerate(self.simulation.vehicles):

            if not self._active(vehicle):
                continue

            urgent = index in self._urgent

            if urgent_only and not urgent:
                continue

            if not urgent:
                # Staggered: vehicle i is checked when (now + i) is a
                # multiple of the interval
                if (now + index) % interval != 0:
                    continue

                last = self._last_switch.get(index)
                if last is not None and now - last < self.policy.cooldown_seconds:
                    continue

            due.append((index, vehicle, urgent))

        self._plan_batches(due)

        for index, vehicle, urgent in due:

            if urgent:
                self.stats["urgent_checks"] += 1
            else:
                self.stats["routine_checks"] += 1

            must_use = self._urgent.get(index) if urgent else None

            if self.consider(vehicle, traffic, urgent, must_use):
                self._last_switch[index] = now
                switched += 1

        self._urgent.clear()
        self._batch_groups = {}
        self._batch_trees = {}
        return switched

    # ------------------------------------------------------------
    # Batched rerouting
    # ------------------------------------------------------------

    # A shared backward search costs about as much as 2 to 3 ordinary ones,
    # so only destinations with at least this many vehicles due share one
    BATCH_MIN = 3

    def _batch_key(self, vehicle, destination):
        return (destination, vehicle.speed, self._obeys_limits(vehicle))

    def _plan_batches(self, due):
        """Count, for this pass, the vehicles due a check per destination
        (and speed and limits) that can share a search (see _batched_route)."""

        self._batch_groups = {}
        self._batch_trees = {}

        # Exact only with per-direction traffic (the osm model): see below
        if not self.road_model.directed:
            return

        groups = {}
        for _, vehicle, _ in due:
            prefix, nodes = self._remaining_nodes(vehicle)
            if nodes is None or not prefix:
                continue
            groups.setdefault(self._batch_key(vehicle, nodes[-1]), set()).add(nodes[0])

        self._batch_groups = {
            key: starts for key, starts in groups.items() if len(starts) >= self.BATCH_MIN
        }

    def _batched_route(self, vehicle, prefix, start, destination, traffic):
        """(path, seconds) from one backward search shared by every vehicle
        due a check this pass with the same destination, or None to search
        on its own.

        Why it is exact: a vehicle's own road is the only one whose cost is
        different for it (it is not traffic in its own way). In the osm
        model traffic is counted per direction, so its own road is the one
        it is driving now, towards `start`; a fastest route FROM `start`
        never uses it (that would pass `start` twice). Vehicles exactly at
        a junction (no prefix) could leave along their own road, so they
        search on their own; so does everything in the simple model, where
        both directions share one count.
        """

        if not prefix:
            return None

        key = self._batch_key(vehicle, destination)
        starts = self._batch_groups.get(key)
        if starts is None:
            return None

        tree = self._batch_trees.get(key)
        if tree is None:
            began = time.perf_counter()
            tree = self.network.fastest_tree_to(
                destination,
                vehicle.speed,
                speed_factor=self.joining_speed_factor(traffic),
                blocked_segments=self.simulation.get_blocked_segments(),
                directed=self.road_model.directed,
                limits=self._obeys_limits(vehicle),
                targets=starts,
            )
            self._batch_trees[key] = tree
            self.stats["batched_searches"] += 1
            self.stats["search_seconds"] += time.perf_counter() - began

        seconds, next_junction = tree
        if start not in seconds:
            return [], math.inf

        path = [start]
        while path[-1] != destination:
            path.append(next_junction[path[-1]])

        self.stats["batched_vehicles"] += 1
        return path, seconds[start]

    # ------------------------------------------------------------
    # One second of simulated time
    # ------------------------------------------------------------

    def step(self):
        """One simulated second. Returns how many vehicles changed route.

        1. Vehicles flagged by changes since the last step (for example an
           accident or reopened road from the API) re-plan BEFORE moving,
           so they react at the moment of the change. Re-planning after
           moving would let a vehicle commit to the first road of its old
           route, which can remove the benefit of switching.
        2. Surges add their vehicles.
        3. Everyone moves (Simulation.step; accidents may clear).
        4. Changes during the move are read, and routine and urgent checks
           run.
        """

        switched = 0

        if self.process_events() or self._urgent:
            switched += self.reroute_pass(urgent_only=True)

        self._run_surges()
        self.simulation.step()
        # Emergency vehicles home, queued emergencies answered (realistic
        # emergency rules only; services.py)
        self.dispatched_from_queue = dispatch.after_step(self.simulation)
        self.update_recent_traffic()
        self.process_events()
        return switched + self.reroute_pass()

    def describe(self):
        now = self.simulation.time
        active = sum(1 for v in self.simulation.vehicles if self._active(v))

        return {
            "simulation_time": now,
            "vehicles": len(self.simulation.vehicles),
            "active_vehicles": active,
            "policy": self.policy.describe(),
            "surges": [surge.describe(now) for surge in self.surges],
            "accidents": [
                accident.describe(now)
                for accident in self.simulation.accidents.values()
            ],
            "closed_roads": len(self.simulation.get_blocked_segments()),
            "rerouting": dict(self.stats),
        }


__all__ = ["ReroutePolicy", "Surge", "TrafficManager", "Zone"]
