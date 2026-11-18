"""Cellular-automaton traffic: the Nagel-Schreckenberg (NaSch) model.

An experimental third traffic model, "ca", next to "simple" and "osm"
(road_model.py). The road graph, routing (A* / travel-time search),
closures, accidents, rerouting and emergencies all stay as they are; what
changes is how ordinary cars move along each road.

The NaSch model
---------------

Each lane of each directed road is a row of cells. A car occupies
`vehicle_cells` consecutive cells and has an integer speed v (cells per
time step). Every time step, every car applies four rules, all at once
(parallel update: every car decides from where everyone was at the start
of the step):

  1. Acceleration    v = min(v + 1, vmax)
  2. Braking         v = min(v, gap)       gap = empty cells to the car ahead
  3. Randomisation   with probability p, v = max(v - 1, 0)
  4. Movement        the car advances v cells

Rule 2 is what prevents collisions: no car can move further than the empty
cells in front of it. Rule 3 ("imperfect driving") is what makes jams
appear on their own and travel backwards as stop-and-go waves.

Units and defaults (CaParameters)
---------------------------------

  cell_metres        2.5 m. Speeds come in steps of one cell per time step
                     (2.5 m/s = 9 km/h) and acceleration is one cell per
                     step per step (2.5 m/s^2, an ordinary car's).
  vehicle_cells      3: a car takes 7.5 m, a car plus the gap in a standing
                     queue (the same jam spacing as the osm model).
  brake_probability  0.2 per car per step.
  time_step          1 s. Smaller steps (0.5, 0.25 s: 1 / time_step must be
                     a whole number) run several updates per simulated
                     second.
  vmax               each road's speed limit (from OpenStreetMap, as in the
                     osm model) in cells per step, rounded to the nearest
                     cell: at the defaults 30 mph (13.4 m/s) is 5 cells
                     (12.5 m/s), 20 mph is 4 (10 m/s), 40 mph 7 (17.5 m/s).

The classic NaSch set-up (7.5 m cells, one cell per car, vmax 5, so speed
steps of 7.5 m/s) is what the textbook results are stated for; the ring-road
experiments use it (benchmark_cellular.py). Finer cells are used on the
network because 7.5 m/s steps cannot tell a 20 mph street from a 30 mph one.

How it fits the road network (CellularTraffic)
----------------------------------------------

  * Roads: one lattice per directed road the vehicles use, created when the
    first car enters it. Length is the real road length (as the other
    models drive), split into max(1, round(length / cell_metres)) cells.
  * Lanes: OpenStreetMap's lane count gives that many parallel lanes. A car
    picks the lane with the most free space when it enters a road and
    keeps it: there is no lane changing or overtaking.
  * Junctions: a car at the front of its lane looks past the end of the
    road into the next road of its route (and the one after, if that is
    empty). Two cars entering the same lane in the same step are placed one
    at a time in a seeded random order, each into the space left, so they
    cannot collide; there are no signals, give-way rules or turning
    slow-downs.
  * Closures: a car on a closed road does not move (as in the other
    models); a closed next road is a wall at the end of the road.
  * Traffic signals (signals.py): a red light ahead is a wall at the end
    of the approach lane too, so a queue forms behind the first car.
  * Accidents: vmax on the road is multiplied by the accident's speed
    factor (at least one cell per step), and only that share of lanes,
    lanes x capacity factor (at least one), take new cars.
  * Emergency vehicles are not on the lattice: they move as in the osm
    model, at the speed the CA measures on each road (with right of way).
    Ordinary cars do not pull over for them.
  * Feedback: the mean speed of the cars on each road, as a fraction of the
    road's vmax, is smoothed over a few seconds (MEASURE_WEIGHT) and used
    as the road's speed factor (CaRoads.speed_factor). Routing, arrival
    estimates, rerouting and the congestion map read it, so they all see
    the traffic the CA produced.

Everything random comes from one generator seeded from the scenario's seed,
and cars are always processed in the same order, so a run is repeatable.
"""

import math
import random
from dataclasses import dataclass

from backend.simulation.road_model import (
    FLOOR_FACTOR,
    LIGHT_ABOVE,
    MODERATE_ABOVE,
    OSM,
    point,
    undirected,
)
from backend.simulation.vehicle import STOP_LINE_GAP, straight_line_metres


CA = "ca"

# Weight of the newest second in a road's measured speed (an exponential
# moving average over about 1 / MEASURE_WEIGHT seconds)
MEASURE_WEIGHT = 0.3


