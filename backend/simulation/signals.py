"""Traffic signals at chosen junctions, for every traffic model.

Signals are an extra layer on the existing simulation, not a separate one:
the road graph, routing, the three traffic models (simple, osm, ca),
closures, accidents and emergencies are unchanged. A Simulation with
`signals` set asks them, at each signalised junction, whether a vehicle may
cross from the road it is on to the next road of its route:

  * simple and osm (Vehicle.move): a vehicle that may not cross stops at
    the end of its road (the stop line) and waits there; the time counts as
    delay, and the vehicles waiting are the queue.
  * ca (cellular.py): a red light is a wall at the end of the approach lane,
    like a closed road ahead, so a real queue of stopped cars forms behind
    the first one, cell by cell.

Junctions and movements
-----------------------

A signalised junction has approaches (roads arriving at it) and exits
(roads leaving it). A movement is approach -> exit (no U-turns). Movements
are compared by the compass bearing of their roads, with traffic driving
on the left (UK): seen from above, a movement is a chord across the
junction from its entry lane (just clockwise of its approach) to its exit
lane (just anticlockwise of its exit). Two movements from different
approaches CONFLICT if their chords cross or they use the same exit; so a
left turn and the opposite straight-ahead do not conflict, but a right turn
and the oncoming straight-ahead do.

Phases group approaches whose movements are all mutually compatible
(greedy, in bearing order). Only one phase is green at a time, so no two
conflicting movements are ever allowed at once. Most Norwich junctions have
right turns, so most end up with one approach per phase ("split" phasing).

Queues leave at the saturation flow: in the simple and osm models (where a
vehicle is a point and the queue stands at the stop line) at most one
vehicle per lane every SATURATION_HEADWAY seconds crosses on green, the
first at once. In the ca model the cars accelerate away one after another
by the automaton's own rules, so no limit is added.

Each phase change goes green -> amber -> all-red -> next green, the UK
sequence (red-and-amber before green is treated as red). Amber is a stop
signal here: no new vehicle enters on amber. A vehicle may also not enter
if the exit road is already full (osm: at its capacity; ca: no free cells),
so a queue cannot back up into the junction.

Controllers
-----------

  fixed     each phase gets `green` seconds in turn, whatever the traffic,
            then amber and all-red. Each junction starts at its own offset
            in the cycle (from its node id) so they are not all in step.
            There is no coordination between junctions.
  adaptive  every second of green, after `min_green`: end the green if the
            phase has nobody queued or approaching while another phase has,
            or at `max_green`, or if another phase with demand has waited
            `max_wait` seconds (no approach can be starved). The next phase
            is the one waiting longest past `max_wait`, otherwise the one
            with the most demand (vehicles queued + half those approaching
            within `detection_metres`), ties broken in cycle order. With no
            demand anywhere it rests on the current green. Deterministic.

Emergency priority (either controller, `priority=True`): an emergency
vehicle on a call within `priority_metres` of a signalised junction on its
route requests the phase that serves its approach. If that phase is green it
is held (up to `priority_hold` extra seconds); otherwise the current green
ends as soon as it has run `PRIORITY_MIN_GREEN` seconds and the requested
phase comes next, always through amber and all-red, never straight from one
green to another.

Signal priority is separate from emergency ROUTING (dispatch.py chooses the
vehicle and its route) and from how traffic behaves around an emergency
vehicle (services.py: right of way). Without priority an emergency vehicle
at a red light waits `emergency_red_wait` seconds and then crosses with
caution, as UK law allows; ordinary vehicles never cross on red.

The defaults are experimental values, not Norwich's real timings (which are
not public): 20 s of green per phase for fixed time, 7 to 45 s adaptive,
3 s amber and 2 s all-red (the UK amber and a typical all-red).
"""

import math
from dataclasses import dataclass


NONE, FIXED, ADAPTIVE = "none", "fixed", "adaptive"
CONTROLLERS = (NONE, FIXED, ADAPTIVE)
GREEN, AMBER, ALL_RED = "green", "amber", "all_red"

