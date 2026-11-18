"""Traffic signals (simulation/signals.py): conflicting movements, phases
and their sequence, vehicles waiting at red and queues in all three traffic
models, the adaptive controller (including no starvation), emergency
priority, and signals in scenarios."""

import random

import pytest

from backend.routing.graph import Graph
from backend.routing.road_attributes import MPH, RoadInfo
from backend.routing.travel_time import RoadNetwork
from backend.scenarios.runner import World, run_scenario
from backend.scenarios.spec import ScenarioSpec, validate_against_graph
from backend.simulation.road_model import CAR_TOP_SPEED, make_road_model
from backend.simulation.signal_sites import load_signal_junctions, signal_junctions
from backend.simulation.signals import (
    ADAPTIVE,
    ALL_RED,
    AMBER,
    FIXED,
    GREEN,
    PRIORITY_MIN_GREEN,
    SignalSystem,
    SignalTiming,
    group_phases,
    make_signals,
    movements_conflict,
)
from backend.simulation.simulation import Simulation
from backend.tests.helpers import make_grid_graph

MODELS = ("simple", "osm", "ca")

# Bearings of the arms of a crossroads (from the junction)
N, E, S, W = 0.0, 90.0, 180.0, 270.0


def move(approach, exit_):
    """A movement arriving from `approach` and leaving towards `exit_`."""
    return (approach, approach, exit_, exit_)


# ------------------------------------------------------------
# Conflicting movements and phases
# ------------------------------------------------------------

def test_a_left_turn_and_the_opposite_straight_ahead_do_not_conflict():
    # From the north turning left (east, UK) / from the south straight on
    assert not movements_conflict(move(N, E), move(S, N))


def test_a_right_turn_and_the_oncoming_straight_ahead_conflict():
    # From the north turning right (west) crosses the southbound... the
    # northbound traffic from the south
    assert movements_conflict(move(N, W), move(S, N))


def test_crossing_straight_ahead_movements_conflict():
    assert movements_conflict(move(N, S), move(E, W))


def test_two_movements_into_the_same_exit_conflict():
    assert movements_conflict(move(N, S), move(E, S))


def test_movements_from_the_same_approach_never_conflict():
    assert not movements_conflict(move(N, S), move(N, W))


def test_opposite_straight_ahead_movements_share_a_phase():
    phases = group_phases([[move(N, S)], [move(S, N)]])
    assert phases == [[0, 1]]


def test_right_turns_split_a_crossroads_into_one_phase_per_approach():
    arms = (N, E, S, W)
    movements = [[move(a, b) for b in arms if b != a] for a in arms]
    assert group_phases(movements) == [[0], [1], [2], [3]]


def test_no_norwich_signal_phase_lets_conflicting_movements_go_together():
    import pickle

    from backend.simulation.signal_sites import GRAPH_FILE

    with open(GRAPH_FILE, "rb") as file:
        network = RoadNetwork(pickle.load(file))
    system = SignalSystem(network, load_signal_junctions(), FIXED)

    assert len(system.junctions) >= 100
    for junction in system.junctions:
        for phase in junction.phases:
            for a in phase:
                for b in phase:
                    for m1 in junction.movements[a]:
                        for m2 in junction.movements[b]:
                            assert not movements_conflict(m1, m2)


def test_signals_are_snapped_to_the_nearest_junction_within_reach():
    graph = make_grid_graph(3)
    junction = graph.nodes[4]   # the middle of the grid: four roads
    near = (junction["latitude"] + 0.0001, junction["longitude"])   # ~11 m away
    far = (junction["latitude"] + 0.001, junction["longitude"] + 0.0015)
    assert signal_junctions(graph, [near, far]) == {4: 1}


# ------------------------------------------------------------
# A crossroads to drive through
# ------------------------------------------------------------

def crossroads(arm=200, lanes=1):
    """Junction 0 with arms 1 (north), 2 (east), 3 (south), 4 (west)."""
    graph = Graph()
    centre = (52.63, 1.29)
    d = arm / 111_320
    points = {
        0: centre, 1: (centre[0] + d, centre[1]), 2: (centre[0], centre[1] + d * 1.64),
        3: (centre[0] - d, centre[1]), 4: (centre[0], centre[1] - d * 1.64),
    }
    for node, (lat, lon) in points.items():
        graph.add_node(node, lat, lon)
    for node in (1, 2, 3, 4):
        for a, b in ((node, 0), (0, node)):
            graph.add_edge(a, b, arm)
            graph.road_info[(a, b)] = RoadInfo(
                highway="primary", speed_limit=30 * MPH, speed_limit_tagged=True,
                lanes=lanes, lanes_tagged=True, oneway=False, name=None,
            )
    return graph


