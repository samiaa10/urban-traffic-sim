"""The scenario engine (backend/scenarios): spec, runner, impact measures.

Runs full simulations on a seeded 8 x 8 grid map (helpers.make_grid_graph),
so no Norwich data is needed and each scenario takes well under a second.
"""

import math

import pytest

from backend.scenarios.impact import area_cell, cell_bounds, percentile
from backend.scenarios.runner import (
    FINISHED,
    UNFINISHED,
    UNROUTABLE,
    ScenarioError,
    World,
    run_scenario,
)
from backend.scenarios.spec import ScenarioSpec
from backend.tests.helpers import make_grid_graph


@pytest.fixture(scope="module")
def world():
    return World(make_grid_graph(size=8, seed=7))


def spec(**overrides):
    base = {"name": "test", "seed": 4, "demand": {"vehicles": 60}}
    base.update(overrides)
    return base


def busiest_roads(result, count):
    """(start, destination) junction pairs of the most delayed real roads."""

    roads = [
        (road["start_node"], road["end_node"])
        for road in result["roads"]
        if road["start_node"] is not None and road["end_node"] is not None
    ]
    return roads[:count]


def without_execution(result):
    return {k: v for k, v in result.items() if k != "execution"}


# ------------------------------------------------------------
# Reproducibility
# ------------------------------------------------------------

def test_the_same_spec_gives_exactly_the_same_result(world):
    first = run_scenario(world, spec())
    second = run_scenario(world, spec())

    assert without_execution(first) == without_execution(second)


def test_a_different_seed_gives_different_trips(world):
    first = run_scenario(world, spec(seed=4))
    second = run_scenario(world, spec(seed=5))

    assert first["trips"]["origin"] != second["trips"]["origin"]


def test_emergencies_do_not_change_the_trips(world):
    """Each kind of randomness has its own stream."""

    station = world.network.position[0]
    plain = run_scenario(world, spec())
    with_emergencies = run_scenario(world, spec(
        stations=[{"latitude": station[0], "longitude": station[1]}],
        emergencies={"count": 3, "first_at": 10, "interval_seconds": 20},
    ))

    assert plain["trips"]["origin"] == with_emergencies["trips"]["origin"]
    assert plain["trips"]["destination"] == with_emergencies["trips"]["destination"]


def test_closures_keep_every_trip_in_its_place(world):
    """A scenario with closures has the same trips in the same order, so
    it can be compared trip by trip with its baseline."""

    baseline = run_scenario(world, spec())
    a, b = busiest_roads(baseline, 1)[0]
    closed = run_scenario(world, spec(closures=[{"start": a, "destination": b}]))

    assert closed["trip_signature"] == baseline["trip_signature"]
    assert closed["trips"]["origin"] == baseline["trips"]["origin"]
    assert closed["spec_fingerprint"] != baseline["spec_fingerprint"]


# ------------------------------------------------------------
# What is measured
# ------------------------------------------------------------

def test_a_vehicle_alone_loses_no_time(world):
    result = run_scenario(world, spec(demand={"vehicles": 1}))
    summary = result["summary"]

    assert summary["trips_finished"] == 1
    assert summary["total_delay_vehicle_hours"] == pytest.approx(0, abs=1e-9)
    # Journey time counts whole seconds, so it can only be up to 1 s
    # longer than the free-flow time of the fastest route
    assert 0 <= summary["mean_increase_seconds"] < 1
    assert result["roads"] == []


def test_delay_on_roads_adds_up_to_delay_of_trips(world):
    """Every second of delay is counted on exactly one road."""

    from backend.scenarios.impact import ImpactTracker

    captured = {}
    original = ImpactTracker.finish

    def keep(self):
        original(self)
        captured["roads"] = self.total_road_delay()

    ImpactTracker.finish = keep
    try:
        result = run_scenario(world, spec(demand={"vehicles": 80}))
    finally:
        ImpactTracker.finish = original

    trip_delay = sum(d for d in result["trips"]["delay_seconds"] if d is not None)

    assert trip_delay > 0
    assert captured["roads"] == pytest.approx(trip_delay, rel=1e-9)
    assert result["summary"]["total_delay_vehicle_hours"] == pytest.approx(trip_delay / 3600)


def test_delay_is_journey_time_minus_driving_time(world):
    result = run_scenario(world, spec())
    trips = result["trips"]

    for journey, free_flow, delay, status in zip(
        trips["journey_seconds"], trips["free_flow_seconds"],
        trips["delay_seconds"], trips["status"],
    ):
        assert status == FINISHED
        assert 0 <= delay <= journey
        # A trip can never beat its free-flow fastest time
        assert journey >= free_flow - 1e-6