# Degrees either side of a road's bearing its lanes sit at (left-hand
# traffic: in just clockwise of the road, out just anticlockwise)
LANE_OFFSET = 12.0

# Shortest green an emergency request can cut short
PRIORITY_MIN_GREEN = 5

# Seconds between vehicles leaving a queue on green, per lane: 1,800
# vehicles an hour per lane, the usual saturation flow (simple and osm)
SATURATION_HEADWAY = 2.0


@dataclass(frozen=True)
class SignalTiming:
    green: float = 20.0
    min_green: float = 7.0
    max_green: float = 45.0
    amber: float = 3.0
    all_red: float = 2.0
    detection_metres: float = 60.0
    max_wait: float = 120.0
    priority_metres: float = 250.0
    priority_hold: float = 30.0
    emergency_red_wait: float = 5.0

    def __post_init__(self):
        if not 0 < self.min_green <= self.max_green:
            raise ValueError("min_green must be positive and at most max_green")
        if self.green <= 0 or self.amber < 0 or self.all_red < 0:
            raise ValueError("green must be positive; amber and all_red not negative")
        if self.max_wait < self.max_green:
            raise ValueError("max_wait must be at least max_green")


def point(p):
    return (p["latitude"], p["longitude"])


def bearing(origin, target):
    """Compass bearing (degrees clockwise from north) from origin to target,
    both (lat, lon)."""
    dy = target[0] - origin[0]
    dx = (target[1] - origin[1]) * math.cos(math.radians(origin[0]))
    return math.degrees(math.atan2(dx, dy)) % 360


def angle_between(a, b):
    difference = abs(a - b) % 360
    return min(difference, 360 - difference)


def _within(x, start, end):
    """Whether bearing x lies strictly on the clockwise arc start -> end."""
    return 0 < (x - start) % 360 < (end - start) % 360


def movements_conflict(first, second):
    """first, second: (approach id, approach bearing, exit id, exit bearing).
    Movements from the same approach never conflict; others do if they
    share an exit or their paths cross (see the module docstring)."""

    if first[0] == second[0]:
        return False
    if first[2] == second[2]:
        return True
    a, b = first[1] + LANE_OFFSET, first[3] - LANE_OFFSET
    c, d = second[1] + LANE_OFFSET, second[3] - LANE_OFFSET
    return _within(c, a, b) != _within(d, a, b)


def group_phases(movements):
    """movements: one list of movements per approach. Phases: lists of
    approach indexes, every movement compatible with every other in its
    phase."""

    phases = []
    for approach, own in enumerate(movements):
        for phase in phases:
            if all(
                not movements_conflict(m1, m2)
                for other in phase for m1 in own for m2 in movements[other]
            ):
                phase.append(approach)
                break
        else:
            phases.append([approach])
    return phases


