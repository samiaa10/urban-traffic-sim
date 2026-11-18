"""Floods and other disruptions that change roads for a while.

These sit next to the existing incidents and reuse their effects:

  * accidents.py: a road stays open but is slower (speed_factor) and fills
    up sooner (capacity_factor);
  * closures (Simulation.block_segment): a road cannot be used.

A FLOOD affects a set of roads (chosen by the user, or every road inside an
area). While it lasts, every one of its roads is either closed (severity
"closed") or slowed like an accident; afterwards the water goes down over
`recovery_seconds` and the roads recover gradually: speed and capacity rise
in a straight line back to normal (a closed road reopens at the start of
recovery, still badly slowed, and then improves too).

This is a transport disruption, NOT a hydrological flood model: there is no
elevation, rainfall or river data in the project's map (the OpenStreetMap
download has roads only: no waterways, no heights, no flood_prone tags), so
which roads flood, how badly and for how long are inputs, not predictions.

Overlapping disruptions combine rather than overwrite (Simulation):

  * closures remember who closed a road ("manual", "flood:3", "failure:1",
    ...) and the road only reopens when nobody still has it closed;
  * an accident and floods on the same road multiply: speed factors and
    capacity factors each multiply, as two independent obstructions would.

Severity figures are modelling choices, like the accidents' ones: "minor" is
standing water drivers slow down for, "moderate" leaves one lane usable,
"severe" is a crawl through deep water, "closed" is impassable.
"""

from collections import namedtuple


FLOOD_SEVERITIES = {
    "minor": {"speed_factor": 0.6, "capacity_factor": 0.7},
    "moderate": {"speed_factor": 0.35, "capacity_factor": 0.5},
    "severe": {"speed_factor": 0.15, "capacity_factor": 0.25},
    "closed": None,
}

# Where a closed road starts recovering from when it reopens
REOPENED = {"speed_factor": 0.3, "capacity_factor": 0.4}

# What a road's combined incidents do to it (the same two numbers an
# Accident has, so the traffic models need no change)
RoadEffect = namedtuple("RoadEffect", ["speed_factor", "capacity_factor"])


class Flood:
    """Roads under water from started_at for duration seconds, then
    recovering for recovery_seconds."""

    def __init__(self, ident, segments, severity, started_at, duration, recovery_seconds):
        if severity not in FLOOD_SEVERITIES:
            raise ValueError("severity must be one of " + ", ".join(FLOOD_SEVERITIES))
        if not duration > 0 or recovery_seconds < 0:
            raise ValueError("duration must be positive and recovery_seconds not negative")
        if not segments:
            raise ValueError("a flood needs at least one road")

        self.ident = ident
        self.segments = tuple(sorted({tuple(sorted(s)) for s in segments}))
        self.severity = severity
        self.started_at = started_at
        self.ends_at = started_at + duration
        self.recovered_at = self.ends_at + recovery_seconds
        self.recovery_seconds = recovery_seconds
        self.reopened = False   # a closed flood's roads have reopened

    @property
    def source(self):
        """Its name as a closer of roads (see Simulation.block_segment)."""
        return f"flood:{self.ident}"

    @property
    def closes_roads(self):
        return FLOOD_SEVERITIES[self.severity] is None

    def stage(self, now):
        if now < self.ends_at:
            return "flooded"
        if now < self.recovered_at:
            return "recovering"
        return "over"

    def recovered_fraction(self, now):
        """0 while flooded, rising to 1 when fully recovered."""
        if now < self.ends_at:
            return 0.0
        if self.recovery_seconds <= 0 or now >= self.recovered_at:
            return 1.0
        return (now - self.ends_at) / self.recovery_seconds

    def effect(self, now):
        """Its RoadEffect now, or None when it has no effect (over, or the
        road is closed, which the closure handles)."""
        stage = self.stage(now)
        if stage == "over" or (stage == "flooded" and self.closes_roads):
            return None
        base = REOPENED if self.closes_roads else FLOOD_SEVERITIES[self.severity]
        recovered = self.recovered_fraction(now)
        return RoadEffect(
            base["speed_factor"] + (1 - base["speed_factor"]) * recovered,
            base["capacity_factor"] + (1 - base["capacity_factor"]) * recovered,
        )

    def describe(self, now):
        effect = self.effect(now)
        stage = self.stage(now)
        return {
            "id": self.ident,
            "severity": self.severity,
            "stage": stage,
            "closed": self.closes_roads and stage == "flooded",
            "started_at": self.started_at,
            "ends_at": self.ends_at,
            "recovered_at": self.recovered_at,
            "recovered_percent": round(100 * self.recovered_fraction(now)),
            "speed_factor": None if effect is None else round(effect.speed_factor, 3),
            "roads": [
                {"start": {"latitude": a[0], "longitude": a[1]},
                 "end": {"latitude": b[0], "longitude": b[1]}}
                for a, b in self.segments
            ],
        }


def combine(*effects):
    """One RoadEffect from several (an accident and floods), or None."""
    effects = [e for e in effects if e is not None]
    if not effects:
        return None
    if len(effects) == 1:
        return effects[0]
    speed = capacity = 1.0
    for effect in effects:
        speed *= effect.speed_factor
        capacity *= effect.capacity_factor
    return RoadEffect(speed, capacity)


def segments_in_area(network, latitude, longitude, radius_metres):
    """Every road (segment key) with both ends within radius_metres of a
    point."""
    from backend.simulation.vehicle import straight_line_metres

    centre = (latitude, longitude)
    inside = {
        node for node, position in network.position.items()
        if straight_line_metres(centre, position) <= radius_metres
    }
    return sorted({
        network.segment[(a, b)] for (a, b) in network.length if a in inside and b in inside
    })
