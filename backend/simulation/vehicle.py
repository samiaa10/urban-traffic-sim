import math


class Vehicle:

    def __init__(
        self,
        route,
        speed=13.9,
        vehicle_type="normal"
    ):
        # A vehicle that cannot move would never arrive, and ETA
        # calculations divide by the speed, so reject it up front.
        if not speed > 0 or speed == float("inf"):
            raise ValueError("Vehicle speed must be a positive number.")

        self.route = route
        self.speed = speed
        self.vehicle_type = vehicle_type

        self.position = 0
        self.distance_on_segment = 0
        self.distance_travelled = 0

        self.finished = False

        # Simulated seconds spent on this trip so far. It counts every
        # second the simulation ran while the vehicle had not arrived,
        # including time spent crawling through traffic or waiting at a
        # closed road, so congestion shows up as a longer journey.
        self.journey_seconds = 0

        # Fraction of full speed the vehicle drove at during the last
        # move (1.0 = free flowing, lower = held up by traffic).
        self.speed_factor = 1.0

        # How many times the route was recalculated during this trip
        # (because of a closure). Reset when a new trip starts.
        self.reroutes = 0

        # Emergency vehicle state
        self.on_emergency = False
        self.available = True

        # The station this vehicle belongs to (emergency vehicles only)
        self.home_station = None

    def set_route(self, route, reset_distance=False):
        """Give the vehicle a new route and start it from the beginning."""

        self.route = route
        self.position = 0
        self.distance_on_segment = 0
        self.finished = False

        if reset_distance:
            self.distance_travelled = 0
            self.journey_seconds = 0
            self.reroutes = 0

    def calculate_distance(self, point1, point2):

        lat1 = math.radians(point1["latitude"])
        lon1 = math.radians(point1["longitude"])

        lat2 = math.radians(point2["latitude"])
        lon2 = math.radians(point2["longitude"])

        earth_radius = 6371000

        x = (
            lon2 - lon1
        ) * math.cos(
            (lat1 + lat2) / 2
        )

        y = lat2 - lat1

        return earth_radius * math.sqrt(
            x * x + y * y
        )

    def move(self, time_seconds=1, is_blocked=None, speed_factor=None):
        """Drive for time_seconds.

        is_blocked(point_a, point_b) is an optional function that says
        whether the road between two points is closed. The vehicle stops
        at the junction instead of driving into a closed road.

        speed_factor(point_a, point_b) is an optional function that says
        how fast traffic lets a vehicle drive along that road, as a
        fraction of its full speed (1.0 = free flowing, 0.5 = half
        speed). Without it the vehicle always drives at full speed.
        """

        if self.finished:
            return

        time_left = time_seconds

        while time_left > 1e-9:

            if self.position >= len(self.route) - 1:

                self.finished = True
                break

            current_point = self.route[self.position]
            next_point = self.route[self.position + 1]

            segment_distance = (
                self.calculate_distance(
                    current_point,
                    next_point
                )
            )

            remaining_segment = (
                segment_distance
                - self.distance_on_segment
            )

            # How fast can we go on THIS road? Traffic slows us down.
            factor = 1.0

            if speed_factor is not None:
                factor = speed_factor(current_point, next_point)

            self.speed_factor = factor

            road_speed = self.speed * factor

            # Distance we could cover with the time we have left
            reachable = road_speed * time_left

            if reachable >= remaining_segment:

                # Reach the next junction, then carry on with the
                # time that is left over.
                time_left -= remaining_segment / road_speed

                self.distance_travelled += (
                    remaining_segment
                )

                self.position += 1
                self.distance_on_segment = 0

                # Stop at the junction if the next road is closed
                if (
                    is_blocked is not None
                    and self.position < len(self.route) - 1
                    and is_blocked(
                        self.route[self.position],
                        self.route[self.position + 1]
                    )
                ):
                    break

            else:

                self.distance_on_segment += reachable

                self.distance_travelled += reachable

                time_left = 0

        if self.position >= len(self.route) - 1:

            self.finished = True

    def get_current_location(self):

        if self.position >= len(self.route) - 1:
            return self.route[-1]

        current_point = self.route[self.position]
        next_point = self.route[self.position + 1]

        segment_distance = (
            self.calculate_distance(
                current_point,
                next_point
            )
        )

        if segment_distance == 0:
            return current_point

        fraction = (
            self.distance_on_segment
            / segment_distance
        )

        latitude = (
            current_point["latitude"]
            + (
                next_point["latitude"]
                - current_point["latitude"]
            ) * fraction
        )

        longitude = (
            current_point["longitude"]
            + (
                next_point["longitude"]
                - current_point["longitude"]
            ) * fraction
        )

        return {
            "latitude": latitude,
            "longitude": longitude
        }