@dataclass(frozen=True)
class CaParameters:
    cell_metres: float = 2.5
    vehicle_cells: int = 3
    brake_probability: float = 0.2
    time_step: float = 1.0

    def __post_init__(self):
        if not self.cell_metres > 0:
            raise ValueError("cell_metres must be positive")
        if self.vehicle_cells < 1:
            raise ValueError("vehicle_cells must be at least 1")
        if not 0 <= self.brake_probability <= 1:
            raise ValueError("brake_probability must be between 0 and 1")
        steps = 1 / self.time_step if self.time_step > 0 else 0
        if not self.time_step > 0 or abs(steps - round(steps)) > 1e-9:
            raise ValueError("time_step must divide one second (1, 0.5, 0.25, ...)")

    @property
    def steps_per_second(self):
        return round(1 / self.time_step)

    def cells_per_step(self, metres_per_second):
        """A speed in m/s as whole cells per time step (at least 1)."""
        return max(1, round(metres_per_second * self.time_step / self.cell_metres))


# ----------------------------------------------------------------------
# The rules
# ----------------------------------------------------------------------

def accelerate(v, vmax):
    """Rule 1: one cell per step faster, up to vmax."""
    return min(v + 1, vmax)


def brake(v, gap):
    """Rule 2: never further than the empty cells ahead."""
    return min(v, max(gap, 0))


def randomise(v, slows):
    """Rule 3: one cell slower when this car's random draw says so."""
    return v - 1 if slows and v > 0 else v


def nasch_speed(v, vmax, gap, slows):
    """Rules 1-3 in order: the speed a car moves at this step (rule 4)."""
    return randomise(brake(accelerate(v, vmax), gap), slows)


# ----------------------------------------------------------------------
# Single roads, for the controlled experiments
# ----------------------------------------------------------------------

class RingRoad:
    """A single-lane circular road: the classic NaSch set-up, where the
    number of cars (the density) is fixed and flow can be measured exactly.

    Cars start evenly spaced at speed 0. step() updates every car in
    parallel. vehicle_cells is 1 (the classic model).
    """

    def __init__(self, cells, cars, vmax=5, brake_probability=0.5, seed=0):
        if not 0 < cars <= cells:
            raise ValueError("need between 1 and `cells` cars")

        self.cells = cells
        self.vmax = vmax
        self.p = brake_probability
        self.random = random.Random(seed)
        # Positions in driving order (each car's leader is the next one)
        self.position = [math.floor(i * cells / cars) for i in range(cars)]
        self.speed = [0] * cars
        self.time = 0
        # Cars that passed cell 0 (the measuring point), in total
        self.passed = 0

    @property
    def density(self):
        return len(self.position) / self.cells

    def gaps(self):
        n = len(self.position)
        return [
            (self.position[(i + 1) % n] - self.position[i] - 1) % self.cells
            if n > 1 else self.cells - 1
            for i in range(n)
        ]

    def step(self):
        gaps = self.gaps()
        draws = [self.random.random() < self.p for _ in self.position]
        self.speed = [
            nasch_speed(v, self.vmax, gap, slows)
            for v, gap, slows in zip(self.speed, gaps, draws)
        ]

        for i, v in enumerate(self.speed):
            new = self.position[i] + v
            if new >= self.cells:
                self.passed += 1
            self.position[i] = new % self.cells

        self.time += 1

    def occupancy(self):
        """Each cell's car speed, or None for an empty cell (one row of a
        space-time diagram)."""
        row = [None] * self.cells
        for x, v in zip(self.position, self.speed):
            row[x] = v
        return row


class OpenRoad:
    """A single-lane road with cars arriving at the start and leaving at
    the end, and an optional blockage (a closed stretch) for a while.

    A car arrives at cell 0 with probability `inflow` each step when the
    cell is free (cars that find it taken are lost: a source that cannot
    deliver more). Used to watch a queue build behind a closure and clear
    after it reopens.
    """

    def __init__(self, cells, inflow, vmax=5, brake_probability=0.2, seed=0):
        self.cells = cells
        self.inflow = inflow
        self.vmax = vmax
        self.p = brake_probability
        self.random = random.Random(seed)
        self.position = []   # front to back: the first car is furthest along
        self.speed = []
        self.blocked_at = None
        self.time = 0
        self.left = 0

    def step(self):
        draws = [self.random.random() < self.p for _ in self.position]
        new_speed = []

        for i, x in enumerate(self.position):
            if i == 0:
                gap = self.cells   # the end of the road: cars leave
            else:
                gap = self.position[i - 1] - x - 1
            if self.blocked_at is not None and x < self.blocked_at:
                gap = min(gap, self.blocked_at - x - 1)
            new_speed.append(nasch_speed(self.speed[i], self.vmax, gap, draws[i]))

        position = [x + v for x, v in zip(self.position, new_speed)]
        keep = [i for i, x in enumerate(position) if x < self.cells]
        self.left += len(position) - len(keep)
        self.position = [position[i] for i in keep]
        self.speed = [new_speed[i] for i in keep]

        if self.random.random() < self.inflow and (
            not self.position or self.position[-1] > 0
        ):
            self.position.append(0)
            self.speed.append(0)

        self.time += 1

    def queue(self):
        """Cars standing still."""
        return sum(1 for v in self.speed if v == 0)