class Junction:
    """One signalised junction: its approaches, phases and signal state."""

    def __init__(self, node, position, upstream, downstream, lanes=None):
        self.node = node
        self.position = position
        # Approaches: the junctions traffic arrives from; exits: where it leaves to
        self.upstream = list(upstream)
        self.downstream = list(downstream)
        self.approach_bearings = [bearing(position, p) for p in self.upstream]
        exit_bearings = [bearing(position, p) for p in self.downstream]

        self.movements = [
            [
                (a, self.approach_bearings[a], e, exit_bearings[e])
                for e, target in enumerate(self.downstream)
                if target != source
            ]
            for a, source in enumerate(self.upstream)
        ]
        self.phases = group_phases(self.movements)
        # Lanes on each approach, and how many vehicles may still leave it
        # this second at the saturation flow
        self.lanes = list(lanes) if lanes else [1] * len(self.upstream)
        self.allowance = [0.0] * len(self.upstream)
        self.phase_of = {a: p for p, phase in enumerate(self.phases) for a in phase}

        # Signal state
        self.phase = 0
        self.stage = GREEN
        self.timer = 0.0           # seconds in the current stage
        self.next_phase = None
        self.last_green = [0.0] * len(self.phases)   # when each phase last had green
        self.priority_held = 0.0

        # This second's observations, per approach
        self.queue = [0] * len(self.upstream)
        self.approaching = [0] * len(self.upstream)

        # Totals
        self.crossings = 0
        self.max_queue = 0
        self.queue_seconds = 0
        self.switches = 0
        self.priority_requests = 0

        self._approach_cache = {}

    def approach_of(self, previous):
        """The approach a vehicle coming from `previous` (lat, lon) is on:
        the one whose road points the same way (a rerouted vehicle's route
        can start mid-road, not at the upstream junction)."""
        found = self._approach_cache.get(previous)
        if found is None:
            if previous in self.upstream:
                found = self.upstream.index(previous)
            else:
                direction = bearing(self.position, previous)
                found = min(
                    range(len(self.upstream)),
                    key=lambda a: angle_between(direction, self.approach_bearings[a]),
                )
            self._approach_cache[previous] = found
        return found

    def is_green(self, approach):
        return self.stage == GREEN and self.phase_of.get(approach) == self.phase

    def light(self, approach):
        """What a driver on this approach sees: green, amber or red."""
        if self.phase_of.get(approach) != self.phase:
            return "red"
        return {GREEN: "green", AMBER: "amber", ALL_RED: "red"}[self.stage]

    def demand(self, phase):
        return sum(self.queue[a] + 0.5 * self.approaching[a] for a in self.phases[phase])

    def describe(self):
        return {
            "node": self.node,
            "latitude": self.position[0],
            "longitude": self.position[1],
            "phase": self.phase,
            "phases": len(self.phases),
            "stage": self.stage,
            "approaches": [
                {
                    "bearing": round(self.approach_bearings[a], 1),
                    "light": self.light(a),
                    "queue": self.queue[a],
                }
                for a in range(len(self.upstream))
            ],
        }


@dataclass
class _Totals:
    priority_requests: int = 0
    switches: int = 0
    held_vehicle_seconds: int = 0