def test_the_time_series_accounts_for_every_vehicle_second(world):
    result = run_scenario(world, spec())
    series = result["timeseries"]

    assert [row["time"] for row in series][:-1] == list(
        range(60, 60 * len(series), 60)
    )[: len(series) - 1]
    assert sum(row["delay_vehicle_seconds"] for row in series) == pytest.approx(
        result["summary"]["total_delay_vehicle_hours"] * 3600
    )
    assert sum(row["vehicle_seconds"] for row in series) == result["execution"]["vehicle_seconds"]
    assert series[-1]["finished_vehicles"] == 60


def test_areas_group_road_delay_into_500_metre_cells(world):
    result = run_scenario(world, spec(demand={"vehicles": 80}))
    areas = result["areas"]
    total = result["summary"]["total_delay_vehicle_hours"] * 3600

    assert areas
    delays = [area["delay_vehicle_seconds"] for area in areas]
    assert delays == sorted(delays, reverse=True)
    assert sum(delays) <= total + 1e-6

    south, west, north, east = cell_bounds(area_cell(52.63, 1.29))
    assert south <= 52.63 < north and west <= 1.29 < east
    assert (north - south) * 111_320 == pytest.approx(500)


def test_percentile_uses_the_nearest_rank():
    assert percentile([], 0.95) is None
    assert percentile([5], 0.95) == 5
    assert percentile(list(range(1, 101)), 0.95) == 95
    assert percentile([3, 1, 2], 0.5) == 2


# ------------------------------------------------------------
# Disruptions
# ------------------------------------------------------------

def test_cutting_the_network_in_two_makes_journeys_longer(world):
    """Close every road between rows 3 and 4 of the grid except one, so
    trips across must all funnel through it. (Closing just the busiest
    roads can make journeys shorter: traffic spreads onto the parallel
    roads, a Braess-like effect, so that is not tested.)"""

    closures = [
        {"start": 3 * 8 + column, "destination": 4 * 8 + column}
        for column in range(1, 8)
    ]
    baseline = run_scenario(world, spec())
    closed = run_scenario(world, spec(closures=closures))

    before = baseline["trips"]["journey_seconds"]
    after = closed["trips"]["journey_seconds"]
    paired = [(x, y) for x, y in zip(before, after) if x is not None and y is not None]

    assert len(paired) == 60
    assert sum(y for _, y in paired) > sum(x for x, _ in paired)
    assert closed["summary"]["mean_increase_seconds"] > baseline["summary"]["mean_increase_seconds"]
    assert closed["timeseries"][0]["closed_roads"] == 7


def test_a_closure_can_reopen(world):
    baseline = run_scenario(world, spec())
    a, b = busiest_roads(baseline, 1)[0]
    result = run_scenario(world, spec(
        closures=[{"start": a, "destination": b, "start_at": 0, "end_at": 90}]
    ))

    closed = [row["closed_roads"] for row in result["timeseries"]]
    assert closed[0] == 1
    assert closed[-1] == 0


def test_an_accident_slows_its_road(world):
    """An accident after the trips have left, with rerouting off: every
    vehicle keeps its route, so the accident's own effect shows as more
    time lost on that road and on the network.

    (An accident at second 0 would be planned around by every departing
    trip, so nobody would use the road; and with rerouting on, vehicles
    already on their way avoid it. Delay can then fall while journeys get
    longer through detours, which is why delay and journey-time increase
    are measured separately.)"""

    baseline = run_scenario(world, spec(rerouting=False))
    road = baseline["roads"][0]
    a, b = road["start_node"], road["end_node"]

    crashed = run_scenario(world, spec(
        rerouting=False,
        accidents=[{"start": a, "destination": b, "severity": "severe", "start_at": 20}],
    ))

    def delay_on(result):
        for item in result["roads"]:
            if {item["start_node"], item["end_node"]} == {a, b}:
                return item["delay_vehicle_seconds"]
        return 0.0

    assert delay_on(crashed) > delay_on(baseline)
    assert crashed["summary"]["total_delay_vehicle_hours"] > baseline["summary"]["total_delay_vehicle_hours"]
    assert crashed["summary"]["total_travel_vehicle_hours"] > baseline["summary"]["total_travel_vehicle_hours"]
    assert crashed["timeseries"][0]["accidents"] == 1     # snapshot at 60 s


def test_trips_cut_off_by_closures_are_counted_not_lost(world):
    # Close every road out of junction 0 (a corner: roads to 1 and 8)
    closures = [{"start": 0, "destination": 1}, {"start": 0, "destination": 8}]
    result = run_scenario(world, spec(demand={"vehicles": 120}, closures=closures))

    statuses = result["trips"]["status"]
    unroutable = statuses.count(UNROUTABLE)

    assert result["summary"]["trips_unroutable"] == unroutable
    assert result["summary"]["trips_requested"] == 120
    assert len(statuses) == 120
    for status, journey in zip(statuses, result["trips"]["journey_seconds"]):
        assert (journey is None) == (status == UNROUTABLE)


