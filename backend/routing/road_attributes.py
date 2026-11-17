"""What is known about each road, from its OpenStreetMap tags.

The map keeps, for every directed road, its class (highway=residential,
primary, ...), speed limit, lanes in its own direction, whether it is
one-way, and its name. OpenStreetMap is incomplete, so every value says
whether it was tagged or filled in by a default (docs/road_network.md has
the counts for Norwich).

How tags become numbers:

* Speed limits: "20 mph" -> 8.94 m/s; a bare number is km/h (the
  OpenStreetMap convention); the UK national-limit codes are understood.
  A road that osmnx made by joining several OpenStreetMap ways can have
  several values; the lowest is used (a vehicle must obey the slowest
  stretch, and a lower speed can only make travel-time estimates more
  cautious). Missing or unusable values ("none" is not a valid UK limit,
  "signals", "variable") use the default for the road's class.
* Default limits: Norwich is a built-up area, where the national limit is
  30 mph on lit roads; living streets are given 20 mph and motorways 70.
* Lanes: the tag counts both directions on a two-way road, so each
  direction gets half (at least 1); a one-way road gets them all. With
  several values the lowest is used (the bottleneck). Missing lanes
  default to 1 per direction, or 2 on one-way trunk and primary roads
  (dual carriageways).
"""

import math
import re
from dataclasses import dataclass
from typing import Optional


MPH = 0.44704          # metres per second in one mile per hour
KMH = 1 / 3.6          # metres per second in one km/h

# The UK national speed-limit codes used in OpenStreetMap
NATIONAL_LIMITS_MPH = {
    "gb:nsl_single": 60,
    "gb:nsl_dual": 70,
    "gb:nsl_restricted": 30,
    "gb:motorway": 70,
    "uk:nsl_single": 60,
    "uk:nsl_dual": 70,
    "uk:motorway": 70,
}

# Speed limit used when a road has none tagged, by class (mph)
DEFAULT_LIMIT_MPH = {
    "motorway": 70,
    "motorway_link": 70,
    "living_street": 20,
}
BUILT_UP_LIMIT_MPH = 30

# Most important first: when osmnx joined ways of different classes, the
# road is treated as the most important one
ROAD_CLASSES = [
    "motorway", "trunk", "primary", "secondary", "tertiary",
    "motorway_link", "trunk_link", "primary_link", "secondary_link",
    "tertiary_link", "unclassified", "residential", "living_street",
    "service", "road",
]

# One-way roads of these classes are usually dual carriageways
TWO_LANE_ONE_WAY = {"motorway", "trunk", "primary"}

_NUMBER = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(mph|km/h|kmh|kph)?\s*$")


@dataclass(frozen=True)
class RoadInfo:
    """One directed road's attributes (see the module docstring)."""

    highway: str
    speed_limit: float          # metres per second
    speed_limit_tagged: bool
    lanes: int                  # in this road's own direction
    lanes_tagged: bool
    oneway: bool
    name: Optional[str] = None


def as_list(value):
    """OpenStreetMap values can be one value or (after joining ways) a
    list; always a list, without missing entries."""

    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [item for item in value if item is not None]
    return [value]


def parse_speed(value):
    """One maxspeed value in metres per second, or None if unusable."""

    if isinstance(value, (int, float)):
        return value * KMH if value > 0 and math.isfinite(value) else None

    if not isinstance(value, str):
        return None

    text = value.strip().lower()

    if text in NATIONAL_LIMITS_MPH:
        return NATIONAL_LIMITS_MPH[text] * MPH

    # Several limits in one value ("30 mph;20 mph"): the lowest
    if ";" in text:
        speeds = [parse_speed(part) for part in text.split(";")]
        speeds = [speed for speed in speeds if speed is not None]
        return min(speeds) if speeds else None

    match = _NUMBER.match(text)
    if match is None:
        return None

    number = float(match.group(1))
    if number <= 0:
        return None

    unit = match.group(2)
    return number * (MPH if unit == "mph" else KMH)


def parse_speed_limit(value):
    """The lowest usable limit among a road's maxspeed values (m/s), or
    None."""

    speeds = [parse_speed(item) for item in as_list(value)]
    speeds = [speed for speed in speeds if speed is not None]
    return min(speeds) if speeds else None


def parse_lanes(value):
    """The lowest usable lane count among a road's values, or None."""

    counts = []

    for item in as_list(value):
        try:
            count = int(float(str(item).split(";")[0]))
        except ValueError:
            continue
        if count > 0:
            counts.append(count)

    return min(counts) if counts else None


def road_class(value):
    """The road's class (the most important one if there are several)."""

    classes = [str(item) for item in as_list(value)]

    if not classes:
        return "road"

    known = [item for item in classes if item in ROAD_CLASSES]
    if known:
        return min(known, key=ROAD_CLASSES.index)

    return classes[0]


def default_speed_limit(highway):
    return DEFAULT_LIMIT_MPH.get(highway, BUILT_UP_LIMIT_MPH) * MPH


def lanes_per_direction(total, oneway, highway):
    """Lanes in one direction from the tagged total (None if untagged)."""

    if total is None:
        return 2 if oneway and highway in TWO_LANE_ONE_WAY else 1

    return total if oneway else max(1, total // 2)


def road_name(data):
    names = []

    for item in as_list(data.get("name")) or as_list(data.get("ref")):
        if str(item) not in names:
            names.append(str(item))

    return " / ".join(names) if names else None


def road_info_from_osm(data):
    """RoadInfo for one osmnx edge's attribute dict."""

    highway = road_class(data.get("highway"))
    oneway = bool(data.get("oneway", False))

    limit = parse_speed_limit(data.get("maxspeed"))
    total_lanes = parse_lanes(data.get("lanes"))

    return RoadInfo(
        highway=highway,
        speed_limit=limit if limit is not None else default_speed_limit(highway),
        speed_limit_tagged=limit is not None,
        lanes=lanes_per_direction(total_lanes, oneway, highway),
        lanes_tagged=total_lanes is not None,
        oneway=oneway,
        name=road_name(data),
    )
