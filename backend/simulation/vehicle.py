import math


class Vehicle:

    def __init__(self, route, speed=13.9):
        self.route = route
        self.speed = speed

        self.position = 0
        self.distance_on_segment = 0
        self.distance_travelled = 0

        self.finished = False

    def calculate_distance(self, point1, point2):
        lat1 = math.radians(point1["latitude"])
        lon1 = math.radians(point1["longitude"])

        lat2 = math.radians(point2["latitude"])
        lon2 = math.radians(point2["longitude"])

        earth_radius = 6371000

        x = (lon2 - lon1) * math.cos((lat1 + lat2) / 2)
        y = lat2 - lat1

        return earth_radius * math.sqrt(x * x + y * y)

    def move(self, time_seconds=1):

        if self.finished:
            return

        distance_to_travel = self.speed * time_seconds

        while distance_to_travel > 0:

            if self.position >= len(self.route) - 1:
                self.finished = True
                break

            current_point = self.route[self.position]
            next_point = self.route[self.position + 1]

            segment_distance = self.calculate_distance(
                current_point,
                next_point
            )

            remaining_segment = (
                segment_distance - self.distance_on_segment
            )

            if distance_to_travel >= remaining_segment:

                distance_to_travel -= remaining_segment
                self.distance_travelled += remaining_segment

                self.position += 1
                self.distance_on_segment = 0

            else:

                self.distance_on_segment += distance_to_travel
                self.distance_travelled += distance_to_travel

                distance_to_travel = 0

                # We are part-way between two route points.
                break

        if self.position >= len(self.route) - 1:
            self.finished = True

    def get_current_location(self):
        return self.route[self.position]