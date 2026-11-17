"""Measuring a scenario's effect on the whole network.

Definitions (docs/ARCHITECTURE.md, "Scenarios", explains them):

delay of a trip
    Time spent on the road minus distance_travelled / speed. The vehicle
    moves at speed x speed factor (Simulation.step), so driving d metres
    takes d / (speed x factor) seconds against d / speed in free flow,
    and seconds spent waiting at a closed road cover no distance at all.
    The difference is exactly the time lost to traffic, accidents and
    closures; it is a measurement of the simulation, not an estimate.
    "Time spent" excludes the unused rest of the second in which the
    vehicle arrives (Vehicle.last_move_seconds), so a vehicle alone on
    the network has a delay of exactly zero. (journey_seconds, the
    simulation's own trip clock, counts that whole last second.)

network delay
    The sum over all trips, in vehicle-hours.

journey-time increase of a trip
    journey_seconds - the free-flow time of the fastest route for the
    same trip (no traffic, no closures). Unlike delay it also counts
    detours: a closure that sends a trip 2 km round has a cost even if
    the detour is free-flowing.

delay on a road
    Each simulated second, the delay each vehicle picked up in that
    second is added to the road it was on at the start of the second,
    the same snapshot the simulation moves vehicles by. (A vehicle that
    crosses into another road during the second has all of that second
    attributed to the first road. A vehicle rerouted part-way along a
    road is on a short "made-up" road from its position to the next
    junction until it reaches that junction (rerouting.find_restart_point);
    delay there is counted under that made-up road, whose start has no
    junction id. The network totals are exact either way.)

Areas are 500 m grid cells; a road belongs to the cell its midpoint is
in.
"""

import math
from collections import defaultdict


AREA_CELL_METRES = 500
METRES_PER_DEGREE = 111_320

# How many roads and areas a result keeps (the rest only count in totals)
TOP_ROADS = 25
TOP_AREAS = 10


