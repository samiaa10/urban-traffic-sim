class Vehicle:

    def __init__(self, route, speed=13.9):
        self.route = route
        self.speed = speed
        self.position = 0
        self.finished = False

    def move(self):
        if self.position < len(self.route) - 1:
            self.position += 1
        else:
            self.finished = True

    def get_current_location(self):
        return self.route[self.position]


if __name__ == "__main__":
    route = [
        {"latitude": 52.6309, "longitude": 1.2974},
        {"latitude": 52.6315, "longitude": 1.2980},
        {"latitude": 52.6320, "longitude": 1.2990},
    ]

    vehicle = Vehicle(route)

    print("Starting location:", vehicle.get_current_location())

    vehicle.move()
    print("After moving:", vehicle.get_current_location())

    vehicle.move()
    print("After moving:", vehicle.get_current_location())

    vehicle.move()
    print("Finished:", vehicle.finished)