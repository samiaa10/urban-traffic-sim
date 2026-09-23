from vehicle import Vehicle


class Simulation:

    def __init__(self, route):
        self.vehicle = Vehicle(route)

    def run(self):
        while not self.vehicle.finished:

            location = self.vehicle.get_current_location()

            print(
                "Vehicle location:",
                location["latitude"],
                location["longitude"]
            )

            self.vehicle.move()

        print("Vehicle has reached its destination.")


if __name__ == "__main__":

    route = [
        {"latitude": 52.6309, "longitude": 1.2974},
        {"latitude": 52.6315, "longitude": 1.2980},
        {"latitude": 52.6320, "longitude": 1.2990},
    ]

    simulation = Simulation(route)

    simulation.run()