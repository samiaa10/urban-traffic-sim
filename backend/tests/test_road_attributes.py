"""Road attributes from OpenStreetMap tags (road_attributes.py), the loader
that keeps them, and the committed Norwich map they are saved in."""

import pickle
from pathlib import Path

import pytest

from backend.routing.graph import Graph
from backend.routing.road_attributes import (
    KMH,
    MPH,
    RoadInfo,
    default_speed_limit,
    lanes_per_direction,
    parse_lanes,
    parse_speed,
    parse_speed_limit,
    road_class,
    road_info_from_osm,
    road_name,
)


MAP_FILE = Path(__file__).resolve().parents[2] / "data" / "osm" / "norwich_graph.pkl"


# ------------------------------------------------------------
# Speed limits
# ------------------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    ("20 mph", 20 * MPH),
    ("30 mph", 30 * MPH),
    ("30mph", 30 * MPH),
    (" 40 MPH ", 40 * MPH),
    ("50", 50 * KMH),           # a bare number is km/h in OpenStreetMap
    ("50 km/h", 50 * KMH),
    ("2.5 mph", 2.5 * MPH),
    ("GB:nsl_single", 60 * MPH),
    ("GB:nsl_dual", 70 * MPH),
    ("30 mph;20 mph", 20 * MPH),
    (48, 48 * KMH),
])
def test_speed_values_are_read_in_metres_per_second(value, expected):
    assert parse_speed(value) == pytest.approx(expected)


@pytest.mark.parametrize("value", [
    "none", "signals", "variable", "national", "", "0", "-5", "mph", None, float("nan"),
])
def test_unusable_speed_values_are_rejected(value):
    assert parse_speed(value) is None


def test_the_lowest_of_several_limits_is_used():
    assert parse_speed_limit(["30 mph", "20 mph"]) == pytest.approx(20 * MPH)
    assert parse_speed_limit(["none", "30 mph"]) == pytest.approx(30 * MPH)
    assert parse_speed_limit(["none", "signals"]) is None
    assert parse_speed_limit(None) is None


def test_default_limits_follow_uk_built_up_rules():
    assert default_speed_limit("residential") == pytest.approx(30 * MPH)
    assert default_speed_limit("primary") == pytest.approx(30 * MPH)
    assert default_speed_limit("living_street") == pytest.approx(20 * MPH)
    assert default_speed_limit("motorway") == pytest.approx(70 * MPH)
    assert default_speed_limit("something_new") == pytest.approx(30 * MPH)


# ------------------------------------------------------------
# Lanes, classes, names
# ------------------------------------------------------------

def test_lane_counts_are_read_and_the_lowest_kept():
    assert parse_lanes("2") == 2
    assert parse_lanes(["3", "2"]) == 2
    assert parse_lanes("2;3") == 2
    assert parse_lanes(["x", "4"]) == 4
    assert parse_lanes("0") is None
    assert parse_lanes(None) is None


def test_lanes_are_split_between_directions_on_two_way_roads():
    assert lanes_per_direction(2, oneway=False, highway="primary") == 1
    assert lanes_per_direction(4, oneway=False, highway="primary") == 2
    assert lanes_per_direction(3, oneway=False, highway="primary") == 1
    assert lanes_per_direction(1, oneway=False, highway="residential") == 1
    assert lanes_per_direction(2, oneway=True, highway="primary") == 2


def test_missing_lanes_default_to_one_per_direction_or_two_on_dual_carriageways():
    assert lanes_per_direction(None, oneway=False, highway="trunk") == 1
    assert lanes_per_direction(None, oneway=True, highway="residential") == 1
    assert lanes_per_direction(None, oneway=True, highway="trunk") == 2
    assert lanes_per_direction(None, oneway=True, highway="primary") == 2


def test_a_road_of_several_classes_is_treated_as_the_most_important():
    assert road_class("residential") == "residential"
    assert road_class(["residential", "primary"]) == "primary"
    assert road_class(["unclassified", "tertiary_link"]) == "tertiary_link"
    assert road_class(None) == "road"


def test_names_join_several_values_and_fall_back_to_the_road_number():
    assert road_name({"name": "Prince of Wales Road"}) == "Prince of Wales Road"
    assert road_name({"name": ["A", "B", "A"]}) == "A / B"
    assert road_name({"ref": "A147"}) == "A147"
    assert road_name({}) is None