# ----------------------------------------------------------------------
# The road network
# ----------------------------------------------------------------------

class _Road:
    """One directed road's lattice."""

    __slots__ = ("key", "cells", "cell_length", "length", "lanes", "limit")

    def __init__(self, key, length, lanes, limit, cell_metres):
        self.key = key
        self.length = length
        self.cells = max(1, round(length / cell_metres))
        # Metres one cell stands for on this road (close to cell_metres)
        self.cell_length = length / self.cells if length > 0 else 0.0
        # Each lane: its cars, front first (cars never overtake in a lane)
        self.lanes = [[] for _ in range(max(1, lanes))]
        self.limit = limit


class _Car:
    """A car on the lattice: its road, lane, front cell and speed, and the
    part of its route it is on (route[index] -> route[index + 1])."""

    __slots__ = ("vehicle", "road", "lane", "front", "v", "route", "index", "came_from")

    def __init__(self, vehicle):
        self.vehicle = vehicle
        self.road = None
        self.lane = None
        self.front = 0
        self.v = 0
        self.route = None
        self.index = 0
        # The road it was on before this one (see _space)
        self.came_from = None


class CellularTraffic:
    """NaSch on every road the cars use (see the module docstring)."""

    def __init__(self, roads, parameters=None, seed=0):
        self.osm = roads   # speed limits and lanes (OsmRoads)
        self.parameters = parameters or CaParameters()
        self.random = random.Random(seed)
        self.roads = {}
        self.cars = {}     # id(vehicle) -> _Car
        # Directed key -> smoothed speed factor (see MEASURE_WEIGHT)
        self.measured = {}
        # Directed key -> {"vehicles", "stopped", "occupancy", "speed_factor"}
        # after the last second, for the map and the experiments
        self.road_state = {}

    # -- roads ------------------------------------------------------

    def _road(self, simulation, key):
        road = self.roads.get(key)
        if road is None:
            length = None
            if simulation.road_lengths is not None:
                length = simulation.road_lengths.get(key)
            if length is None:
                length = straight_line_metres(*key)
            road = _Road(
                key, length, self.osm.lanes.get(key, 1), self.osm.limit(key),
                self.parameters.cell_metres,
            )
            self.roads[key] = road
        return road

    def _vmax(self, simulation, road, vehicle):
        cells = self.parameters.cells_per_step(min(road.limit, vehicle.speed))
        accident = simulation.road_incident(undirected(road.key))
        if accident is not None:
            cells = max(1, round(cells * accident.speed_factor))
        return cells

    def _open_lanes(self, simulation, road):
        accident = simulation.road_incident(undirected(road.key))
        if accident is None:
            return road.lanes
        count = max(1, round(len(road.lanes) * accident.capacity_factor))
        return road.lanes[:count]

    def _space(self, lane, came_from):
        """Free cells at the start of a lane, before its last car's rear
        (None: the lane is empty). A car that has just crossed the junction
        may still hang back over the road it came from; it only takes that
        room from cars on that same road."""
        if not lane:
            return None
        tail = lane[-1]
        rear = tail.front - self.parameters.vehicle_cells + 1
        if rear < 0 and tail.came_from is not came_from:
            rear = 0
        return rear

    def _room(self, road, lane, came_from):
        space = self._space(lane, came_from)
        return road.cells if space is None else space

    def _entry_lane(self, simulation, road, came_from):
        """The open lane with the most free space at its start."""
        return max(
            self._open_lanes(simulation, road),
            key=lambda lane: self._room(road, lane, came_from),
        )

    @staticmethod
    def _route_key(route, index):
        return (point(route[index]), point(route[index + 1]))

    # -- keeping cars and their Vehicle in step -----------------------

    def _remove(self, car):
        if car.lane is not None:
            car.lane.remove(car)
        car.road = car.lane = None

    def _write(self, car):
        """Copy the car's place on the lattice into its Vehicle, measured
        along the vehicle's own route segment (which, after a reroute from
        mid-road, is shorter than the road the car is on)."""
        vehicle = car.vehicle
        road = car.road
        vehicle.position = car.index
        route = car.route
        segment = vehicle.segment_distance(route[car.index], route[car.index + 1])
        remaining = road.length - (car.front + 1) * road.cell_length
        # A car in the last cell waits just before the junction, not on it
        # (see STOP_LINE_GAP)
        vehicle.distance_on_segment = max(0.0, min(segment - STOP_LINE_GAP, segment - remaining))

    def _place(self, simulation, car):
        """Put a car that is not on the lattice (new, or moved to a
        different road by its route) onto its route's current road, at its
        position or the nearest free place behind it. Returns False if there
        is no room yet (it waits, and tries again next step)."""

        vehicle = car.vehicle
        index = vehicle.position
        road = self._road(simulation, self._route_key(vehicle.route, index))
        size = self.parameters.vehicle_cells
        wanted = 0
        if road.cell_length:
            wanted = math.ceil(vehicle.distance_on_segment / road.cell_length - 1e-9) - 1
        wanted = min(road.cells - 1, max(0, wanted))

        for lane in sorted(
            self._open_lanes(simulation, road),
            key=lambda lane: -self._room(road, lane, None),
        ):
            ahead = [c for c in lane if c.front > wanted]
            behind = lane[len(ahead):]
            front = wanted
            if ahead:
                front = min(front, ahead[-1].front - size)
            if front < 0 or (behind and behind[0].front > front - size):
                continue
            lane.insert(len(ahead), car)
            car.road, car.lane, car.front, car.v = road, lane, front, 0
            car.route, car.index, car.came_from = vehicle.route, index, None
            self._write(car)
            return True

        return False

    def _sync(self, simulation, car):
        """Bring the lattice up to date with the vehicle's route (it may
        have been rerouted, dispatched or given a new trip). Returns True if
        the car is on the lattice."""

        vehicle = car.vehicle
        route = vehicle.route
        if car.road is not None and car.route is route and car.index == vehicle.position:
            return True

        # A reroute from the middle of a road gives the route
        # [here, next junction, ...]: the car stays where it is, on the
        # road it was driving, which ends at that junction
        if (
            car.road is not None
            and vehicle.position < len(route) - 1
            and car.road.key[1] == point(route[vehicle.position + 1])
        ):
            car.route, car.index = route, vehicle.position
            self._write(car)
            return True

        self._remove(car)
        return self._place(simulation, car)

    # -- one simulated second ---------------------------------------

    def step(self, simulation):
        """Move every ordinary car for one simulated second. Returns the set
        of vehicles handled (Simulation.step leaves them alone)."""

        handled = set()
        cars = []

        for vehicle in simulation.vehicles:
            if vehicle.finished or vehicle.vehicle_type != "normal":
                continue
            handled.add(vehicle)
            vehicle.journey_seconds += 1
            vehicle.held_at_signal = False
            vehicle.last_move_seconds = 1.0
            vehicle.last_free_flow_seconds = 0.0
            car = self.cars.get(id(vehicle))
            if car is None:
                car = self.cars[id(vehicle)] = _Car(vehicle)
            cars.append(car)

        # Cars whose vehicle has finished
        live = {id(car.vehicle) for car in cars}
        for key in [key for key in self.cars if key not in live]:
            self._remove(self.cars.pop(key))

        speeds = {}
        steps = self.parameters.steps_per_second
        for substep in range(steps):
            self._update(simulation, cars, speeds, substep, steps)

        self._measure(cars, speeds)
        return handled

    def _final_road(self, car):
        return car.index >= len(car.route) - 2

    def _update(self, simulation, cars, speeds, substep, steps):
        p = self.parameters
        size = p.vehicle_cells
        draw = self.random.random
        plans = []

        # ---- Rules 1-3, from where everyone is at the start ------------
        for car in cars:
            vehicle = car.vehicle
            if vehicle.finished:
                continue
            if vehicle.position >= len(vehicle.route) - 1:
                self._remove(car)
                self._finish(car, substep, steps, 0.0)
                continue

            on_lattice = self._sync(simulation, car)
            # Drawn for every car every step, so one car's state never
            # shifts another car's random numbers
            slows = draw() < p.brake_probability
            if not on_lattice:
                speeds[car] = (0, 1)
                continue

            road = car.road
            vmax = self._vmax(simulation, road, vehicle)

            if simulation.is_segment_blocked(road.key) or simulation.is_road_closed(
                car.route[car.index], car.route[car.index + 1]
            ):
                car.v = 0
                speeds[car] = (0, vmax)
                continue

            position = car.lane.index(car)
            if position > 0:
                leader = car.lane[position - 1]
                gap, plan = leader.front - size - car.front, None
            else:
                gap, plan = self._look_ahead(simulation, car, vmax)

            car.v = nasch_speed(car.v, vmax, gap, slows)
            if car.v == 0 and plan is not None and plan[-1:] == ["signal"]:
                vehicle.held_at_signal = True
            speeds[car] = (car.v, vmax)
            plans.append((car, [item for item in plan if item != "signal"] if plan else plan))

        # ---- Rule 4: move ----------------------------------------------
        crossing = []
        for car, plan in plans:
            left = car.road.cells - 1 - car.front
            if car.v <= left:
                self._advance(car, car.v)
                if self._final_road(car) and car.front == car.road.cells - 1:
                    self._remove(car)
                    self._finish(car, substep, steps, 1.0 if car.v else 0.0)
                else:
                    self._write(car)
            else:
                crossing.append((car, plan))

        # Cars crossing junctions go one at a time, in a random order, each
        # into the room really left (another may have just taken some)
        self.random.shuffle(crossing)
        for car, plan in crossing:
            self._cross(simulation, car, plan, substep, steps)

    def _look_ahead(self, simulation, car, vmax):
        """Empty cells ahead of the first car in a lane: the rest of its
        road, then the next roads of its route while they are empty, up to
        vmax. Returns (gap, plan): plan lists the (road, lane) it would
        enter, ending with "exit" where the route ends."""

        road = car.road
        gap = road.cells - 1 - car.front
        plan = []
        route, index = car.route, car.index
        came_from = road
        signals = simulation.signals

        while gap < vmax:
            index += 1
            if index >= len(route) - 1:
                plan.append("exit")
                return vmax, plan
            if simulation.is_road_closed(route[index], route[index + 1]):
                break
            if signals is not None and not signals.may_cross(
                car.vehicle, route[index - 1], route[index], route[index + 1]
            ):
                # A red light: the stop line is a wall (marked so the car
                # can be counted as held if it stops there)
                if index == car.index + 1:
                    plan.append("signal")
                break
            nxt = self._road(simulation, self._route_key(route, index))
            lane = self._entry_lane(simulation, nxt, came_from)
            plan.append((nxt, lane))
            space = self._space(lane, came_from)
            if space is not None:
                gap += max(space, 0)
                break
            gap += nxt.cells
            came_from = nxt

        return max(gap, 0), plan

    def _advance(self, car, cells):
        car.front += cells
        self._travel(car.vehicle, cells, car.road)

    def _travel(self, vehicle, cells, road):
        if cells <= 0:
            return
        metres = cells * road.cell_length
        vehicle.distance_travelled += metres
        # Free flow: the same cells at this road's vmax for this vehicle
        free = self.parameters.cells_per_step(min(road.limit, vehicle.speed))
        vehicle.last_free_flow_seconds += cells / free * self.parameters.time_step

    def _cross(self, simulation, car, plan, substep, steps):
        """Move a car past the end of its road along its plan, never
        further than the room each lane has now."""

        speed = car.v
        left = car.road.cells - 1 - car.front
        self._advance(car, left)
        to_go = speed - left
        moved = left

        for item in plan:
            if item == "exit":
                self._remove(car)
                self._finish(car, substep, steps, moved / speed if speed else 0.0)
                return

            road, lane = item
            space = self._space(lane, car.road)
            room = road.cells if space is None else space
            if room <= 0 or to_go <= 0:
                break

            advance = min(to_go, room)
            previous = car.road
            self._remove(car)
            lane.append(car)
            car.road, car.lane, car.front = road, lane, advance - 1
            car.came_from = previous
            car.index += 1
            self._travel(car.vehicle, advance, road)
            to_go -= advance
            moved += advance

            if space is not None or advance < road.cells:
                break

        car.v = moved
        if self._final_road(car) and car.front == car.road.cells - 1:
            self._remove(car)
            self._finish(car, substep, steps, moved / speed if speed else 0.0)
            return
        self._write(car)

    def _finish(self, car, substep, steps, fraction):
        """The car reached the end of its route `fraction` of the way
        through this time step."""
        vehicle = car.vehicle
        vehicle.position = len(vehicle.route) - 1
        vehicle.distance_on_segment = 0
        vehicle.finished = True
        vehicle.last_move_seconds = (substep + fraction) / steps
        car.road = car.lane = None

    def _measure(self, cars, speeds):
        """Each car's speed factor, and each road's state and smoothed
        speed (the feedback to routing)."""

        totals = {}
        for car in cars:
            v, vmax = speeds.get(car, (0, 1))
            factor = v / vmax if vmax else 0.0
            car.vehicle.speed_factor = factor
            if car.road is None or car.vehicle.finished:
                continue
            entry = totals.setdefault(car.road, [0, 0, 0.0])
            entry[0] += 1
            entry[1] += v == 0
            entry[2] += factor

        size = self.parameters.vehicle_cells
        state = {}
        for road, (count, stopped, factors) in totals.items():
            mean = factors / count
            old = self.measured.get(road.key)
            self.measured[road.key] = mean if old is None else old + MEASURE_WEIGHT * (mean - old)
            state[road.key] = {
                "vehicles": count,
                "stopped": stopped,
                "occupancy": min(1.0, count * size / (road.cells * len(road.lanes))),
                "speed_factor": mean,
            }

        for key in [key for key in self.measured if key not in state]:
            del self.measured[key]
        self.road_state = state


