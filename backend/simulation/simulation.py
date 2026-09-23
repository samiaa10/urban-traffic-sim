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

    def run(self):
        while not all(vehicle.finished for vehicle in self.vehicles):

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
            print("Vehicle has reached its destination.")

            print(
                "Distance travelled:",
                round(vehicle.distance_travelled),
                "metres"
            )


if __name__ == "__main__":

    route = [
        {"latitude": 52.6309, "longitude": 1.2974},
        {"latitude": 52.6315, "longitude": 1.2980},
        {"latitude": 52.6320, "longitude": 1.2990},
    ]

    simulation = Simulation(route)

    simulation.run()