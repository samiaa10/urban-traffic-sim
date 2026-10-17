from backend.simulation.simulation import Simulation
from backend.tests.helpers import make_diamond_graph, coords


def make_simulation_with_ambulance(speed=20):
    """A parked ambulance at station A and a civilian car, on the diamond map."""
    graph = make_diamond_graph()
    simulation = Simulation(coords(graph, ["A", "C"]))   # the civilian car

    station = simulation.add_station(
        graph.nodes["A"]["latitude"], graph.nodes["A"]["longitude"]
    )
    ambulance = simulation.add_emergency_vehicle(
        [station.get_location(), station.get_location()],
        station,
        speed=speed,
    )
    return graph, simulation, station, ambulance


def send_ambulance(graph, simulation, ambulance, path_nodes):
    """Create an emergency at the end of path_nodes and dispatch the ambulance."""
    end = graph.nodes[path_nodes[-1]]
    emergency = simulation.create_emergency(end["latitude"], end["longitude"])
    vehicle = simulation.dispatch_from_nearest_station(emergency)
    vehicle.set_route(coords(graph, path_nodes), reset_distance=True)
    return emergency, vehicle


def run_until_resolved(simulation, emergency, max_steps=500):
    for _ in range(max_steps):
        if emergency.resolved:
            break
        simulation.step()


def test_ambulance_resolves_the_emergency():
    graph, simulation, station, ambulance = make_simulation_with_ambulance()

    emergency, vehicle = send_ambulance(graph, simulation, ambulance, ["A", "B", "D"])
    run_until_resolved(simulation, emergency)

    assert vehicle is ambulance
    assert emergency.resolved
    assert not emergency.active


def test_response_time_is_simulated_seconds():
    graph, simulation, station, ambulance = make_simulation_with_ambulance(speed=20)

    emergency, _ = send_ambulance(graph, simulation, ambulance, ["A", "B", "D"])
    run_until_resolved(simulation, emergency)

    # 680 m at 20 m/s = 34 s (the approximate straight-line distances
    # used by the vehicle make this a little different, so allow 2 s)
    assert abs(emergency.response_time - 34) <= 2


def test_resolves_even_if_one_step_jumps_over_the_target():
    # 200 m per second: each step is far longer than the "arrival" area,
    # which used to make the ambulance skip straight over the emergency.
    graph, simulation, station, ambulance = make_simulation_with_ambulance(speed=200)

    emergency, _ = send_ambulance(graph, simulation, ambulance, ["A", "B", "D"])
    run_until_resolved(simulation, emergency)

    assert emergency.resolved


def test_a_civilian_car_does_not_resolve_an_emergency():
    graph, simulation, station, ambulance = make_simulation_with_ambulance()

    # Emergency on the civilian car's route (at C), but no ambulance sent
    c = graph.nodes["C"]
    emergency = simulation.create_emergency(c["latitude"], c["longitude"])

    for _ in range(200):
        simulation.step()

    assert simulation.vehicles[0].finished      # the car did drive past it
    assert not emergency.resolved


def test_ambulance_is_available_again_after_the_call():
    graph, simulation, station, ambulance = make_simulation_with_ambulance()

    emergency, _ = send_ambulance(graph, simulation, ambulance, ["A", "B", "D"])
    assert not station.has_available_vehicle()   # it is out on a call

    run_until_resolved(simulation, emergency)

    assert station.has_available_vehicle()
    assert ambulance.available
    assert not ambulance.on_emergency


def test_second_emergency_can_reuse_the_ambulance():
    graph, simulation, station, ambulance = make_simulation_with_ambulance()

    first, _ = send_ambulance(graph, simulation, ambulance, ["A", "B", "D"])
    run_until_resolved(simulation, first)

    second, vehicle = send_ambulance(graph, simulation, ambulance, ["A", "C"])
    run_until_resolved(simulation, second)

    assert vehicle is ambulance
    assert second.resolved


def test_cancelled_emergency_frees_the_ambulance():
    graph, simulation, station, ambulance = make_simulation_with_ambulance()

    emergency, _ = send_ambulance(graph, simulation, ambulance, ["A", "B", "D"])
    simulation.cancel_emergency(emergency)

    assert station.has_available_vehicle()
    assert emergency not in simulation.get_emergencies()