def setup(model="osm", controller=FIXED, priority=False, timing=None, lanes=1, nodes=(0,)):
    network = RoadNetwork(crossroads(lanes=lanes))
    simulation = Simulation(
        [network.point(1), network.point(1)], road_model=make_road_model(model, network, seed=1)
    )
    simulation.vehicles[0].finished = True
    simulation.use_road_lengths(network.length_between_points)
    signals = make_signals(network, list(nodes), controller, priority, timing)
    simulation.signals = signals
    return network, simulation, signals


def car(simulation, network, path, **kwargs):
    return simulation.add_vehicle(network.points(path), speed=CAR_TOP_SPEED, **kwargs)


def approach_of(signals, network, node):
    junction = signals.junctions[0]
    return junction, junction.approach_of(network.position[node])


def set_green(junction, approach):
    junction.phase = junction.phase_of[approach]
    junction.stage, junction.timer = GREEN, 0.0


def set_red(junction, approach):
    other = next(p for p in range(len(junction.phases)) if p != junction.phase_of[approach])
    junction.phase = other
    junction.stage, junction.timer = GREEN, 0.0


# ------------------------------------------------------------
# The signal sequence
# ------------------------------------------------------------

def test_fixed_time_goes_green_amber_all_red_then_the_next_phase():
    timing = SignalTiming(green=10, amber=3, all_red=2)
    _, simulation, signals = setup(timing=timing)
    junction = signals.junctions[0]
    junction.phase, junction.stage, junction.timer = 0, GREEN, 0.0

    seen = []
    for _ in range(31):
        simulation.step()
        seen.append((junction.phase, junction.stage))

    assert seen[:9] == [(0, GREEN)] * 9
    assert seen[9:12] == [(0, AMBER)] * 3
    assert seen[12:14] == [(0, ALL_RED)] * 2
    assert seen[14] == (1, GREEN)
    # Never straight from one green to another
    for (p1, s1), (p2, s2) in zip(seen, seen[1:]):
        if s1 == GREEN and s2 == GREEN:
            assert p1 == p2


def test_only_one_phase_is_ever_green():
    network, simulation, signals = setup(controller=ADAPTIVE)
    junction = signals.junctions[0]
    generator = random.Random(3)
    for second in range(400):
        if generator.random() < 0.3:
            a, b = generator.sample([1, 2, 3, 4], 2)
            car(simulation, network, [a, 0, b])
        simulation.step()
        green = [a for a in range(4) if junction.is_green(a)]
        assert len({junction.phase_of[a] for a in green}) <= 1


# ------------------------------------------------------------
# Waiting at red and queues, in every traffic model
# ------------------------------------------------------------

@pytest.mark.parametrize("model", MODELS)
def test_a_vehicle_waits_at_a_red_light_and_goes_on_green(model):
    network, simulation, signals = setup(model, timing=SignalTiming(green=60))
    junction, approach = approach_of(signals, network, 1)
    set_red(junction, approach)
    vehicle = car(simulation, network, [1, 0, 3])

    for _ in range(50):
        simulation.step()
    # Still on its first road, just before the stop line, waiting
    assert vehicle.position == 0
    assert vehicle.distance_on_segment > 190
    assert vehicle.held_at_signal
    assert vehicle.signal_wait_seconds > 0

    set_green(junction, approach)
    for _ in range(40):
        simulation.step()
    assert vehicle.finished


@pytest.mark.parametrize("model", MODELS)
def test_a_queue_forms_at_red_and_leaves_one_by_one(model):
    network, simulation, signals = setup(model, timing=SignalTiming(green=120))
    junction, approach = approach_of(signals, network, 1)
    set_red(junction, approach)
    cars = []
    for _ in range(8):
        cars.append(car(simulation, network, [1, 0, 3]))
        for _ in range(3):
            simulation.step()
    for _ in range(60):
        simulation.step()
    assert junction.queue[approach] == 8
    assert all(c.position == 0 for c in cars)

    set_green(junction, approach)
    passed = []
    for second in range(40):
        simulation.step()
        passed.append(sum(1 for c in cars if c.position >= 1 or c.finished))
    # Not all at once: they leave over several seconds
    assert passed[0] < 8 and passed[-1] == 8
    if model != "ca":
        # The saturation flow: one car a lane every 2 s, the first 2 s
        # after the green starts (start-up)
        assert passed[:6] == [0, 1, 1, 2, 2, 3]


