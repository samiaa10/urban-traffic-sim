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

        # Simulated time a vehicle was sent (later than created_at if the
        # emergency waited in the queue, see services.py)
        self.dispatched_at = None

        # Why it was refused, if it was: "no_vehicle" or "no_route"
        self.cancel_reason = None

        # Seconds the vehicle stays at the scene after arriving before it is
        # free again (the command centre's incidents, command.py). 0, the
        # default, frees it the moment it arrives, as before.
        self.on_scene_seconds = 0
        # Simulated time the vehicle left the scene (None until then)
        self.cleared_at = None

    def resolve(self, resolved_time):
        self.resolved_at = resolved_time
        self.response_time = resolved_time - self.created_at
        self.resolved = True
        self.active = False

    @property
    def on_scene(self):
        """Arrived, and the vehicle is still working at the scene."""
        return self.resolved and self.cleared_at is None and self.on_scene_seconds > 0

    def clear(self, cleared_time):
        self.cleared_at = cleared_time

    def get_location(self):
        return {
            "latitude": self.latitude,
            "longitude": self.longitude
        }