def test_road_info_marks_what_was_tagged_and_what_was_defaulted():
    tagged = road_info_from_osm({
        "highway": "primary", "maxspeed": "20 mph", "lanes": "4",
        "oneway": False, "name": "Riverside Road",
    })
    assert tagged == RoadInfo(
        highway="primary", speed_limit=pytest.approx(20 * MPH),
        speed_limit_tagged=True, lanes=2, lanes_tagged=True,
        oneway=False, name="Riverside Road",
    )

    bare = road_info_from_osm({"highway": "living_street", "oneway": True})
    assert bare.speed_limit == pytest.approx(20 * MPH)
    assert not bare.speed_limit_tagged
    assert bare.lanes == 1 and not bare.lanes_tagged
    assert bare.oneway and bare.name is None


# ------------------------------------------------------------
# The loader
# ------------------------------------------------------------

def test_parallel_roads_keep_the_attributes_of_the_shorter_one():
    networkx = pytest.importorskip("networkx")
    from backend.routing.osm_loader import convert_to_custom_graph

    osm = networkx.MultiDiGraph()
    osm.add_node(1, y=52.0, x=1.0)
    osm.add_node(2, y=52.001, x=1.0)
    osm.add_edge(1, 2, length=150.0, highway="primary", maxspeed="40 mph", oneway=True)
    osm.add_edge(1, 2, length=110.0, highway="residential", maxspeed="20 mph", oneway=True)
    osm.add_edge(2, 1, length=111.0, highway="residential", oneway=True)

    graph = convert_to_custom_graph(osm)

    # Both parallel roads stay in the graph, as before
    assert sorted(w for _, w in graph.get_neighbours(1)) == [110.0, 150.0]
    # The attributes are the shorter road's (the one routing uses)
    assert graph.road(1, 2).highway == "residential"
    assert graph.road(1, 2).speed_limit == pytest.approx(20 * MPH)
    assert graph.road(2, 1).speed_limit == pytest.approx(30 * MPH)
    assert graph.road(2, 1).speed_limit_tagged is False


def test_maps_without_attributes_still_work():
    graph = Graph()
    graph.add_node(1, 52.0, 1.0)
    graph.add_node(2, 52.001, 1.0)
    graph.add_edge(1, 2, 100)

    assert graph.road(1, 2) is None
    assert graph.has_road_info is False

    # A map file pickled before road_info existed has no attribute at all
    del graph.road_info
    assert graph.road(1, 2) is None
    assert graph.has_road_info is False


# ------------------------------------------------------------
# The committed Norwich map
# ------------------------------------------------------------

@pytest.fixture(scope="module")
def norwich():
    with open(MAP_FILE, "rb") as file:
        return pickle.load(file)


def all_roads(graph):
    return [(a, b, length) for a, roads in graph.edges.items() for b, length in roads]


def test_norwich_keeps_its_network(norwich):
    # The same network as before attributes were kept (rebuilt from the
    # same OpenStreetMap response, see osm_loader.py)
    assert len(norwich.nodes) == 5058
    assert len(all_roads(norwich)) == 10980


def test_every_norwich_road_has_attributes(norwich):
    for a, b, _ in all_roads(norwich):
        info = norwich.road(a, b)
        assert info is not None, (a, b)
        assert 1 * MPH <= info.speed_limit <= 70 * MPH
        assert info.lanes >= 1


def test_most_norwich_speed_limits_are_tagged_not_defaulted(norwich):
    infos = list(norwich.road_info.values())
    tagged = sum(info.speed_limit_tagged for info in infos) / len(infos)

    assert tagged >= 0.98


def test_norwich_one_way_roads_are_represented_correctly(norwich):
    roads = {(a, b) for a, b, _ in all_roads(norwich)}
    one_way = [(a, b) for (a, b), info in norwich.road_info.items() if info.oneway]

    for (a, b), info in norwich.road_info.items():
        if not info.oneway:
            # A two-way road can be driven both ways
            assert (b, a) in roads, (a, b)

    # One-way roads are stored in their own direction only. The few with a
    # road back between the same two junctions are separate roads in
    # OpenStreetMap. Counted from the 8 September 2026 data: 844 one-way
    # roads in osmnx's graph, 843 junction pairs once a parallel pair is
    # merged, 49 of them with a separate road back.
    assert len(one_way) == 843
    assert sum((b, a) in roads for a, b in one_way) == 49
