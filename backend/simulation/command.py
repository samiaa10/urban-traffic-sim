"""
Emergency response command centre
---------------------------------

Incidents an operator logs, the units sent to them, and how the response
went, built from the pieces the simulation already has:

    a unit sent              one Emergency per unit (dispatch.py: the free
                             vehicle of the service that would arrive first
                             by road, under current traffic and closures;
                             queued when none is free)
    getting there            the vehicle drives its route in the simulation,
                             and re-plans like every vehicle (closures and
                             accidents at once, congestion when a meaningfully
                             faster route appears: traffic_manager.py)
    the road itself          an accident, a flood or a closure, exactly as the
                             incident endpoints make them (simulation.py)

What this module adds:

  * INCIDENTS: a kind, a place, and a RESPONSE PLAN (which services go);
    road accidents and floods also put an accident or a flood on the
    roads, and a road closure closes the nearest road.
  * TIME AT THE SCENE: a unit that arrives stays there, busy, for the kind's
    on-scene time (Emergency.on_scene_seconds), then leaves and drives back
    to its station. An operator can stand units down sooner (resolve).
  * THE TIMELINE of each incident: reported, each unit dispatched (and how
    long it waited if none was free), arrived, cleared; the road reopened or
    the accident or flood over; resolved. Every time is the simulation's.
  * METRICS over the units that have arrived: mean and worst response,
    and the share within each service's target (services.py); and how many
    units are free now.
  * TRACKS for replay: every unit's position each simulated second while it
    is out (on a call, at the scene or driving back), recorded from the
    simulation as it runs.

The response plans and on-scene times are model settings, written down
here, not data about any real service (docs/COMMAND_CENTRE.md).

Nothing here talks to FastAPI, so all of it can be tested on a tiny map.
"""

import math

from backend.simulation import dispatch
from backend.simulation.services import SERVICES


# Which services each kind of incident gets, one unit each, in this order
RESPONSE_PLANS = {
    "medical": ("ambulance",),
    "fire": ("fire", "ambulance"),
    "crime": ("police",),
    "road_accident": ("ambulance", "police"),
    "flood": ("fire",),
    "road_closure": (),
}

LABELS = {
    "medical": "Medical emergency",
    "fire": "Fire",
    "crime": "Crime in progress",
    "road_accident": "Road accident",
    "flood": "Flood",
    "road_closure": "Road closure",
}

# Simulated seconds a unit works at the scene after arriving
ON_SCENE_SECONDS = {
    "medical": 900,
    "fire": 1200,
    "crime": 600,
    "road_accident": 900,
    "flood": 1200,
}

# The road effects: a road accident is a severe accident on the nearest
# road for its usual time (accidents.py); a flood closes every road within
# FLOOD_RADIUS metres for FLOOD_SECONDS, then recovers over FLOOD_RECOVERY
ACCIDENT_SEVERITY = "severe"
FLOOD_RADIUS = 200
FLOOD_SECONDS = 1200
FLOOD_RECOVERY = 600

KINDS = tuple(RESPONSE_PLANS)

# Tracks: at most this many samples per unit (a sample per simulated
# second while out: about 3 hours)
MAX_TRACK = 10_800


class Incident:

    def __init__(self, ident, kind, latitude, longitude, created_at):
        self.ident = ident
        self.kind = kind
        self.latitude = latitude
        self.longitude = longitude
        self.created_at = created_at

        # One Emergency per unit asked for (queued ones have no vehicle yet)
        self.units = []
        # Services nothing could be sent for: (service, reason)
        self.refused = []

        # The road effect: a sorted segment (accident or closure), a flood
        self.segment = None
        self.flood = None
        self.road_cleared_at = None

        self.resolved_at = None
        # "operator" if stood down from the command centre
        self.resolved_by = None

        # Where units are sent: the incident itself, or for a flood the
        # nearest junction clear of the water (its middle may be cut off)
        self.target = (latitude, longitude)


