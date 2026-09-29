class EmergencyStation:

    def __init__(
        self,
        latitude,
        longitude,
        station_type="ambulance"
    ):
        self.latitude = latitude
        self.longitude = longitude
        self.station_type = station_type

        # Emergency vehicles currently available at this station
        self.available_vehicles = []

    def add_vehicle(self, vehicle):
        """Add an emergency vehicle to this station."""

        vehicle.available = True
        vehicle.on_emergency = False

        self.available_vehicles.append(vehicle)

    def get_available_vehicle(self):
        """Remove and return an available emergency vehicle."""

        if not self.available_vehicles:
            return None

        vehicle = self.available_vehicles.pop(0)

        vehicle.available = False
        vehicle.on_emergency = True

        return vehicle

    def has_available_vehicle(self):
        """Check whether this station has an available vehicle."""

        return len(self.available_vehicles) > 0

    def get_location(self):
        return {
            "latitude": self.latitude,
            "longitude": self.longitude
        }