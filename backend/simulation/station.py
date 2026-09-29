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

        # Vehicles currently available at this station
        self.available_vehicles = []

    def add_vehicle(self, vehicle):
        """Add an emergency vehicle to this station."""

        self.available_vehicles.append(vehicle)

    def get_available_vehicle(self):
        """Return an available vehicle from the station."""

        if not self.available_vehicles:
            return None

        return self.available_vehicles.pop(0)

    def get_location(self):
        return {
            "latitude": self.latitude,
            "longitude": self.longitude
        }