def test_a_surge_adds_its_trips_after_the_demand(world):
    centre = world.network.position[27]
    result = run_scenario(world, spec(
        demand={"vehicles": 20},
        surges=[{
            "latitude": centre[0], "longitude": centre[1], "radius_metres": 500,
            "vehicles_per_minute": 60, "start_at": 30, "duration_seconds": 60,
        }],
    ))

    # 60 a minute for 60 s: 60 more trips, after the 20 of the demand
    assert result["summary"]["trips_requested"] == 80
    assert result["summary"]["trips_finished"] + result["summary"]["trips_unroutable"] == 80


def test_spreading_departures_lowers_the_peak(world):
    at_once = run_scenario(world, spec(demand={"vehicles": 80}))
    spread = run_scenario(world, spec(demand={"vehicles": 80, "spread_seconds": 300}))

    assert max(r["active_vehicles"] for r in spread["timeseries"]) < 80
    assert spread["summary"]["total_delay_vehicle_hours"] < at_once["summary"]["total_delay_vehicle_hours"]


def test_rerouting_can_be_switched_off(world):
    result = run_scenario(world, spec(rerouting=False))

    assert result["summary"]["reroutes"] == 0


def test_the_time_limit_stops_the_run_and_counts_unfinished_trips(world):
    result = run_scenario(world, spec(max_seconds=60))
    summary = result["summary"]

    assert summary["hit_time_limit"] is True
    assert summary["simulated_seconds"] == 60
    assert summary["trips_finished"] < 60
    assert result["trips"]["status"].count(UNFINISHED) == 60 - summary["trips_finished"]


# ------------------------------------------------------------
# Emergencies
# ------------------------------------------------------------

def test_emergencies_are_answered_by_the_nearest_station(world):
    station = world.network.position[27]
    result = run_scenario(world, spec(
        stations=[{"latitude": station[0], "longitude": station[1], "ambulances": 2}],
        emergencies={"count": 4, "first_at": 10, "interval_seconds": 30, "target_seconds": 600},
    ))
    emergencies = result["emergencies"]

    assert emergencies["requested"] == 4
    assert emergencies["dispatched"] + emergencies["refused"] == 4
    assert emergencies["arrived"] == emergencies["dispatched"] > 0
    assert 0 < emergencies["mean_response_seconds"] <= emergencies["worst_response_seconds"]
    assert 0 <= emergencies["within_target_percent"] <= 100


def test_more_stations_answer_faster_or_as_fast(world):
    corner = world.network.position[0]
    other = world.network.position[63]
    emergencies = {"count": 6, "first_at": 5, "interval_seconds": 40}

    one = run_scenario(world, spec(
        demand={"vehicles": 0},
        stations=[{"latitude": corner[0], "longitude": corner[1]}],
        emergencies=emergencies,
    ))
    two = run_scenario(world, spec(
        demand={"vehicles": 0},
        stations=[
            {"latitude": corner[0], "longitude": corner[1]},
            {"latitude": other[0], "longitude": other[1]},
        ],
        emergencies=emergencies,
    ))

    assert two["emergencies"]["mean_response_seconds"] <= one["emergencies"]["mean_response_seconds"]


# ------------------------------------------------------------
# The spec
# ------------------------------------------------------------

def test_specs_are_checked_against_the_map(world):
    with pytest.raises(ScenarioError, match="not on the map"):
        run_scenario(world, spec(closures=[{"start": 0, "destination": 999}]))

    with pytest.raises(ScenarioError, match="not directly connected"):
        run_scenario(world, spec(accidents=[{"start": 0, "destination": 63}]))


@pytest.mark.parametrize("bad", [
    {"closures": [{"start": 1, "destination": 2, "start_at": 50, "end_at": 10}]},
    {"emergencies": {"count": 2}},                          # no station
    {"demand": {"vehicles": 5000}},                         # over the limit
    {"accidents": [{"start": 1, "destination": 2, "severity": "apocalyptic"}]},
    {"surges": [{"latitude": 52.6, "longitude": 1.3, "radius_metres": 50}]},
    {"max_seconds": 10},
    {"unknown_setting": True},
])
def test_impossible_specs_are_refused(bad):
    with pytest.raises(ValueError):
        ScenarioSpec.model_validate(spec(**bad))


def test_the_fingerprint_ignores_the_name_but_not_the_settings():
    a = ScenarioSpec.model_validate(spec(name="A"))
    b = ScenarioSpec.model_validate(spec(name="B"))
    c = ScenarioSpec.model_validate(spec(rerouting=False))

    assert a.fingerprint() == b.fingerprint()
    assert a.fingerprint() != c.fingerprint()
    assert a.trip_signature() == c.trip_signature()
    assert len(a.fingerprint()) == 64


def test_results_are_plain_python_values(world):
    """Results go to other processes and to JSON, so no numpy numbers."""

    import json

    result = run_scenario(world, spec())

    json.dumps(result)

    def walk(value):
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        else:
            assert value is None or type(value) in (int, float, str, bool), type(value)

    walk(result)
    assert not math.isnan(result["summary"]["total_delay_vehicle_hours"])