def nearest_road(graph, latitude, longitude):
    """The road segment nearest a point: from the nearest junction, the
    road to whichever neighbour lies most nearly towards the point. Returns
    (node_a, node_b) or None if the junction has no roads."""

    node = dispatch.nearest_junction(graph, latitude, longitude)
    if node is None:
        return None
    here = graph.nodes[node]
    # Roads leaving the junction, and (for one-way dead ends) roads into it
    neighbours = {other for other, _ in graph.get_neighbours(node) if other != node}
    neighbours |= {
        source for source, roads in graph.edges.items()
        if source != node and any(target == node for target, _ in roads)
    }
    if not neighbours:
        return None

    def distance(other):
        data = graph.nodes[other]
        middle = ((here["latitude"] + data["latitude"]) / 2, (here["longitude"] + data["longitude"]) / 2)
        return math.hypot(middle[0] - latitude, (middle[1] - longitude) * math.cos(math.radians(latitude)))

    other = min(sorted(neighbours, key=str), key=distance)
    return node, other


def staging_point(simulation, network, latitude, longitude, segments, service, tries=60):
    """Where units go for a flood: the junction nearest its middle that no
    flooded road touches and that a station of `service` can still reach by
    road (the water can cut the nearest dry junctions off too). Returns
    (latitude, longitude), or the middle itself if none can be reached."""

    graph = network.graph
    wet = {point for segment in segments for point in segment}
    scale = math.cos(math.radians(latitude))
    dry = sorted(
        (
            (math.hypot(data["latitude"] - latitude, (data["longitude"] - longitude) * scale), str(node), node)
            for node, data in graph.nodes.items()
            if (data["latitude"], data["longitude"]) not in wet
        ),
    )
    blocked = simulation.get_blocked_segments()
    starts = {
        dispatch.nearest_junction(graph, station.latitude, station.longitude)
        for station in simulation.stations if station.station_type == service
    }
    for _, _, node in dry[:tries]:
        if any(network.fastest_path(start, node, 1.0, blocked_segments=blocked)[0] for start in starts):
            return graph.nodes[node]["latitude"], graph.nodes[node]["longitude"]
    return latitude, longitude


def segment_of(graph, a, b):
    """The simulation's key for the road between two junctions."""
    pa = (graph.nodes[a]["latitude"], graph.nodes[a]["longitude"])
    pb = (graph.nodes[b]["latitude"], graph.nodes[b]["longitude"])
    return tuple(sorted((pa, pb)))


