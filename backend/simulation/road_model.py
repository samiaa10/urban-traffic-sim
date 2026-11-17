"""How roads behave: speed limits, capacity, and how traffic slows vehicles.

Two models, chosen per simulation:

simple   The original rules. Traffic is counted per road with both
         directions together; a road with 1 vehicle is free flowing, 2
         vehicles drive at 60% and 3 or more at 30% (simulation.py); every
         vehicle drives at its own speed on every road.

osm      Uses the OpenStreetMap attributes kept in the map
         (road_attributes.py):
         * Speed limits: a car drives at most at the road's limit (its own
           top speed if lower). Emergency vehicles are exempt, as UK law
           allows on a call (and the station optimiser assumes).
         * Each direction of a road is counted on its own, so vehicles
           going opposite ways no longer slow each other.
         * Capacity: how many cars fit on the road, lanes x length / 7.5 m
           (a car plus the gap in a standing queue). Speed falls in a
           straight line as the OTHER vehicles fill it (Greenshields'
           model: free flow when empty, stopped when full), with a 5% floor
           so a queue always creeps forward and a lone vehicle is never
           slowed. Very short roads (a quarter of Norwich's are under 20 m,
           mostly at junctions) are treated as MIN_ROAD_LENGTH long, or one
           car waiting to turn would jam them (see docs/road_network.md for
           how sensitive results are to this).
         * Accidents divide the capacity by their capacity factor and then
           apply their speed factor (accidents.py), as in the simple model.

Everything that needs a road's speed goes through the model: moving
vehicles (Vehicle.move), planning routes (RoadNetwork.fastest_path),
estimating arrival times (dispatch.remaining_seconds) and measuring delay
(scenarios/impact.py), so planned and driven times stay the same.

Roads are keyed by their two end points ((lat, lon), (lat, lon)): in the
simple model sorted (either direction), in the osm model in driving
direction. Closures and accidents always cover both directions.
"""

from backend.routing.road_attributes import MPH, default_speed_limit


SIMPLE = "simple"
OSM = "osm"
TRAFFIC_MODELS = (SIMPLE, OSM)

# Metres of road one car occupies in a standing queue (car plus gap)
JAM_SPACING = 7.5

# Shortest road length used for capacity (see the module docstring)
MIN_ROAD_LENGTH = 30.0

# Slowest a road can get, as a fraction of its free-flow speed
FLOOR_FACTOR = 0.05

# Congestion colours in the osm model, by speed factor
LIGHT_ABOVE = 0.8
MODERATE_ABOVE = 0.4

# Top speed given to cars in the osm model: the road's limit decides
# (70 mph is the highest UK limit)
CAR_TOP_SPEED = 70 * MPH

# Roads the map does not know (the made-up road from a rerouted vehicle's
# position to the next junction, or a map without attributes): the UK
# built-up default
UNKNOWN_ROAD_LIMIT = default_speed_limit("road")


def point(p):
    return (p["latitude"], p["longitude"])


def undirected(key):
    return tuple(sorted(key))


class SimpleRoads:
    """The original rules (see the module docstring)."""

    name = SIMPLE
    directed = False
    has_limits = False

    def key(self, point_a, point_b):
        return undirected((point(point_a), point(point_b)))

    def keys_of_segment(self, segment):
        """Traffic keys covering a closure or accident segment."""
        return (segment,)

    def limit(self, key):
        return None

    def speed_factor(self, key, vehicle_count, accident=None):
        from backend.simulation.simulation import speed_factor_on_road

        return speed_factor_on_road(vehicle_count, accident)

    def level(self, key, vehicle_count, accident=None):
        from backend.simulation.simulation import (
            congestion_level,
            effective_vehicle_count,
        )

        return congestion_level(effective_vehicle_count(vehicle_count, accident))


class OsmRoads:
    """Speed limits, lanes and capacity from OpenStreetMap (see the module
    docstring). Built once per map and shared by every simulation on it."""

    name = OSM
    directed = True
    has_limits = True

    def __init__(self, network, min_road_length=MIN_ROAD_LENGTH):
        self.min_road_length = min_road_length

        # Directed key -> speed limit (m/s) and capacity (vehicles)
        self.limits = {}
        self.capacity = {}

        graph = network.graph

        for (a, b), length in network.length.items():
            key = (network.position[a], network.position[b])
            info = graph.road(a, b)

            lanes = info.lanes if info is not None else 1
            self.limits[key] = (
                info.speed_limit if info is not None else UNKNOWN_ROAD_LIMIT
            )
            self.capacity[key] = self._capacity(length, lanes)

    def _capacity(self, length, lanes):
        return lanes * max(length, self.min_road_length) / JAM_SPACING

    def key(self, point_a, point_b):
        return (point(point_a), point(point_b))

    def keys_of_segment(self, segment):
        a, b = segment
        return ((a, b), (b, a))

    def limit(self, key):
        return self.limits.get(key, UNKNOWN_ROAD_LIMIT)

    def _road_capacity(self, key):
        capacity = self.capacity.get(key)

        if capacity is None:
            # A made-up road (see UNKNOWN_ROAD_LIMIT): one lane, the
            # straight-line length
            from backend.simulation.vehicle import straight_line_metres

            capacity = self._capacity(straight_line_metres(*key), 1)

        return capacity

    def speed_factor(self, key, vehicle_count, accident=None):
        """Fraction of free-flow speed with vehicle_count vehicles on the
        road (the one asking included)."""

        others = max(0, vehicle_count - 1)
        factor = 1.0

        if others:
            capacity = self._road_capacity(key)

            if accident is not None:
                capacity *= accident.capacity_factor

            factor = max(FLOOR_FACTOR, 1.0 - others / capacity)

        if accident is not None:
            factor *= accident.speed_factor

        return factor

    def level(self, key, vehicle_count, accident=None):
        if vehicle_count <= 0:
            return "clear"

        factor = self.speed_factor(key, vehicle_count, accident)

        if factor >= LIGHT_ABOVE:
            return "light"
        if factor >= MODERATE_ABOVE:
            return "moderate"
        return "heavy"


SIMPLE_ROADS = SimpleRoads()


def make_road_model(name, network=None):
    """The model called `name` (osm needs the RoadNetwork)."""

    if name == SIMPLE:
        return SIMPLE_ROADS

    if name == OSM:
        if network is None:
            raise ValueError("the osm traffic model needs the road network")
        return network.osm_roads()

    raise ValueError(f"traffic model must be one of {', '.join(TRAFFIC_MODELS)}")
