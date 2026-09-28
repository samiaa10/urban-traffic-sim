from backend.simulation.vehicle import Vehicle


class Simulation:

    def __init__(self, route):
        self.vehicles = []

        # Create the first vehicle
        self.add_vehicle(route)

    def add_vehicle(self, route, speed=13.9):
        vehicle = Vehicle(route, speed)
        self.vehicles.append(vehicle)

        return vehicle

    def step(self):
        for vehicle in self.vehicles:
            if not vehicle.finished:
                vehicle.move(1)

    def get_vehicle_positions(self):
        positions = []

        for vehicle in self.vehicles:
            positions.append(
                vehicle.get_current_location()
            )

        return positions

    def get_traffic_density(self):
        traffic = {}

        for vehicle in self.vehicles:

            # Ignore vehicles that have finished
            if vehicle.finished:
                continue

            # Current road segment
            if vehicle.position >= len(vehicle.route) - 1:
                continue

            current_point = vehicle.route[vehicle.position]
            next_point = vehicle.route[vehicle.position + 1]

            # Create a segment identifier
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

            # Make the segment direction-independent
            segment = tuple(sorted(segment))

            if segment not in traffic:
                traffic[segment] = 0

            traffic[segment] += 1

        return traffic

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

    def run(self):

        while not all(
            vehicle.finished
            for vehicle in self.vehicles
        ):

            for vehicle in self.vehicles:

                if not vehicle.finished:

                    location = (
                        vehicle.get_current_location()
                    )

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