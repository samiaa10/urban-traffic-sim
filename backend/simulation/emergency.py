import time


class Emergency:

    def __init__(
        self,
        latitude,
        longitude,
        emergency_type="ambulance"
    ):
        self.latitude = latitude
        self.longitude = longitude
        self.emergency_type = emergency_type

        self.active = True
        self.response_time = None
        self.resolved = False

        # Time when the emergency was created
        self.created_at = time.time()

        # Time when a vehicle reaches the emergency
        self.resolved_at = None

    def resolve(self, response_time):
        self.response_time = response_time
        self.resolved = True
        self.active = False
        self.resolved_at = time.time()

    def get_location(self):
        return {
            "latitude": self.latitude,
            "longitude": self.longitude
        }