# ----------------------------------------------------------------------
# The traffic model
# ----------------------------------------------------------------------

class CaRoads:
    """The "ca" traffic model: the osm model's speed limits, lanes and
    per-direction roads, with cars moved by CellularTraffic and every
    road's speed taken from what the CA measured on it.

    One per simulation (it holds that simulation's cars), unlike the osm
    model, which is shared by every simulation on a map.
    """

    name = CA
    directed = True
    has_limits = True
    cellular = True

    def __init__(self, osm_roads, parameters=None, seed=0):
        self.osm = osm_roads
        self.network = osm_roads.network
        self.engine = CellularTraffic(osm_roads, parameters, seed)

    @property
    def parameters(self):
        return self.engine.parameters

    def key(self, point_a, point_b):
        return self.osm.key(point_a, point_b)

    def keys_of_segment(self, segment):
        return self.osm.keys_of_segment(segment)

    def limit(self, key):
        return self.osm.limit(key)

    def speed_factor(self, key, vehicle_count, accident=None):
        """What the CA measured on the road. A road with no measurement
        yet (cars just arrived, or only emergency vehicles on it) uses the
        osm model's estimate."""

        measured = self.engine.measured.get(key)
        if measured is None:
            return self.osm.speed_factor(key, vehicle_count, accident)
        return max(FLOOR_FACTOR, measured)

    def level(self, key, vehicle_count, accident=None):
        if vehicle_count <= 0:
            return "clear"
        factor = self.speed_factor(key, vehicle_count, accident)
        if factor >= LIGHT_ABOVE:
            return "light"
        if factor >= MODERATE_ABOVE:
            return "moderate"
        return "heavy"

    def step(self, simulation):
        return self.engine.step(simulation)

    def road_state(self):
        return self.engine.road_state

    def traffic_density(self, simulation):
        """Vehicles on each road (Simulation.get_traffic_density). A car on
        the lattice counts on the road it is on there: after a reroute from
        mid-road its route starts with a made-up stretch from where it was,
        but it is still on the real road. Other vehicles (emergency
        vehicles, cars waiting to enter) count on their route's road, as in
        the other models."""

        traffic = {}
        cars = self.engine.cars
        for vehicle in simulation.vehicles:
            if vehicle.finished or vehicle.position >= len(vehicle.route) - 1:
                continue
            car = cars.get(id(vehicle))
            if car is not None and car.road is not None:
                road = car.road.key
            else:
                road = self.key(vehicle.route[vehicle.position], vehicle.route[vehicle.position + 1])
            traffic[road] = traffic.get(road, 0) + 1
        return traffic


def make_ca_roads(network, parameters=None, seed=0):
    return CaRoads(network.osm_roads(), parameters, seed)


__all__ = [
    "CA", "CaParameters", "CaRoads", "CellularTraffic", "OpenRoad", "RingRoad",
    "accelerate", "brake", "make_ca_roads", "nasch_speed", "randomise", "OSM",
]
