"""
Accidents
---------

An accident is not a road closure. The road stays open, but for a while:

  * everyone on it drives slower (speed_factor), and
  * it holds fewer vehicles before it jams (capacity_factor): with half
    the capacity, 2 vehicles congest the road as much as 4 normally would.

Each accident clears by itself after its duration (in simulated seconds),
or earlier if it is cleared by hand. Severity sets all three numbers.

The figures are modelling choices, not measured data: a minor accident
behaves like losing part of a lane, a severe one like traffic squeezing
past a wreck in single file.
"""

SEVERITIES = {
    "minor": {
        "speed_factor": 0.7,
        "capacity_factor": 0.75,
        "default_duration": 600,     # 10 minutes
    },
    "moderate": {
        "speed_factor": 0.45,
        "capacity_factor": 0.5,
        "default_duration": 1200,    # 20 minutes
    },
    "severe": {
        "speed_factor": 0.2,
        "capacity_factor": 0.34,
        "default_duration": 1800,    # 30 minutes
    },
}


class Accident:

    def __init__(self, segment, severity, started_at, duration=None):
        if severity not in SEVERITIES:
            raise ValueError(
                "severity must be one of " + ", ".join(SEVERITIES)
            )

        settings = SEVERITIES[severity]

        if duration is None:
            duration = settings["default_duration"]

        if not duration > 0 or duration == float("inf"):
            raise ValueError("duration must be a positive number of seconds")

        self.segment = segment
        self.severity = severity
        self.speed_factor = settings["speed_factor"]
        self.capacity_factor = settings["capacity_factor"]
        self.started_at = started_at
        self.duration = duration
        self.clears_at = started_at + duration

    def remaining_seconds(self, now):
        return max(0, self.clears_at - now)

    def describe(self, now):
        (start, end) = self.segment

        return {
            "start": {"latitude": start[0], "longitude": start[1]},
            "end": {"latitude": end[0], "longitude": end[1]},
            "severity": self.severity,
            "speed_factor": self.speed_factor,
            "capacity_factor": self.capacity_factor,
            "started_at": self.started_at,
            "clears_at": self.clears_at,
            "remaining_seconds": self.remaining_seconds(now),
        }
