from backend.simulation.vehicle import Vehicle
from backend.simulation.emergency import Emergency
from backend.simulation.station import EmergencyStation


class Simulation:

    def __init__(self, route):
        self.vehicles = []

        # Simulated time in seconds (goes up by 1 every step)
        self.time = 0

        # Roads currently affected by incidents
        self.blocked_segments = set()

        # Active emergency incidents
        self.emergencies = []

        # Emergency stations
        self.stations = []

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

        self.vehicles.append(vehicle)

        return vehicle

    # ========================================
    # INCIDENTS / BLOCKED ROADS
    # ========================================

    def block_segment(self, segment):
        """Block a road segment because of an incident."""
        segment = tuple(sorted(segment))
        self.blocked_segments.add(segment)

    def unblock_segment(self, segment):
        """Reopen a previously blocked road segment."""
        segment = tuple(sorted(segment))
        self.blocked_segments.discard(segment)

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

            # Free the vehicle so it can answer the next emergency
            if vehicle.home_station is not None:
                vehicle.home_station.return_vehicle(vehicle)
            else:
                vehicle.on_emergency = False
                vehicle.available = True

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

        for vehicle in self.vehicles:

            if vehicle.finished:
                continue

            if vehicle.position < len(vehicle.route) - 1:

                current_point = vehicle.route[vehicle.position]
                next_point = vehicle.route[vehicle.position + 1]

                segment = (
                    (
                        current_point["latitude"],
                        current_point["longitude"]
                    ),
                    (
                        next_point["latitude"],
                        next_point["longitude"]
                    )
                )

                segment = tuple(sorted(segment))

                if segment in self.blocked_segments:
                    continue

            vehicle.move(1, is_blocked=self.is_road_closed)

        self.time += 1

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
        traffic = {}

        for vehicle in self.vehicles:

            if vehicle.finished:
                continue

            if vehicle.position >= len(vehicle.route) - 1:
                continue

            current_point = vehicle.route[vehicle.position]
            next_point = vehicle.route[vehicle.position + 1]

            segment = (
                (
                    current_point["latitude"],
                    current_point["longitude"]
                ),
                (
                    next_point["latitude"],
                    next_point["longitude"]
                )
            )

            segment = tuple(sorted(segment))

            if segment not in traffic:
                traffic[segment] = 0

            traffic[segment] += 1

        return traffic

    # ========================================
    # CONGESTION
    # ========================================

    def get_congestion(self):

        traffic = self.get_traffic_density()

        congestion = {}

        for segment, vehicle_count in traffic.items():

            if vehicle_count == 1:
                level = "light"
            elif vehicle_count == 2:
                level = "moderate"
            elif vehicle_count >= 3:
                level = "heavy"
            else:
                level = "clear"

            congestion[segment] = {
                "vehicle_count": vehicle_count,
                "level": level
            }

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