def test_the_ca_queue_is_a_line_of_stopped_cars():
    network, simulation, signals = setup("ca", timing=SignalTiming(green=120))
    junction, approach = approach_of(signals, network, 1)
    set_red(junction, approach)
    for _ in range(6):
        car(simulation, network, [1, 0, 3])
        for _ in range(3):
            simulation.step()
    for _ in range(80):
        simulation.step()
    road = simulation.road_model.engine.roads[(network.position[1], network.position[0])]
    fronts = [c.front for c in road.lanes[0]]
    assert fronts == [road.cells - 1 - 3 * i for i in range(6)]


def test_no_vehicle_enters_a_junction_whose_exit_is_full():
    network, simulation, signals = setup("osm", timing=SignalTiming(green=120))
    junction, approach = approach_of(signals, network, 1)
    set_green(junction, approach)
    exit_key = (network.position[0], network.position[3])
    capacity = simulation.road_model._road_capacity(exit_key)
    # Fill the exit road with stationary traffic (a closure beyond it)
    simulation.block_segment((network.position[0], network.position[3]))
    for _ in range(int(capacity) + 1):
        car(simulation, network, [0, 3])
    vehicle = car(simulation, network, [1, 0, 3])
    for _ in range(40):
        simulation.step()
    assert vehicle.position == 0 and vehicle.held_at_signal


# ------------------------------------------------------------
# Adaptive control
# ------------------------------------------------------------

def test_adaptive_switches_to_the_approach_with_traffic():
    network, simulation, signals = setup(controller=ADAPTIVE)
    junction, approach = approach_of(signals, network, 1)
    set_red(junction, approach)
    vehicle = car(simulation, network, [1, 0, 3])
    for _ in range(60):
        simulation.step()
    # min_green 7 s, then nobody on the green phase: it switches
    assert vehicle.finished or vehicle.position >= 1
    assert junction.switches >= 1


def test_adaptive_rests_on_green_with_no_traffic():
    _, simulation, signals = setup(controller=ADAPTIVE)
    junction = signals.junctions[0]
    phase = junction.phase
    for _ in range(200):
        simulation.step()
    assert junction.phase == phase and junction.stage == GREEN and junction.switches == 0


def test_adaptive_ends_a_busy_green_at_max_green():
    timing = SignalTiming(min_green=5, max_green=20, max_wait=120)
    network, simulation, signals = setup(controller=ADAPTIVE, timing=timing)
    junction, north = approach_of(signals, network, 1)
    _, east = approach_of(signals, network, 2)
    set_green(junction, north)
    car(simulation, network, [2, 0, 4])
    for second in range(40):
        if second % 2 == 0:
            car(simulation, network, [1, 0, 3])
        simulation.step()
        if junction.stage != GREEN:
            break
    assert junction.timer == 0 and second + 1 <= timing.max_green + 1


def test_adaptive_never_starves_a_quiet_approach():
    timing = SignalTiming(min_green=5, max_green=60, max_wait=60)
    network, simulation, signals = setup(controller=ADAPTIVE, timing=timing)
    junction, north = approach_of(signals, network, 1)
    _, south = approach_of(signals, network, 3)
    set_green(junction, north)
    # Heavy, steady traffic from the north and south; one car from the west
    quiet = car(simulation, network, [4, 0, 2])
    for second in range(300):
        if second % 2 == 0:
            car(simulation, network, [1, 0, 3])
            car(simulation, network, [3, 0, 1])
        simulation.step()
        if quiet.finished:
            break
    assert quiet.finished
    # It waited no longer than max_wait plus one phase change and the drive
    assert quiet.signal_wait_seconds <= timing.max_wait + timing.max_green + 10


def test_signals_are_deterministic():
    def run():
        network, simulation, signals = setup("ca", controller=ADAPTIVE)
        generator = random.Random(9)
        for _ in range(300):
            if generator.random() < 0.4:
                a, b = generator.sample([1, 2, 3, 4], 2)
                car(simulation, network, [a, 0, b])
            simulation.step()
        return [v.journey_seconds for v in simulation.vehicles], signals.summary(simulation.vehicles)

    assert run() == run()


# ------------------------------------------------------------
# Emergency vehicles
# ------------------------------------------------------------

def ambulance(simulation, network, path):
    vehicle = simulation.add_vehicle(network.points(path), speed=20, vehicle_type="ambulance")
    vehicle.on_emergency = True
    vehicle.available = False
    return vehicle


