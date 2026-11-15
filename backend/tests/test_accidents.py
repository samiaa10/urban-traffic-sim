"""Accidents (slow, reduced-capacity roads that clear by themselves) and
vehicles driving real road lengths."""

import pytest

from backend.routing.a_star import get_segment
from backend.routing.travel_time import RoadNetwork
from backend.simulation import dispatch
from backend.simulation.accidents import SEVERITIES, Accident
from backend.simulation.simulation import (
    Simulation,
    effective_vehicle_count,
    speed_factor_on_road,
)
from backend.tests.helpers import coords, make_diamond_graph


def simulation_on(graph, path, vehicles=1):
    simulation = Simulation(coords(graph, path))
    for _ in range(vehicles - 1):
        simulation.add_vehicle(coords(graph, path))
    return simulation


# ------------------------------------------------------------
# The accident itself
# ------------------------------------------------------------

def test_unknown_severity_is_refused():
    with pytest.raises(ValueError):
        Accident(("a", "b"), "catastrophic", 0)


@pytest.mark.parametrize("duration", [0, -5, float("inf")])
def test_duration_must_be_a_positive_number(duration):
    with pytest.raises(ValueError):
        Accident(("a", "b"), "minor", 0, duration)


def test_default_duration_comes_from_the_severity():
    accident = Accident(("a", "b"), "severe", started_at=100)

    assert accident.clears_at == 100 + SEVERITIES["severe"]["default_duration"]


def test_worse_accidents_slow_traffic_more_and_take_more_capacity():
    minor, moderate, severe = (SEVERITIES[s] for s in ("minor", "moderate", "severe"))

    assert minor["speed_factor"] > moderate["speed_factor"] > severe["speed_factor"] > 0
    assert minor["capacity_factor"] > moderate["capacity_factor"] > severe["capacity_factor"] > 0


# ------------------------------------------------------------
# How an accident changes a road's speed
# ------------------------------------------------------------

def test_without_an_accident_the_old_speed_rules_apply():
    assert [speed_factor_on_road(n) for n in range(5)] == [1.0, 1.0, 0.6, 0.3, 0.3]


def test_a_lone_vehicle_is_slowed_by_the_accident_but_not_by_capacity():
    accident = Accident(("a", "b"), "moderate", 0)

    assert effective_vehicle_count(1, accident) == 1
    assert speed_factor_on_road(1, accident) == pytest.approx(0.45)


def test_lost_capacity_makes_fewer_vehicles_jam_the_road():
    accident = Accident(("a", "b"), "moderate", 0)   # half the capacity

    # 2 vehicles feel like 4: heavy (0.3) instead of moderate (0.6)
    assert effective_vehicle_count(2, accident) == 4
    assert speed_factor_on_road(2, accident) == pytest.approx(0.3 * 0.45)


def test_vehicle_on_an_accident_road_covers_less_ground():
    graph = make_diamond_graph()
    road = get_segment(graph, "A", "B")

    clear = simulation_on(graph, ["A", "B", "D"])
    crash = simulation_on(graph, ["A", "B", "D"])
    crash.add_accident(road, "moderate")

    clear.step()
    crash.step()

    assert crash.vehicles[0].distance_travelled == pytest.approx(
        clear.vehicles[0].distance_travelled * 0.45
    )


def test_an_accident_on_another_road_changes_nothing():
    graph = make_diamond_graph()

    clear = simulation_on(graph, ["A", "B", "D"])
    crash = simulation_on(graph, ["A", "B", "D"])
    crash.add_accident(get_segment(graph, "A", "C"), "severe")

    for _ in range(10):
        clear.step()
        crash.step()

    assert crash.vehicles[0].distance_travelled == pytest.approx(
        clear.vehicles[0].distance_travelled
    )


def test_congestion_level_shows_the_lost_capacity():
    graph = make_diamond_graph()
    road = get_segment(graph, "A", "B")

    simulation = simulation_on(graph, ["A", "B"], vehicles=2)
    assert simulation.get_congestion()[road]["level"] == "moderate"

    simulation.add_accident(road, "moderate")
    assert simulation.get_congestion()[road]["level"] == "heavy"


def test_eta_includes_the_accident_slowdown():
    graph = make_diamond_graph()
    simulation = simulation_on(graph, ["A", "B", "D"])
    vehicle = simulation.vehicles[0]

    before = dispatch.remaining_seconds(simulation, vehicle)
    simulation.add_accident(get_segment(graph, "B", "D"), "moderate")
    after = dispatch.remaining_seconds(simulation, vehicle)

    # The B-D half now takes 1 / 0.45 times as long
    half = before / 2
    assert after == pytest.approx(half + half / 0.45, rel=1e-3)


# ------------------------------------------------------------
# Accidents start and clear
# ------------------------------------------------------------

