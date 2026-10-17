class Emergency:

    def __init__(
        self,
        latitude,
        longitude,
        emergency_type="ambulance",
        created_time=0
    ):
        self.latitude = latitude
        self.longitude = longitude
        self.emergency_type = emergency_type

        self.active = True
        self.response_time = None
        self.resolved = False

        # Simulated time (in seconds) when the emergency was created.
        # We use simulation time, not the computer's clock, so that
        # response times don't depend on how fast the frontend polls.
        self.created_at = created_time

        # Simulated time when a vehicle reached the emergency
        self.resolved_at = None

        # The emergency vehicle that was sent to this emergency
        self.assigned_vehicle = None

    def resolve(self, resolved_time):
        self.resolved_at = resolved_time
        self.response_time = resolved_time - self.created_at
        self.resolved = True
        self.active = False

    def get_location(self):
        return {
            "latitude": self.latitude,
            "longitude": self.longitude
        }
