import math

from backend.simulation.accidents import Accident
from backend.simulation.disruptions import Flood, combine
from backend.simulation.vehicle import Vehicle
from backend.simulation.emergency import Emergency
from backend.simulation.station import EmergencyStation
from backend.simulation.road_model import SIMPLE_ROADS, undirected
from backend.simulation.services import rules_for


# How busy a road is, from the number of vehicles on it. The map colours
# roads by these levels, and the same levels set how fast vehicles can go.
def congestion_level(vehicle_count):

    if vehicle_count <= 0:
        return "clear"

    if vehicle_count == 1:
        return "light"

    if vehicle_count == 2:
        return "moderate"

    return "heavy"


# Fraction of full speed a vehicle can drive at on a road of each level.
# A lone vehicle drives freely; a crowded road is a crawl.
SPEED_FACTORS = {
    "clear": 1.0,
    "light": 1.0,
    "moderate": 0.6,
    "heavy": 0.3,
}


def speed_factor_for_count(vehicle_count):
    """Fraction of full speed on a road with this many vehicles on it."""

    return SPEED_FACTORS[congestion_level(vehicle_count)]


def effective_vehicle_count(vehicle_count, accident=None):
    """How many vehicles the road "feels" like it has.

    An accident lowers the road's capacity, so the same vehicles congest
    it more: with half the capacity, 2 vehicles count as 4. A lone vehicle
    is never held up by capacity (it only has itself), but it is still
    slowed by the accident's speed factor.
    """

    if accident is None or vehicle_count <= 1:
        return vehicle_count

    return math.ceil(vehicle_count / accident.capacity_factor - 1e-9)


def speed_factor_on_road(vehicle_count, accident=None):
    """Fraction of full speed on a road: traffic, then any accident.

    This is the single definition of a road's speed. The simulation moves
    vehicles with it, ETAs are estimated with it, and the travel-time
    router plans routes with it, so all three agree.
    """

    factor = speed_factor_for_count(
        effective_vehicle_count(vehicle_count, accident)
    )

    if accident is not None:
        factor *= accident.speed_factor

    return factor


# Congestion levels, least to most congested
LEVEL_ORDER = ["clear", "light", "moderate", "heavy"]


def segment_between(point_a, point_b):
    """Road segment key for two route points (direction does not matter)."""

    return tuple(sorted((
        (point_a["latitude"], point_a["longitude"]),
        (point_b["latitude"], point_b["longitude"])
    )))