def test_an_accident_clears_by_itself_after_its_duration():
    graph = make_diamond_graph()
    road = get_segment(graph, "A", "B")
    simulation = simulation_on(graph, ["A", "C", "D"])

    simulation.add_accident(road, "minor", duration=5)

    for _ in range(4):
        simulation.step()
    assert road in simulation.accidents

    simulation.step()
    assert road not in simulation.accidents
    assert simulation.events[-1] == {
        "time": 5, "type": "accident_cleared", "segment": road
    }


def test_traffic_speeds_up_again_once_the_accident_clears():
    graph = make_diamond_graph()
    road = get_segment(graph, "A", "B")
    simulation = Simulation(coords(graph, ["A", "B", "D"]))
    simulation.add_accident(road, "severe", duration=2)

    simulation.step()
    slow = simulation.vehicles[0].speed_factor
    simulation.step()
    simulation.step()

    assert slow == pytest.approx(0.2)
    assert simulation.vehicles[0].speed_factor == pytest.approx(1.0)


def test_an_accident_can_be_cleared_early():
    graph = make_diamond_graph()
    road = get_segment(graph, "A", "B")
    simulation = simulation_on(graph, ["A", "B"])

    simulation.add_accident(road)

    assert simulation.clear_accident(road) is True
    assert simulation.accidents == {}
    assert simulation.clear_accident(road) is False


def test_a_new_accident_on_the_same_road_replaces_the_old_one():
    graph = make_diamond_graph()
    road = get_segment(graph, "A", "B")
    simulation = simulation_on(graph, ["A", "B"])

    simulation.add_accident(road, "minor")
    simulation.add_accident(road, "severe")

    assert len(simulation.accidents) == 1
    assert simulation.accidents[road].severity == "severe"


def test_accident_works_whichever_way_the_road_is_given():
    graph = make_diamond_graph()
    road = get_segment(graph, "A", "B")
    simulation = simulation_on(graph, ["A", "B"])

    simulation.add_accident((road[1], road[0]))

    assert road in simulation.accidents


def test_closing_and_reopening_roads_is_recorded_once():
    graph = make_diamond_graph()
    road = get_segment(graph, "A", "B")
    simulation = simulation_on(graph, ["A", "C"])

    simulation.block_segment(road)
    simulation.block_segment(road)       # already closed: nothing new
    simulation.unblock_segment(road)
    simulation.unblock_segment(road)     # already open: nothing new

    assert [event["type"] for event in simulation.events] == [
        "road_closed", "road_reopened"
    ]


# ------------------------------------------------------------
# Real road lengths
# ------------------------------------------------------------

def test_vehicles_measure_straight_lines_unless_given_road_lengths():
    graph = make_diamond_graph()
    simulation = simulation_on(graph, ["A", "B"])
    vehicle = simulation.vehicles[0]
    a, b = vehicle.route

    assert vehicle.segment_distance(a, b) == pytest.approx(vehicle.calculate_distance(a, b))


def test_with_road_lengths_a_vehicle_drives_the_real_road():
    graph = make_diamond_graph()     # A-B is 340 m of road
    simulation = simulation_on(graph, ["A", "B"])
    simulation.use_road_lengths(RoadNetwork(graph).length_between_points)
    vehicle = simulation.vehicles[0]

    steps = 0
    while not vehicle.finished:
        simulation.step()
        steps += 1

    assert vehicle.distance_travelled == pytest.approx(340)
    assert steps == 25          # 340 m at 13.9 m/s = 24.5 s


def test_vehicles_added_later_also_drive_real_roads():
    graph = make_diamond_graph()
    simulation = simulation_on(graph, ["A", "B"])
    simulation.use_road_lengths(RoadNetwork(graph).length_between_points)

    later = simulation.add_vehicle(coords(graph, ["A", "C"]))

    assert later.segment_distance(*later.route) == pytest.approx(600)


def test_position_on_the_map_follows_progress_along_the_real_road():
    graph = make_diamond_graph()
    simulation = simulation_on(graph, ["A", "B"])
    simulation.use_road_lengths(RoadNetwork(graph).length_between_points)
    vehicle = simulation.vehicles[0]

    vehicle.distance_on_segment = 170       # half of 340 m
    here = vehicle.get_current_location()

    assert here["longitude"] == pytest.approx((1.2900 + 1.2950) / 2)


def test_remaining_distance_uses_the_real_roads():
    graph = make_diamond_graph()
    simulation = simulation_on(graph, ["A", "B", "D"])
    simulation.use_road_lengths(RoadNetwork(graph).length_between_points)
    vehicle = simulation.vehicles[0]

    assert dispatch.remaining_distance(vehicle) == pytest.approx(680)

    vehicle.distance_on_segment = 100
    assert dispatch.remaining_distance(vehicle) == pytest.approx(580)