class SignalSystem:
    """The signals of one simulation (see the module docstring)."""

    def __init__(self, network, nodes, controller=FIXED, priority=False, timing=None):
        if controller not in (FIXED, ADAPTIVE):
            raise ValueError(f"controller must be {FIXED} or {ADAPTIVE}")
        self.controller = controller
        self.priority = priority
        self.timing = timing or SignalTiming()
        self.network = network

        upstream, downstream = {}, {}
        for (a, b) in network.length:
            upstream.setdefault(b, set()).add(a)
            downstream.setdefault(a, set()).add(b)

        self.junctions = []
        for node in nodes:
            if node not in network.position or len(upstream.get(node, ())) < 2:
                continue
            ups = sorted(upstream[node], key=str)
            osm = network.osm_roads()
            junction = Junction(
                node, network.position[node],
                [network.position[n] for n in ups],
                [network.position[n] for n in sorted(downstream.get(node, ()), key=str)],
                lanes=[osm.lanes.get((network.position[n], network.position[node]), 1) for n in ups],
            )
            self.junctions.append(junction)
        self.at_point = {j.position: j for j in self.junctions}

        if controller == FIXED:
            for junction in self.junctions:
                self._fixed_offset(junction)

        self.time = 0
        self._heading = {}   # id(vehicle) -> the junction it is heading for
        self._red_wait = {}  # id(vehicle) -> seconds held at its current red
        self._box_rule = None
        self._cellular = False
        self.totals = _Totals()

    # ---- fixed time --------------------------------------------------

    def _phase_seconds(self):
        t = self.timing
        return t.green + t.amber + t.all_red

    def _fixed_offset(self, junction):
        """Start each junction at its own point in its cycle."""
        cycle = self._phase_seconds() * len(junction.phases)
        offset = int(junction.node) % int(cycle) if str(junction.node).lstrip("-").isdigit() else 0
        junction.phase = int(offset // self._phase_seconds())
        junction.timer = float(offset % self._phase_seconds())
        if junction.timer >= self.timing.green:
            junction.timer = 0.0

    # ---- each second ---------------------------------------------------

    def step(self, simulation, traffic):
        """Observe the traffic, then advance every junction one second.
        Called by Simulation.step before anything moves."""

        self.time += 1
        self._observe(simulation)
        requests = self._requests if self.priority else {}

        for junction in self.junctions:
            self._advance(junction, requests.get(junction))
            # Saturation flow: on green, half a vehicle per lane a second
            # (at most one per lane waiting to go)
            for a, lanes in enumerate(junction.lanes):
                if junction.is_green(a):
                    junction.allowance[a] = min(
                        lanes, junction.allowance[a] + lanes / SATURATION_HEADWAY
                    )
                else:
                    junction.allowance[a] = 0.0

        # Exit roads that are full (the "yellow box" rule) for this second
        model = simulation.road_model
        self._cellular = model.cellular
        if model.cellular or not hasattr(model, "_road_capacity"):
            self._box_rule = None
        else:
            capacity = model._road_capacity
            self._box_rule = lambda key: traffic.get(key, 0) >= capacity(key)

    def _observe(self, simulation):
        for junction in self.junctions:
            junction.queue = [0] * len(junction.upstream)
            junction.approaching = [0] * len(junction.upstream)
        self._requests = {}
        heading = self._heading
        cellular = simulation.road_model.cellular
        detection = self.timing.detection_metres

        for vehicle in simulation.vehicles:
            key = id(vehicle)
            route = vehicle.route
            position = vehicle.position
            if vehicle.finished or position >= len(route) - 1:
                heading.pop(key, None)
                continue

            junction = self.at_point.get(point(route[position + 1]))
            previous = heading.get(key)
            if previous is not None and previous is not junction:
                # It went through the junction it was heading for
                previous.crossings += 1
                self._red_wait.pop(key, None)
            # Its next junction is its destination (not crossed), or unsignalised
            if junction is None or position >= len(route) - 2:
                heading.pop(key, None)
                continue
            heading[key] = junction

            approach = junction.approach_of(point(route[position]))
            held = getattr(vehicle, "held_at_signal", False)
            remaining = vehicle.segment_distance(route[position], route[position + 1]) - vehicle.distance_on_segment

            if held or (cellular and vehicle.speed_factor == 0 and remaining <= 2 * detection):
                junction.queue[approach] += 1
                vehicle.signal_wait_seconds = getattr(vehicle, "signal_wait_seconds", 0) + 1
                self.totals.held_vehicle_seconds += 1
                if held:
                    self._red_wait[key] = self._red_wait.get(key, 0) + 1
            elif remaining <= detection:
                junction.approaching[approach] += 1

            if (
                self.priority and simulation.on_call(vehicle)
                and remaining <= self.timing.priority_metres
                and junction not in self._requests
            ):
                self._requests[junction] = approach

        for junction in self.junctions:
            total = sum(junction.queue)
            junction.queue_seconds += total
            junction.max_queue = max(junction.max_queue, total)

    def _advance(self, junction, request):
        t = self.timing
        junction.timer += 1

        if junction.stage == AMBER:
            if request is not None:
                junction.next_phase = junction.phase_of[request]
            if junction.timer >= t.amber:
                junction.stage, junction.timer = ALL_RED, 0.0
            return

        if junction.stage == ALL_RED:
            if request is not None:
                junction.next_phase = junction.phase_of[request]
            if junction.timer >= t.all_red:
                junction.phase = junction.next_phase
                junction.stage, junction.timer = GREEN, 0.0
                junction.next_phase = None
                junction.priority_held = 0.0
            return

        # Green
        if request is not None:
            wanted = junction.phase_of[request]
            if wanted == junction.phase:
                # Hold the green for the emergency vehicle (up to a limit)
                if junction.priority_held < t.priority_hold:
                    junction.priority_held += 1
                    if junction.priority_held == 1:
                        junction.priority_requests += 1
                        self.totals.priority_requests += 1
                    return
            elif junction.timer >= PRIORITY_MIN_GREEN:
                junction.priority_requests += 1
                self.totals.priority_requests += 1
                self._end_green(junction, wanted)
                return

        if self.controller == FIXED:
            if junction.timer >= t.green and len(junction.phases) > 1:
                self._end_green(junction, (junction.phase + 1) % len(junction.phases))
            return

        nxt = self._adaptive_next(junction)
        if nxt is not None:
            self._end_green(junction, nxt)

    def _end_green(self, junction, next_phase):
        junction.last_green[junction.phase] = self.time
        junction.stage, junction.timer = AMBER, 0.0
        junction.next_phase = next_phase
        junction.switches += 1
        self.totals.switches += 1

    def _adaptive_next(self, junction):
        """The phase to switch to now, or None to keep the green."""
        t = self.timing
        current = junction.phase
        count = len(junction.phases)
        if count < 2 or junction.timer < t.min_green:
            return None

        others = [p for p in range(count) if p != current and junction.demand(p) > 0]
        if not others:
            return None   # rest on green

        waited = {p: self.time - junction.last_green[p] for p in others}
        starved = [p for p in others if waited[p] >= t.max_wait]
        if not (starved or junction.timer >= t.max_green or junction.demand(current) == 0):
            return None

        if starved:
            return max(starved, key=lambda p: (waited[p], -((p - current) % count)))
        return max(others, key=lambda p: (junction.demand(p), -((p - current) % count)))

    # ---- asked by the movement code -----------------------------------

    def may_cross(self, vehicle, previous, at, following):
        """Whether `vehicle` may go through the junction at `at` from the
        road previous -> at onto at -> following (all route points)."""

        junction = self.at_point.get(point(at))
        if junction is None:
            return True
        approach = junction.approach_of(point(previous))
        on_call = vehicle.on_emergency and vehicle.vehicle_type != "normal"
        if not junction.is_green(approach):
            # An emergency vehicle on a call crosses a red light with
            # caution after stopping for emergency_red_wait seconds
            if not (on_call and self._red_wait.get(id(vehicle), 0) >= self.timing.emergency_red_wait):
                return False
        if self._box_rule is not None and self._box_rule((point(at), point(following))):
            return False
        if not self._cellular and not on_call:
            # The queue leaves at the saturation flow
            if junction.allowance[approach] < 1:
                return False
            junction.allowance[approach] -= 1
        return True

    # ---- reporting -----------------------------------------------------

    def describe(self):
        return [junction.describe() for junction in self.junctions]

    def summary(self, vehicles=()):
        """Totals for a finished run: per-junction throughput and queues,
        and each trip's time waiting at signals."""
        seconds = max(1, self.time)
        waits = [getattr(v, "signal_wait_seconds", 0) for v in vehicles]
        return {
            "controller": self.controller,
            "priority": self.priority,
            "junctions": len(self.junctions),
            "crossings": sum(j.crossings for j in self.junctions),
            "crossings_per_junction_hour": (
                sum(j.crossings for j in self.junctions) / len(self.junctions) * 3600 / seconds
                if self.junctions else 0.0
            ),
            "mean_queue": sum(j.queue_seconds for j in self.junctions) / len(self.junctions) / seconds
            if self.junctions else 0.0,
            "max_queue": max((j.max_queue for j in self.junctions), default=0),
            "mean_wait_seconds": sum(waits) / len(waits) if waits else 0.0,
            "max_wait_seconds": max(waits, default=0),
            "switches": self.totals.switches,
            "priority_requests": self.totals.priority_requests,
        }


def make_signals(network, nodes, controller=FIXED, priority=False, timing=None):
    """A SignalSystem, or None for controller "none"."""
    if controller == NONE:
        return None
    return SignalSystem(network, nodes, controller, priority, timing)