class ImpactTracker:
    """Collects per-second delay for the trips it is told about."""

    def __init__(self, simulation, node_by_point, timeseries_interval=60, measure=True):
        self.simulation = simulation
        # measure=False keeps only the bookkeeping (which vehicles are still
        # on their way), for measuring what the measuring itself costs
        self.measure = measure
        self.node_by_point = node_by_point
        self.timeseries_interval = timeseries_interval

        # Tracked vehicles (demand and surge trips, not ambulances), and
        # those still on their way (finished ones are dropped from it)
        self.vehicles = []
        self.active = []

        # Road key -> accumulated figures
        self.road_delay = defaultdict(float)        # vehicle-seconds lost
        self.road_occupancy = defaultdict(int)       # vehicle-seconds on it
        self.road_peak = defaultdict(int)            # most vehicles at once

        self.timeseries = []
        self._interval_delay = 0.0
        self._interval_vehicle_seconds = 0
        self._snapshot = []

    def track(self, vehicle):
        self.vehicles.append(vehicle)
        self.active.append(vehicle)

    @property
    def all_finished(self):
        return not self.active

    # ------------------------------------------------------------
    # Every second
    # ------------------------------------------------------------

    def before_step(self):
        """Record where each moving vehicle is and how far it has got."""

        if not self.measure:
            return

        snapshot = []
        counts = defaultdict(int)

        for vehicle in self.active:
            if vehicle.finished or vehicle.position >= len(vehicle.route) - 1:
                continue

            road = self._road_of(vehicle)
            snapshot.append(
                (vehicle, road, vehicle.journey_seconds, vehicle.distance_travelled)
            )
            counts[road] += 1

        for road, count in counts.items():
            if count > self.road_peak[road]:
                self.road_peak[road] = count

        self._snapshot = snapshot

    def after_step(self):
        """Add the delay each vehicle picked up during the step."""

        if not self.measure:
            self.active = [vehicle for vehicle in self.active if not vehicle.finished]
            return

        step_delay = 0.0
        limits = self.simulation.road_model.has_limits

        for vehicle, road, journey, distance in self._snapshot:
            if vehicle.journey_seconds - journey <= 0:
                continue

            # The whole second, unless the vehicle arrived during it: then
            # only the part it spent driving (the journey clock still
            # counts the whole second, as it always has)
            seconds = vehicle.last_move_seconds if vehicle.finished else 1.0

            driven = vehicle.distance_travelled - distance

            if limits and vehicle.obeys_speed_limits:
                # Free flow is the speed limit on each road it drove
                # (Vehicle.move adds it up); a vehicle that did not move
                # this second had no free-flow time at all
                free = vehicle.last_free_flow_seconds if driven > 0 else 0.0
            else:
                free = driven / vehicle.speed

            lost = float(seconds - free)

            # Floating-point rounding can leave -1e-12 on a free-flowing road
            if lost < 1e-9:
                lost = 0.0

            self.road_delay[road] += lost
            self.road_occupancy[road] += 1
            vehicle.scenario_delay = getattr(vehicle, "scenario_delay", 0.0) + lost
            step_delay += lost

        self._interval_delay += step_delay
        self._interval_vehicle_seconds += len(self._snapshot)
        self._snapshot = []

        if any(vehicle.finished for vehicle in self.active):
            self.active = [vehicle for vehicle in self.active if not vehicle.finished]

        now = self.simulation.time
        if now % self.timeseries_interval == 0:
            self._record_interval(now)

    def finish(self):
        """Close the last, partial interval of the time series."""

        now = self.simulation.time
        if self._interval_vehicle_seconds and (
            not self.timeseries or self.timeseries[-1]["time"] != now
        ):
            self._record_interval(now)

    def _record_interval(self, now):
        moving = [
            v for v in self.vehicles
            if not v.finished and v.position < len(v.route) - 1
        ]
        factors = [v.speed_factor for v in moving]

        self.timeseries.append({
            "time": now,
            "active_vehicles": len(moving),
            "finished_vehicles": sum(1 for v in self.vehicles if v.finished),
            "delay_vehicle_seconds": float(self._interval_delay),
            "vehicle_seconds": self._interval_vehicle_seconds,
            "mean_speed_factor": float(sum(factors) / len(factors)) if factors else None,
            "closed_roads": len(self.simulation.get_blocked_segments()),
            "accidents": len(self.simulation.accidents),
        })

        self._interval_delay = 0.0
        self._interval_vehicle_seconds = 0

    def _road_of(self, vehicle):
        """The road key a vehicle is on: the sorted pair of its route
        points around its position. Cached until the vehicle moves to the
        next road or gets a new route (a vehicle stays on a road for
        several seconds, so this saves building the key every second)."""

        cached = getattr(vehicle, "_impact_road", None)
        if (
            cached is not None
            and cached[0] is vehicle.route
            and cached[1] == vehicle.position
        ):
            return cached[2]

        a = vehicle.route[vehicle.position]
        b = vehicle.route[vehicle.position + 1]
        point_a = (a["latitude"], a["longitude"])
        point_b = (b["latitude"], b["longitude"])
        road = (point_a, point_b) if point_a <= point_b else (point_b, point_a)

        vehicle._impact_road = (vehicle.route, vehicle.position, road)
        return road

    # ------------------------------------------------------------
    # Results
    # ------------------------------------------------------------

    def top_roads(self, limit=TOP_ROADS):
        """The roads with the most delay, as plain dicts."""

        ranked = sorted(
            ((delay, road) for road, delay in self.road_delay.items() if delay > 0),
            key=lambda item: (-item[0], item[1]),
        )

        roads = []

        for rank, (delay, road) in enumerate(ranked[:limit], start=1):
            (lat_a, lon_a), (lat_b, lon_b) = road
            occupancy = self.road_occupancy[road]

            roads.append({
                "rank": rank,
                "start_node": self.node_by_point.get(road[0]),
                "end_node": self.node_by_point.get(road[1]),
                "start": {"latitude": lat_a, "longitude": lon_a},
                "end": {"latitude": lat_b, "longitude": lon_b},
                "delay_vehicle_seconds": float(delay),
                "vehicle_seconds": int(occupancy),
                "peak_vehicles": self.road_peak[road],
                # Share of the time on this road that was lost
                "delay_share": float(delay / occupancy) if occupancy else 0.0,
            })

        return roads

    def top_areas(self, limit=TOP_AREAS):
        """500 m cells with the most delay (by road midpoint)."""

        cell_delay = defaultdict(float)
        cell_roads = defaultdict(int)

        for road, delay in self.road_delay.items():
            if delay <= 0:
                continue
            cell = area_cell(*midpoint(road))
            cell_delay[cell] += delay
            cell_roads[cell] += 1

        ranked = sorted(cell_delay.items(), key=lambda item: (-item[1], item[0]))

        areas = []

        for rank, (cell, delay) in enumerate(ranked[:limit], start=1):
            south, west, north, east = cell_bounds(cell)
            areas.append({
                "rank": rank,
                "cell_row": cell[0],
                "cell_col": cell[1],
                "south": south,
                "west": west,
                "north": north,
                "east": east,
                "delay_vehicle_seconds": float(delay),
                "roads_affected": cell_roads[cell],
            })

        return areas

    def total_road_delay(self):
        return float(sum(self.road_delay.values()))


def midpoint(road):
    (lat_a, lon_a), (lat_b, lon_b) = road
    return (lat_a + lat_b) / 2, (lon_a + lon_b) / 2


# Grid cells are fixed in degrees so a cell is the same in every scenario.
# 500 m of longitude at Norwich's latitude (52.63 N).
_CELL_LAT = AREA_CELL_METRES / METRES_PER_DEGREE
_CELL_LON = AREA_CELL_METRES / (METRES_PER_DEGREE * math.cos(math.radians(52.63)))


def area_cell(latitude, longitude):
    return (math.floor(latitude / _CELL_LAT), math.floor(longitude / _CELL_LON))


def cell_bounds(cell):
    row, col = cell
    return (
        row * _CELL_LAT,
        col * _CELL_LON,
        (row + 1) * _CELL_LAT,
        (col + 1) * _CELL_LON,
    )


def percentile(values, fraction):
    """The value below which `fraction` of `values` fall (nearest rank)."""

    if not values:
        return None

    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[index]