class CommandCentre:
    """The command centre of one live run."""

    def __init__(self, simulation, network):
        self.simulation = simulation
        self.network = network
        self.incidents = []
        self._next = 1

        # Stations the command centre mobilised: index -> name and source
        self.stations = {}

        # vehicle index -> [[time, latitude, longitude], ...]
        self.tracks = {}

    # ------------------------------------------------------------
    # The fleet
    # ------------------------------------------------------------

    def add_station(self, latitude, longitude, service, name, source, vehicles):
        """A station with `vehicles` units of its service. Returns
        (station index, [vehicle indices])."""

        simulation = self.simulation
        station = simulation.add_station(latitude, longitude, service)
        index = len(simulation.stations) - 1
        self.stations[index] = {"name": name, "source": source}
        where = station.get_location()
        added = []
        for _ in range(vehicles):
            vehicle = simulation.add_emergency_vehicle(
                [where, where], station, speed=SERVICES[service].speed, vehicle_type=service,
            )
            added.append(simulation.vehicles.index(vehicle))
        return index, added

    def units(self):
        """(vehicle index, vehicle) for every vehicle at a station."""
        return [
            (index, vehicle) for index, vehicle in enumerate(self.simulation.vehicles)
            if vehicle.home_station is not None and vehicle.vehicle_type in SERVICES
        ]

    # ------------------------------------------------------------
    # Incidents
    # ------------------------------------------------------------

    def report(self, kind, latitude, longitude, graph, flood_radius=FLOOD_RADIUS):
        """Log an incident, put its effect on the roads and send its units.

        Returns (incident, outcomes): outcomes are dispatch_emergency's
        answers, one per unit asked for, so the caller can record them.
        Raises ValueError for an unknown kind or a place with no road."""

        if kind not in RESPONSE_PLANS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}")
        if not (math.isfinite(latitude) and math.isfinite(longitude)):
            raise ValueError("latitude and longitude must be numbers")

        simulation = self.simulation
        incident = Incident(self._next, kind, latitude, longitude, simulation.time)

        if kind in ("road_accident", "road_closure"):
            road = nearest_road(graph, latitude, longitude)
            if road is None:
                raise ValueError("There is no road near that point")
            incident.segment = segment_of(graph, *road)
        elif kind == "flood":
            from backend.simulation.disruptions import segments_in_area

            roads = segments_in_area(self.network, latitude, longitude, flood_radius)
            if not roads:
                raise ValueError("No road lies within the flood's area there")
            incident.flood = simulation.add_flood(roads, "closed", FLOOD_SECONDS, FLOOD_RECOVERY)
            incident.target = staging_point(
                simulation, self.network, latitude, longitude, incident.flood.segments, "fire",
            )

        if incident.segment is not None:
            if kind == "road_accident":
                simulation.add_accident(incident.segment, ACCIDENT_SEVERITY)
            else:
                simulation.block_segment(incident.segment, source=f"incident-{incident.ident}")

        self._next += 1
        self.incidents.append(incident)

        outcomes = []
        for service in RESPONSE_PLANS[kind]:
            outcome = dispatch.dispatch_emergency(graph, simulation, *incident.target, service)
            emergency = outcome["emergency"]
            if outcome["status"] in ("dispatched", "queued"):
                emergency.on_scene_seconds = ON_SCENE_SECONDS[kind]
                incident.units.append(emergency)
            else:
                incident.refused.append((service, outcome["status"]))
            outcomes.append(outcome)

        return incident, outcomes

    def find(self, ident):
        return next((i for i in self.incidents if i.ident == ident), None)

    def resolve(self, incident):
        """The operator closes an incident: units at the scene leave, the
        road reopens and the accident or flood ends. Refused (ValueError)
        while a unit is still on its way or waiting: it would be stranded
        with nowhere to go."""

        if incident.resolved_at is not None:
            raise ValueError("The incident is already resolved")
        if any(not unit.resolved for unit in incident.units):
            raise ValueError("A unit is still on its way: resolve once every unit is at the scene")

        simulation = self.simulation
        for unit in incident.units:
            simulation.clear_from_scene(unit)
        if incident.segment is not None:
            if incident.kind == "road_accident":
                simulation.clear_accident(incident.segment)
            else:
                simulation.unblock_segment(incident.segment, source=f"incident-{incident.ident}")
        if incident.flood is not None and incident.flood in simulation.floods:
            simulation.clear_flood(incident.flood.ident)
        incident.resolved_by = "operator"
        self.update()

    def _road_clear(self, incident):
        """Whether the incident's road effect is over (True if it has none)."""

        simulation = self.simulation
        if incident.kind == "road_accident":
            return incident.segment not in simulation.accidents
        if incident.kind == "road_closure":
            return incident.segment not in simulation.get_blocked_segments() or \
                f"incident-{incident.ident}" not in simulation.closed_by(incident.segment)
        if incident.flood is not None:
            return incident.flood not in simulation.floods
        return True

    def update(self):
        """Note road effects that ended and incidents now resolved (after
        every step and every change)."""

        now = self.simulation.time
        for incident in self.incidents:
            if incident.resolved_at is not None:
                continue
            has_road = incident.segment is not None or incident.flood is not None
            if has_road and incident.road_cleared_at is None and self._road_clear(incident):
                incident.road_cleared_at = now
            units_done = all(unit.cleared_at is not None for unit in incident.units)
            road_done = not has_road or incident.road_cleared_at is not None
            answered = incident.units or has_road
            # A road closure stays until an operator reopens it
            if incident.kind == "road_closure" and incident.resolved_by is None:
                continue
            if answered and units_done and road_done:
                incident.resolved_at = now

    def record(self):
        """One sample of every unit that is out (call after each step)."""

        now = self.simulation.time
        for index, vehicle in self.units():
            out = not vehicle.available or vehicle.going_home or vehicle.on_emergency
            track = self.tracks.get(index)
            if not out and not track:
                continue
            if track is None:
                track = self.tracks[index] = []
            here = vehicle.get_current_location()
            sample = [now, round(here["latitude"], 6), round(here["longitude"], 6)]
            # One sample per second, and only while out (plus the moment it
            # gets back, which ends the trip on the map)
            if track and track[-1][0] == now:
                continue
            if not out and track and track[-1][1:] == sample[1:]:
                continue
            # Standing still (at the scene): keep the first and the latest
            # sample of the stop only, so replay still knows when it moved
            if len(track) >= 2 and track[-1][1:] == sample[1:] == track[-2][1:]:
                track[-1][0] = now
                continue
            if len(track) < MAX_TRACK:
                track.append(sample)

    # ------------------------------------------------------------
    # Describing it
    # ------------------------------------------------------------

    def unit_status(self, index, vehicle):
        """What a unit is doing: available, en_route, blocked, on_scene or
        returning, and the incident it belongs to."""

        incident = None
        state = "available"
        for candidate in self.incidents:
            for unit in candidate.units:
                if unit.assigned_vehicle is vehicle and unit.cleared_at is None:
                    incident = candidate
                    if unit.on_scene:
                        state = "on_scene"
                    elif dispatch.vehicle_is_stuck(self.simulation, vehicle):
                        state = "blocked"
                    else:
                        state = "en_route"
        if incident is None and vehicle.going_home:
            state = "returning"
        if incident is None and not vehicle.available and not vehicle.going_home and vehicle.on_emergency:
            # Sent to an emergency raised outside the command centre
            state = "en_route"
        return state, incident

    def describe_unit(self, index, vehicle):
        state, incident = self.unit_status(index, vehicle)
        where = vehicle.get_current_location()
        station = vehicle.home_station
        station_index = self.simulation.stations.index(station)
        info = self.stations.get(station_index, {"name": f"Station {station_index}", "source": "added"})
        eta = None
        if state in ("en_route", "blocked"):
            eta = dispatch.remaining_seconds(self.simulation, vehicle)
        return {
            "vehicle_id": index,
            "service": vehicle.vehicle_type,
            "station": info["name"],
            "station_id": station_index,
            "state": state,
            "incident_id": incident.ident if incident else None,
            "latitude": where["latitude"],
            "longitude": where["longitude"],
            "eta_seconds": eta,
            "reroutes": vehicle.reroutes,
            "route": vehicle.route[vehicle.position:] if state in ("en_route", "blocked") else [],
        }

    def incident_status(self, incident):
        if incident.resolved_at is not None:
            return "resolved"
        if incident.units and any(u.assigned_vehicle is None for u in incident.units):
            return "queued"
        if any(not u.resolved for u in incident.units):
            return "en_route"
        if any(u.on_scene for u in incident.units):
            return "on_scene"
        if incident.refused and not incident.units and incident.segment is None and incident.flood is None:
            return "unanswered"
        return "active"

    def timeline(self, incident):
        """Everything that happened to an incident, in time order."""

        simulation = self.simulation
        events = [{"time": incident.created_at, "event": "reported", "label": f"{LABELS[incident.kind]} reported"}]
        if incident.segment is not None:
            events.append({
                "time": incident.created_at, "event": "road",
                "label": "Severe accident on the road" if incident.kind == "road_accident" else "Road closed",
            })
        if incident.flood is not None:
            events.append({"time": incident.created_at, "event": "road",
                           "label": f"Flood: {len(incident.flood.segments)} roads closed"})
        for service, reason in incident.refused:
            events.append({
                "time": incident.created_at, "event": "refused", "service": service,
                "label": f"No {SERVICES[service].label.lower()} " + (
                    "can reach it by road" if reason == "no_route" else "to send"),
            })
        for unit in incident.units:
            vehicle = unit.assigned_vehicle
            label = SERVICES[unit.emergency_type].label
            if vehicle is None:
                events.append({"time": incident.created_at, "event": "queued", "service": unit.emergency_type,
                               "label": f"{label} queued: none free"})
                continue
            index = simulation.vehicles.index(vehicle)
            waited = unit.dispatched_at - unit.created_at
            events.append({
                "time": unit.dispatched_at, "event": "dispatched", "service": unit.emergency_type, "vehicle_id": index,
                "label": f"{label} {index} dispatched" + (f" after waiting {waited:.0f} s" if waited > 0 else ""),
            })
            if unit.resolved_at is not None:
                target = SERVICES[unit.emergency_type].target_seconds
                events.append({
                    "time": unit.resolved_at, "event": "arrived", "service": unit.emergency_type, "vehicle_id": index,
                    "response_seconds": unit.response_time, "within_target": unit.response_time <= target,
                    "label": f"{label} {index} arrived: {unit.response_time:.0f} s"
                             + (f", {vehicle.reroutes} route change(s)" if vehicle.reroutes else ""),
                })
            if unit.cleared_at is not None:
                events.append({"time": unit.cleared_at, "event": "cleared", "service": unit.emergency_type,
                               "vehicle_id": index, "label": f"{label} {index} left the scene"})
        if incident.road_cleared_at is not None:
            events.append({"time": incident.road_cleared_at, "event": "road_cleared", "label": {
                "road_accident": "Accident cleared", "road_closure": "Road reopened",
            }.get(incident.kind, "Flood over")})
        if incident.resolved_at is not None:
            events.append({"time": incident.resolved_at, "event": "resolved", "label": "Incident resolved"
                           + (" by the operator" if incident.resolved_by == "operator" else "")})
        order = {"reported": 0, "road": 1, "refused": 2, "queued": 2, "dispatched": 3, "arrived": 4,
                 "cleared": 5, "road_cleared": 6, "resolved": 7}
        return sorted(events, key=lambda e: (e["time"], order[e["event"]]))

    def describe_incident(self, incident):
        return {
            "id": incident.ident,
            "kind": incident.kind,
            "label": LABELS[incident.kind],
            "latitude": incident.latitude,
            "longitude": incident.longitude,
            "status": self.incident_status(incident),
            "created_at": incident.created_at,
            "resolved_at": incident.resolved_at,
            "services": list(RESPONSE_PLANS[incident.kind]),
            "on_scene_seconds": ON_SCENE_SECONDS.get(incident.kind, 0),
            "units": [
                self.simulation.vehicles.index(u.assigned_vehicle) if u.assigned_vehicle else None
                for u in incident.units
            ],
            "road": None if incident.segment is None else [
                {"latitude": p[0], "longitude": p[1]} for p in incident.segment
            ],
            "flood_roads": 0 if incident.flood is None else len(incident.flood.segments),
            "target": {"latitude": incident.target[0], "longitude": incident.target[1]},
            "timeline": self.timeline(incident),
        }

    def metrics(self):
        """Response figures over every unit that has arrived, and the fleet
        now."""

        arrived = [u for i in self.incidents for u in i.units if u.resolved]
        times = [u.response_time for u in arrived]
        within = [u.response_time <= SERVICES[u.emergency_type].target_seconds for u in arrived]
        states = [self.unit_status(index, vehicle)[0] for index, vehicle in self.units()]
        by_service = {}
        for service in SERVICES:
            mine = [u.response_time for u in arrived if u.emergency_type == service]
            by_service[service] = {
                "responses": len(mine),
                "mean_seconds": sum(mine) / len(mine) if mine else None,
                "target_seconds": SERVICES[service].target_seconds,
                "available": sum(
                    1 for (index, vehicle), state in zip(self.units(), states)
                    if vehicle.vehicle_type == service and state in ("available", "returning")
                ),
                "units": sum(1 for _, vehicle in self.units() if vehicle.vehicle_type == service),
            }
        return {
            "responses": len(times),
            "mean_response_seconds": sum(times) / len(times) if times else None,
            "worst_response_seconds": max(times) if times else None,
            "within_target_percent": 100 * sum(within) / len(within) if within else None,
            "units_total": len(states),
            "units_available": sum(1 for s in states if s in ("available", "returning")),
            "units_en_route": sum(1 for s in states if s in ("en_route", "blocked")),
            "units_on_scene": sum(1 for s in states if s == "on_scene"),
            "units_returning": sum(1 for s in states if s == "returning"),
            "incidents_open": sum(1 for i in self.incidents if i.resolved_at is None),
            "incidents_resolved": sum(1 for i in self.incidents if i.resolved_at is not None),
            "queued_units": sum(1 for i in self.incidents for u in i.units if u.assigned_vehicle is None),
            "by_service": by_service,
        }

    def state(self):
        simulation = self.simulation
        return {
            "simulation_time": simulation.time,
            "incidents": [self.describe_incident(i) for i in self.incidents],
            "units": [self.describe_unit(index, vehicle) for index, vehicle in self.units()],
            "stations": [
                {"station_id": index, **info, "service": simulation.stations[index].station_type,
                 **simulation.stations[index].get_location()}
                for index, info in sorted(self.stations.items())
            ],
            "metrics": self.metrics(),
        }

    def replay(self):
        """Everything recorded for replay: each unit's track and each
        incident's timeline."""

        return {
            "simulation_time": self.simulation.time,
            "tracks": [
                {"vehicle_id": index, "service": self.simulation.vehicles[index].vehicle_type, "samples": track}
                for index, track in sorted(self.tracks.items())
            ],
            "incidents": [self.describe_incident(i) for i in self.incidents],
        }