class Simulation:

    def __init__(self, route, road_model=None):
        self.vehicles = []

        # How roads behave: speed limits, capacity and how traffic slows
        # vehicles (road_model.py). The simple model is the original rules.
        self.road_model = road_model or SIMPLE_ROADS

        # How emergencies are dispatched, queued and finished
        # (services.py): the original rules with the simple model, the
        # realistic ones with the osm model
        self.emergency_rules = rules_for(self.road_model)

        # Simulated time in seconds (goes up by 1 every step)
        self.time = 0

        # Roads currently affected by incidents
        self.blocked_segments = set()

        # Active emergency incidents
        self.emergencies = []

        # Emergency stations
        self.stations = []

        # Accidents: road segment -> Accident (the road stays open but slow)
        self.accidents = {}

        # Who has each closed road closed (block_segment's `source`): it
        # reopens only when nobody does
        self._closed_by = {}

        # Floods (disruptions.py), and each road's floods for quick lookup
        self.floods = []
        self._floods_on = {}
        self._next_flood = 1

        # Things that happened to the road network, oldest first. Each is
        # {"time", "type", "segment"}; type is one of "road_closed",
        # "road_reopened", "accident", "accident_cleared". Whoever plans
        # routes (see traffic_manager.py) reads these to react.
        self.events = []

        # Real road lengths, keyed by ((lat, lon), (lat, lon)) in driving
        # direction. None means vehicles measure roads as straight lines
        # (see use_road_lengths).
        self.road_lengths = None

        # Traffic signals at chosen junctions (signals.py), or None
        self.signals = None

        # Create the first vehicle
        self.add_vehicle(route)

    def add_vehicle(
        self,
        route,
        speed=13.9,
        vehicle_type="normal"
    ):
        vehicle = Vehicle(
            route,
            speed,
            vehicle_type
        )

        vehicle.road_lengths = self.road_lengths

        self.vehicles.append(vehicle)

        return vehicle

    def use_road_lengths(self, road_lengths):
        """Make vehicles drive the real length of each road.

        road_lengths maps ((lat, lon), (lat, lon)) to metres (for example
        RoadNetwork.length_between_points). Without it a vehicle measures
        the straight line between two junctions, which is shorter than the
        road the router and the optimiser measured. Roads not in the
        mapping (such as the bit of road from a vehicle's position to the
        next junction after a reroute) are still measured as straight lines.
        """

        self.road_lengths = road_lengths

        for vehicle in self.vehicles:
            vehicle.road_lengths = road_lengths

    def record_event(self, event_type, segment):
        self.events.append(
            {"time": self.time, "type": event_type, "segment": segment}
        )

    # ========================================
    # INCIDENTS / BLOCKED ROADS
    # ========================================

    def block_segment(self, segment, source="manual"):
        """Block a road segment because of an incident.

        source names who closed it (a manual closure, a flood, a failure):
        several can close the same road, and it reopens only when every one
        of them has reopened it (unblock_segment)."""
        segment = tuple(sorted(segment))
        self._closed_by.setdefault(segment, set()).add(source)

        if segment not in self.blocked_segments:
            self.blocked_segments.add(segment)
            self.record_event("road_closed", segment)

    def unblock_segment(self, segment, source="manual"):
        """Reopen a road closed by `source`; it stays closed while anything
        else still has it closed."""
        segment = tuple(sorted(segment))
        sources = self._closed_by.get(segment)
        if sources is not None:
            sources.discard(source)
            if sources:
                return
            del self._closed_by[segment]

        if segment in self.blocked_segments:
            self.blocked_segments.discard(segment)
            self.record_event("road_reopened", segment)

    def closed_by(self, segment):
        """Who has a road closed (empty if it is open)."""
        return set(self._closed_by.get(tuple(sorted(segment)), ()))

    # ========================================
    # FLOODS (disruptions.py)
    # ========================================

    def add_flood(self, segments, severity="moderate", duration=1800, recovery_seconds=900):
        """Flood a set of roads from now. Returns the Flood."""

        flood = Flood(self._next_flood, segments, severity, self.time, duration, recovery_seconds)
        self._next_flood += 1
        self.floods.append(flood)

        for segment in flood.segments:
            self._floods_on.setdefault(segment, []).append(flood)
            if flood.closes_roads:
                self.block_segment(segment, source=flood.source)
            else:
                self.record_event("flood", segment)

        return flood

    def _end_flood(self, flood):
        if flood.closes_roads and not flood.reopened:
            for segment in flood.segments:
                self.unblock_segment(segment, source=flood.source)
        self.floods.remove(flood)
        for segment in flood.segments:
            others = [f for f in self._floods_on.get(segment, []) if f is not flood]
            if others:
                self._floods_on[segment] = others
            else:
                self._floods_on.pop(segment, None)
            self.record_event("flood_cleared", segment)

    def clear_flood(self, ident):
        """End a flood at once (its roads fully recovered). False if none."""
        flood = next((f for f in self.floods if f.ident == ident), None)
        if flood is None:
            return False
        self._end_flood(flood)
        return True

    def update_floods(self):
        """Reopen closed roads when their flood starts receding, and remove
        floods that have fully recovered (called by step)."""

        for flood in list(self.floods):
            stage = flood.stage(self.time)
            if stage != "flooded" and flood.closes_roads and not flood.reopened:
                flood.reopened = True
                for segment in flood.segments:
                    self.unblock_segment(segment, source=flood.source)
            if stage == "over":
                self._end_flood(flood)

    def road_incident(self, segment):
        """What incidents do to a road now: its accident and any floods on
        it combined (an object with speed_factor and capacity_factor), or
        None. segment is the sorted segment key."""

        accident = self.accidents.get(segment)
        floods = self._floods_on.get(segment)
        if not floods:
            return accident
        return combine(accident, *(flood.effect(self.time) for flood in floods))

    def incident_segments(self):
        """Roads with an accident or a flood on them."""
        return set(self.accidents) | set(self._floods_on)

    # ========================================
    # ACCIDENTS (road stays open, but slow)
    # ========================================

    def add_accident(self, segment, severity="moderate", duration=None):
        """Start an accident on a road. Replaces any accident already there."""

        segment = tuple(sorted(segment))
        accident = Accident(segment, severity, self.time, duration)

        self.accidents[segment] = accident
        self.record_event("accident", segment)

        return accident

    def clear_accident(self, segment):
        """Clear an accident early. Returns False if there was none."""

        segment = tuple(sorted(segment))

        if self.accidents.pop(segment, None) is None:
            return False

        self.record_event("accident_cleared", segment)
        return True

    def clear_expired_accidents(self):
        """Remove accidents whose time is up (called by step)."""

        for segment, accident in list(self.accidents.items()):
            if self.time >= accident.clears_at:
                self.clear_accident(segment)

    def road_speed_factor(self, segment, vehicle_count):
        """Fraction of free-flow speed on this road with this many vehicles.

        segment is the road's traffic key (road_model.py: sorted in the
        simple model, in driving direction in the osm model)."""

        return self.road_model.speed_factor(
            segment, vehicle_count, self.road_incident(undirected(segment))
        )

    def traffic_key(self, point_a, point_b):
        """The key traffic on the road from point_a to point_b is counted
        under (see road_model.py)."""

        return self.road_model.key(point_a, point_b)

    def limit_factor_for(self, vehicle):
        """Function(point_a, point_b) -> the road's speed limit as a
        fraction of the vehicle's speed, or None if no limit applies to it
        (simple model, or an emergency vehicle)."""

        model = self.road_model

        if not model.has_limits or not vehicle.obeys_speed_limits:
            return None

        speed = vehicle.speed
        key = model.key
        limit = model.limit

        def factor(point_a, point_b):
            return min(1.0, limit(key(point_a, point_b)) / speed)

        return factor

    def is_segment_blocked(self, segment):
        """Check whether a road segment is blocked."""
        segment = tuple(sorted(segment))
        return segment in self.blocked_segments

    def is_road_closed(self, point_a, point_b):
        """Check whether the road between two route points is closed."""
        return self.is_segment_blocked((
            (point_a["latitude"], point_a["longitude"]),
            (point_b["latitude"], point_b["longitude"])
        ))

    def get_blocked_segments(self):
        """Return all currently blocked road segments."""
        return self.blocked_segments

    # ========================================
    # EMERGENCIES
    # ========================================

    def create_emergency(
        self,
        latitude,
        longitude,
        emergency_type="ambulance"
    ):
        """Create a new emergency incident."""

        emergency = Emergency(
            latitude,
            longitude,
            emergency_type,
            created_time=self.time
        )

        self.emergencies.append(emergency)

        return emergency

    def get_emergencies(self):
        """Return all emergency incidents."""
        return self.emergencies

    def check_emergencies(self):
        """Resolve emergencies whose dispatched vehicle has arrived.

        Only the vehicle that was sent to an emergency can resolve it
        (a normal car driving past does not count). The vehicle has
        arrived when it reaches the end of its route.
        """

        for emergency in self.emergencies:

            if not emergency.active:
                continue

            vehicle = emergency.assigned_vehicle

            # Nobody has been sent yet
            if vehicle is None:
                continue

            # Still on its way
            if not vehicle.finished:
                continue

            emergency.resolve(self.time)

            if self.emergency_rules.drive_back and vehicle.home_station is not None:
                # It drives back (dispatch.after_step gives it the route);
                # it can be sent again from on the way
                vehicle.on_emergency = False
                vehicle.available = True
                vehicle.going_home = True
                vehicle.needs_route_home = True
            # Free the vehicle so it can answer the next emergency
            elif vehicle.home_station is not None:
                vehicle.home_station.return_vehicle(vehicle)
            else:
                vehicle.on_emergency = False
                vehicle.available = True

    @staticmethod
    def on_call(vehicle):
        """An emergency vehicle driving to an emergency."""
        return vehicle.on_emergency and vehicle.vehicle_type != "normal"

    def queued_emergencies(self):
        """Emergencies waiting for a vehicle, oldest first."""
        return sorted(
            (e for e in self.emergencies if e.active and e.assigned_vehicle is None),
            key=lambda e: e.created_at,
        )

    def cancel_emergency(self, emergency):
        """Remove an emergency that could not be answered and free its vehicle."""

        vehicle = emergency.assigned_vehicle

        if vehicle is not None:

            if vehicle.home_station is not None:
                vehicle.home_station.return_vehicle(vehicle)
            else:
                vehicle.on_emergency = False
                vehicle.available = True

        if emergency in self.emergencies:
            self.emergencies.remove(emergency)

    # ========================================
    # EMERGENCY STATIONS
    # ========================================

    def add_station(
        self,
        latitude,
        longitude,
        station_type="ambulance"
    ):
        """Add an emergency station."""

        station = EmergencyStation(
            latitude,
            longitude,
            station_type
        )

        self.stations.append(station)

        return station

    def get_stations(self):
        """Return all emergency stations."""
        return self.stations

    def find_nearest_station(
        self,
        latitude,
        longitude,
        station_type="ambulance"
    ):
        """Find the nearest station of the requested type."""

        nearest_station = None
        nearest_distance = float("inf")

        for station in self.stations:

            if station.station_type != station_type:
                continue

            if not station.has_available_vehicle():
                continue

            distance = (
                (station.latitude - latitude) ** 2
                + (station.longitude - longitude) ** 2
            )

            if distance < nearest_distance:
                nearest_distance = distance
                nearest_station = station

        return nearest_station

    def dispatch_vehicle(
        self,
        vehicle_id,
        emergency
    ):
        """Dispatch a specific emergency vehicle."""

        if vehicle_id < 0:
            return None

        if vehicle_id >= len(self.vehicles):
            return None

        vehicle = self.vehicles[vehicle_id]

        if vehicle.finished:
            return None

        vehicle.on_emergency = True
        vehicle.available = False

        emergency.assigned_vehicle = vehicle

        return vehicle

    def dispatch_from_nearest_station(
        self,
        emergency
    ):
        """Find the nearest station and dispatch an available vehicle."""

        station = self.find_nearest_station(
            emergency.latitude,
            emergency.longitude,
            emergency.emergency_type
        )

        if station is None:
            return None

        vehicle = station.get_available_vehicle()

        if vehicle is None:
            return None

        vehicle.on_emergency = True
        vehicle.available = False

        emergency.assigned_vehicle = vehicle

        return vehicle

    def add_emergency_vehicle(
        self,
        route,
        station,
        speed=20,
        vehicle_type="ambulance"
    ):
        """Create an emergency vehicle and place it at a station."""

        vehicle = self.add_vehicle(
            route,
            speed,
            vehicle_type
        )

        vehicle.available = True
        vehicle.on_emergency = False

        station.add_vehicle(vehicle)

        return vehicle

    # ========================================
    # SIMULATION
    # ========================================

    def step(self):

        # Measure traffic ONCE, before anybody moves, so every vehicle
        # sees the same roads and the order of the list does not matter.
        # (A vehicle that crosses several short roads in one second keeps
        # using this snapshot; the speed is re-read every second.)
        traffic = self.get_traffic_density()
        key = self.road_model.key
        rules = self.emergency_rules

        def speed_factor(point_a, point_b):
            road = key(point_a, point_b)
            return self.road_speed_factor(road, traffic.get(road, 0))

        def emergency_speed_factor(point_a, point_b):
            return rules.emergency_factor(speed_factor(point_a, point_b))

        # Signals see where everyone is, then change for this second
        signals = self.signals
        if signals is not None:
            signals.step(self, traffic)

        # The ca model moves ordinary cars itself (cellular.py); everything
        # else moves below, as in the other models
        handled = ()
        if self.road_model.cellular:
            handled = self.road_model.step(self)

        for vehicle in self.vehicles:

            if vehicle.finished or vehicle in handled:
                continue

            # The trip clock runs while the vehicle is on its way, so
            # slow traffic and closed roads both make the journey longer.
            vehicle.journey_seconds += 1

            if vehicle.position < len(vehicle.route) - 1:

                current_point = vehicle.route[vehicle.position]
                next_point = vehicle.route[vehicle.position + 1]

                if segment_between(
                    current_point, next_point
                ) in self.blocked_segments:
                    continue

            vehicle.move(
                1,
                is_blocked=self.is_road_closed,
                speed_factor=(
                    emergency_speed_factor
                    if rules.right_of_way and self.on_call(vehicle)
                    else speed_factor
                ),
                limit_factor=self.limit_factor_for(vehicle),
                may_cross=(
                    None if signals is None
                    else lambda a, j, b, vehicle=vehicle: signals.may_cross(vehicle, a, j, b)
                ),
            )

        self.time += 1

        self.clear_expired_accidents()
        self.update_floods()

        self.check_emergencies()

    def get_vehicle_positions(self):
        positions = []

        for vehicle in self.vehicles:
            positions.append(
                vehicle.get_current_location()
            )

        return positions

    # ========================================
    # TRAFFIC
    # ========================================

    def get_traffic_density(self):
        """Vehicles on each road, keyed by traffic key (road_model.py)."""

        # The ca model counts cars on the roads its lattice has them on
        if self.road_model.cellular:
            return self.road_model.traffic_density(self)

        traffic = {}
        key = self.road_model.key

        for vehicle in self.vehicles:

            if vehicle.finished:
                continue

            if vehicle.position >= len(vehicle.route) - 1:
                continue

            road = key(
                vehicle.route[vehicle.position],
                vehicle.route[vehicle.position + 1]
            )

            traffic[road] = traffic.get(road, 0) + 1

        return traffic

    def get_segment_traffic(self):
        """Vehicles on each road segment, both directions together (the
        sorted segment key). This is what the shortest-route search's
        traffic penalty (a_star.py) counts, whatever the traffic model."""

        if not self.road_model.directed:
            return self.get_traffic_density()

        counts = {}
        for road, count in self.get_traffic_density().items():
            segment = undirected(road)
            counts[segment] = counts.get(segment, 0) + count

        return counts

    # ========================================
    # CONGESTION
    # ========================================

    def get_congestion(self):
        """Congestion per road segment (both directions together, for the
        map): vehicles on it, and the level of its busier direction."""

        traffic = self.get_traffic_density()

        congestion = {}

        for road, vehicle_count in traffic.items():

            segment = undirected(road)
            accident = self.road_incident(segment)

            # An accident's lost capacity shows in the level too, so the
            # map colour matches how slowly vehicles really move there
            level = self.road_model.level(road, vehicle_count, accident)

            entry = congestion.get(segment)

            if entry is None:
                congestion[segment] = {"vehicle_count": vehicle_count, "level": level}
            else:
                entry["vehicle_count"] += vehicle_count
                if LEVEL_ORDER.index(level) > LEVEL_ORDER.index(entry["level"]):
                    entry["level"] = level

        return congestion

    # ========================================
    # COMMAND-LINE SIMULATION
    # ========================================

    def run(self):

        while not all(
            vehicle.finished
            for vehicle in self.vehicles
        ):

            for vehicle in self.vehicles:

                if not vehicle.finished:

                    location = vehicle.get_current_location()

                    print(
                        "Vehicle location:",
                        location["latitude"],
                        location["longitude"]
                    )

                    vehicle.move(1)

        for vehicle in self.vehicles:

            print(
                "Vehicle has reached its destination."
            )

            print(
                "Distance travelled:",
                round(vehicle.distance_travelled),
                "metres"
            )


if __name__ == "__main__":

    route = [
        {
            "latitude": 52.6309,
            "longitude": 1.2974
        },
        {
            "latitude": 52.6315,
            "longitude": 1.2980
        },
        {
            "latitude": 52.6320,
            "longitude": 1.2990
        },
    ]

    simulation = Simulation(route)

    simulation.run()