@pytest.mark.parametrize("model", MODELS)
def test_ordinary_cars_never_cross_on_red(model):
    network, simulation, signals = setup(model, timing=SignalTiming(green=120))
    junction, approach = approach_of(signals, network, 1)
    set_red(junction, approach)
    vehicle = car(simulation, network, [1, 0, 3])
    for _ in range(110):
        simulation.step()
    assert vehicle.position == 0


@pytest.mark.parametrize("model", MODELS)
def test_without_priority_an_ambulance_crosses_red_with_caution(model):
    timing = SignalTiming(green=120, emergency_red_wait=5)
    network, simulation, signals = setup(model, timing=timing)
    junction, approach = approach_of(signals, network, 1)
    set_red(junction, approach)
    vehicle = ambulance(simulation, network, [1, 0, 3])
    for _ in range(60):
        simulation.step()
    assert vehicle.finished
    # It stopped for the caution wait, and the light never changed for it
    assert vehicle.signal_wait_seconds >= 5
    assert junction.priority_requests == 0


def test_priority_turns_the_light_green_safely_for_an_approaching_ambulance():
    timing = SignalTiming(green=120)
    network, simulation, signals = setup(controller=FIXED, priority=True, timing=timing)
    junction, approach = approach_of(signals, network, 1)
    set_red(junction, approach)
    vehicle = ambulance(simulation, network, [1, 0, 3])

    stages = []
    for _ in range(40):
        simulation.step()
        stages.append((junction.phase, junction.stage))
        if vehicle.finished:
            break

    assert vehicle.finished
    assert junction.priority_requests >= 1
    # The old green ended through amber and all-red before the new one
    first_new = next(i for i, (p, s) in enumerate(stages) if p == junction.phase_of[approach] and s == GREEN)
    assert [s for _, s in stages[first_new - 5:first_new]] == [AMBER] * 3 + [ALL_RED] * 2
    assert first_new >= PRIORITY_MIN_GREEN - 1
    # It got its green before arriving, so it never had to stop
    assert vehicle.signal_wait_seconds == 0


def test_priority_holds_a_green_the_ambulance_already_has():
    timing = SignalTiming(green=10)
    network, simulation, signals = setup(controller=FIXED, priority=True, timing=timing)
    junction, approach = approach_of(signals, network, 1)
    set_green(junction, approach)
    junction.timer = 9.0   # about to end
    vehicle = ambulance(simulation, network, [1, 0, 3])
    for _ in range(30):
        simulation.step()
        if vehicle.finished:
            break
    assert vehicle.finished and vehicle.signal_wait_seconds == 0


# ------------------------------------------------------------
# Scenarios
# ------------------------------------------------------------

def test_specs_without_signals_keep_their_fingerprint():
    plain = {"name": "a", "seed": 1, "demand": {"vehicles": 10}}
    assert ScenarioSpec(**plain).fingerprint() == ScenarioSpec(**plain, signals=None).fingerprint()
    assert ScenarioSpec(**plain).fingerprint() != ScenarioSpec(**plain, signals={"controller": "fixed"}).fingerprint()


def test_signal_junctions_must_be_on_the_map():
    graph = make_grid_graph(4)
    spec = ScenarioSpec(name="a", seed=1, signals={"junctions": [5, 999]})
    assert validate_against_graph(spec, graph) == ["signals.junctions: junction 999 is not on the map"]


@pytest.mark.parametrize("model", MODELS)
def test_signals_change_journey_times_in_a_scenario(model):
    world = World(make_grid_graph(6))
    base = {"name": "s", "seed": 2, "demand": {"vehicles": 120, "spread_seconds": 60}, "traffic_model": model}
    junctions = [n for n in world.graph.nodes if 0 < n % 6 < 5 and 6 <= n < 30]

    plain = run_scenario(world, base)
    fixed = run_scenario(world, {**base, "signals": {"controller": "fixed", "junctions": junctions}})
    again = run_scenario(world, {**base, "signals": {"controller": "fixed", "junctions": junctions}})

    assert plain["signals"] is None
    assert fixed["signals"]["junctions"] == len(junctions)
    assert fixed["signals"]["crossings"] > 0
    assert fixed["summary"]["trips_finished"] == plain["summary"]["trips_finished"] == 120
    total = lambda result: sum(result["trips"]["journey_seconds"])
    assert total(fixed) > total(plain)
    assert fixed["trips"]["journey_seconds"] == again["trips"]["journey_seconds"]
