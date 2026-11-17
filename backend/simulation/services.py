"""The emergency services and how emergencies are handled.

Three services, each with its own vehicles, stations and response target.
A station belongs to one service and holds only that service's vehicles;
an emergency names the service it needs, and only that service is sent.

| Service   | Default speed        | Default target |
|-----------|----------------------|----------------|
| ambulance | 20 m/s (72 km/h)     | 7 min          |
| fire      | 17 m/s (61 km/h)     | 8 min          |
| police    | 22 m/s (79 km/h)     | 15 min         |

The ambulance target is the mean response time NHS England sets for its
most urgent calls (Category 1, 7 minutes). There is no single national
target for fire or police in England, so those two are illustrative
defaults; everything here can be set per station, vehicle and study.
Fire engines are heavy, hence slower; speeds are top speeds on a call,
before traffic.

How emergencies are handled depends on the run's traffic model
(simulation/road_model.py):

simple   the original rules: the nearest station in a straight line with a
         vehicle free sends it along the shortest route; if none is free
         the emergency is refused; a vehicle is back at its station the
         moment it arrives; traffic slows emergency vehicles like any car.
osm      the realistic rules (EmergencyRules.realistic):
         * dispatch by drive time: every free vehicle of the service is
           considered and the one that would arrive first (fastest route
           under the current traffic and closures) is sent;
         * a queue: an emergency with nothing free waits, oldest first,
           and is answered when a vehicle of its service is free again
           (its response time includes the wait);
         * vehicles drive back to their station after a call and are only
           free again when they get there (or are sent from on the way);
         * right of way: traffic gives way, so an emergency vehicle on a
           call loses only half the time other traffic would
           (RIGHT_OF_WAY), and it is exempt from speed limits.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Service:
    name: str
    label: str
    speed: float            # metres per second
    target_seconds: float


SERVICES = {
    "ambulance": Service("ambulance", "Ambulance", 20.0, 420.0),
    "fire": Service("fire", "Fire engine", 17.0, 480.0),
    "police": Service("police", "Police car", 22.0, 900.0),
}

SERVICE_NAMES = tuple(SERVICES)

# Share of the congestion slowdown an emergency vehicle on a call avoids
# in the realistic rules (traffic pulls over)
RIGHT_OF_WAY = 0.5


def service(name):
    """The Service called `name`; ValueError for anything else."""

    try:
        return SERVICES[name]
    except (KeyError, TypeError):
        raise ValueError(
            f"Unknown emergency service {name!r}: use one of {', '.join(SERVICE_NAMES)}"
        ) from None


@dataclass(frozen=True)
class EmergencyRules:
    dispatch_by_drive_time: bool
    queue: bool
    drive_back: bool
    right_of_way: float      # 0 = none, RIGHT_OF_WAY = halve the slowdown

    def emergency_factor(self, factor):
        """Speed factor for an emergency vehicle on a call, given the
        factor other traffic gets on that road."""

        if not self.right_of_way:
            return factor
        return 1.0 - (1.0 - factor) * (1.0 - self.right_of_way)


ORIGINAL_RULES = EmergencyRules(
    dispatch_by_drive_time=False, queue=False, drive_back=False, right_of_way=0.0
)
REALISTIC_RULES = EmergencyRules(
    dispatch_by_drive_time=True, queue=True, drive_back=True, right_of_way=RIGHT_OF_WAY
)


def rules_for(road_model):
    """The emergency rules that go with a traffic model."""

    return REALISTIC_RULES if road_model.name == "osm" else ORIGINAL_RULES
