"""Network resilience: how reachable Norwich is before, during and after a
disruption, for ordinary traffic and for the emergency services.

ACCESSIBILITY is measured on the road network itself, not on the trips the
scenario happened to draw: from every junction the demand model uses to a
fixed sample of DESTINATIONS junctions (chosen by the scenario's seed), the
fastest drive at the speed limits, around closed roads and slowed by
accidents and floods (not by traffic, so it shows what the disruption did
to the network). For each snapshot:

  * reachable: the share of (junction, destination) pairs with any route
    (closures can cut parts of the city off);
  * mean and worst drive time over the reachable pairs, and the mean
    increase over the same pairs before the disruption;
  * the areas (impact.area_cell, about 500 m squares) whose junctions lost
    the most: the largest mean increase, and any junctions cut off.

EMERGENCY accessibility does the same from the scenario's stations: for
each sampled destination, the fastest drive from the nearest station of
each service at its vehicles' speed (emergency vehicles are exempt from
limits), and the share reached within the service's target.

Snapshots are taken BEFORE anything happens, at the moment of greatest
DISRUPTION (the most roads closed or affected; re-measured whenever that
grows), and AFTER every disruption has ended (or at the end of the run if
some never ended). The dynamic side (journey times, delay, throughput,
congestion, emergency responses) comes from the scenario's own figures,
split into the same three windows (summarise).
"""

import math
import random
import statistics

from backend.scenarios.impact import area_cell, cell_bounds


DESTINATIONS = 60


class ResilienceProbe:

    def __init__(self, world, simulation, spec, car_speed, undisrupted_before=False):
        self.world = world
        self.simulation = simulation
        self.car_speed = car_speed
        network = world.network
        stream = random.Random(f"{spec.seed}:resilience")
        nodes = sorted(world.demand.nodes, key=str)
        self.origins = nodes
        self.destinations = stream.sample(nodes, min(DESTINATIONS, len(nodes)))

        # Each service's station junctions and speed, and its target
        from backend.simulation.dispatch import nearest_junction

        self.services = {}
        for station in spec.stations:
            node = nearest_junction(world.graph, station.latitude, station.longitude)
            entry = self.services.setdefault(station.service, {"nodes": set(), "speed": station.speed})
            entry["nodes"].add(node)
            entry["speed"] = max(entry["speed"], station.speed)
        self.targets = {group.service: group.target_seconds for group in spec.emergency_groups if group.count}

        self.before = self._snapshot("before", undisrupted=undisrupted_before)
        self.during = None
        self.after = None
        self._worst_level = 0
        self.disrupted_from = None
        self.disrupted_until = None

    # ---- the road network's state -------------------------------------

    def level(self):
        """How many roads are closed or slowed by an incident now."""
        return len(self.simulation.get_blocked_segments() | self.simulation.incident_segments())

    def _factor(self):
        simulation = self.simulation

        def factor(segment):
            effect = simulation.road_incident(segment)
            return 1.0 if effect is None else effect.speed_factor

        return factor

    def _snapshot(self, label, undisrupted=False):
        """undisrupted: as if no road were closed or slowed (a live run's
        "before", measured while the disruption is already on)."""
        network = self.world.network
        simulation = self.simulation
        blocked = set() if undisrupted else set(simulation.get_blocked_segments())
        factor = self._factor() if simulation.incident_segments() and not undisrupted else None
        origins = self.origins

        times = {}   # destination -> {origin: seconds}
        emergency = {service: [] for service in self.services}
        for destination in self.destinations:
            seconds, _ = network.fastest_tree_to(
                destination, self.car_speed, speed_factor=factor,
                blocked_segments=blocked, limits=True,
            )
            # Plain floats (the map's road lengths are NumPy numbers)
            times[destination] = {o: float(seconds.get(o, math.inf)) for o in origins if o != destination}
            for service, entry in self.services.items():
                to_here, _ = network.fastest_tree_to(
                    destination, entry["speed"], speed_factor=factor,
                    blocked_segments=blocked, limits=False, targets=entry["nodes"],
                )
                emergency[service].append(float(min((to_here.get(n, math.inf) for n in entry["nodes"]), default=math.inf)))

        return {"label": label, "time": simulation.time, "closed_roads": len(blocked),
                "affected_roads": self.level(), "times": times, "emergency": emergency}

    # ---- called by the runner -----------------------------------------

    def after_step(self):
        level = self.level()
        now = self.simulation.time
        if level > 0 and self.disrupted_from is None:
            self.disrupted_from = now
        if level > self._worst_level:
            self._worst_level = level
            self.during = self._snapshot("during")
        if level == 0 and self.disrupted_from is not None and self.after is None:
            self.disrupted_until = now
            self.after = self._snapshot("after")

    def finish(self):
        if self.after is None:
            self.after = self._snapshot("after")

    # ---- results -------------------------------------------------------

    def _accessibility(self, snapshot):
        before = self.before["times"]
        pairs = reachable = 0
        total = 0.0
        worst = 0.0
        increases = []
        per_origin = {}
        for destination, row in snapshot["times"].items():
            for origin, seconds in row.items():
                base = before[destination].get(origin, math.inf)
                if math.isinf(base):
                    continue   # not reachable even before: not counted
                pairs += 1
                entry = per_origin.setdefault(origin, [0.0, 0, 0])
                if math.isinf(seconds):
                    entry[2] += 1
                    continue
                reachable += 1
                total += seconds
                worst = max(worst, seconds)
                increases.append(seconds - base)
                entry[0] += seconds - base
                entry[1] += 1

        areas = {}
        for origin, (increase, count, cut_off) in per_origin.items():
            latitude, longitude = self.world.network.position[origin]
            cell = area_cell(latitude, longitude)
            area = areas.setdefault(cell, [0.0, 0, 0, 0])
            area[0] += increase
            area[1] += count
            area[2] += cut_off
            area[3] += 1
        ranked = sorted(
            (
                {
                    "bounds": cell_bounds(cell),
                    "junctions": junctions,
                    "mean_increase_seconds": increase / count if count else None,
                    "cut_off_pairs": cut_off,
                }
                for cell, (increase, count, cut_off, junctions) in areas.items()
            ),
            key=lambda a: (-(a["cut_off_pairs"]), -(a["mean_increase_seconds"] or 0)),
        )
        return {
            "time": snapshot["time"],
            "closed_roads": snapshot["closed_roads"],
            "affected_roads": snapshot["affected_roads"],
            "reachable_percent": 100 * reachable / pairs if pairs else None,
            "unreachable_pairs": pairs - reachable,
            "mean_seconds": total / reachable if reachable else None,
            "worst_seconds": worst if reachable else None,
            "mean_increase_seconds": statistics.fmean(increases) if increases else None,
            "worst_increase_seconds": max(increases) if increases else None,
            "areas": [a for a in ranked if a["cut_off_pairs"] or (a["mean_increase_seconds"] or 0) > 0][:5],
        }

    def _emergency(self, snapshot):
        out = {}
        for service, seconds in snapshot["emergency"].items():
            reached = [s for s in seconds if not math.isinf(s)]
            target = self.targets.get(service)
            out[service] = {
                "reachable_percent": 100 * len(reached) / len(seconds) if seconds else None,
                "mean_seconds": statistics.fmean(reached) if reached else None,
                "worst_seconds": max(reached) if reached else None,
                "within_target_percent": (
                    100 * sum(1 for s in reached if s <= target) / len(seconds)
                    if target and seconds else None
                ),
            }
        return out

    def summarise(self, timeseries, emergencies):
        snapshots = {"before": self.before, "during": self.during or self.before, "after": self.after}
        start, end = self.disrupted_from, self.disrupted_until

        def window(t):
            if start is None or t < start:
                return "before"
            if end is None or t < end:
                return "during"
            return "after"

        # Dynamic figures per window, from the per-minute series
        dynamic = {}
        previous_finished = 0
        for row in timeseries:
            label = window(row["time"] - 1)
            entry = dynamic.setdefault(label, {"minutes": 0, "arrivals": 0, "speed": [], "delay": 0.0, "vehicle_seconds": 0})
            entry["minutes"] += 1
            entry["arrivals"] += row["finished_vehicles"] - previous_finished
            previous_finished = row["finished_vehicles"]
            if row["mean_speed_factor"] is not None:
                entry["speed"].append(row["mean_speed_factor"])
            entry["delay"] += row["delay_vehicle_seconds"]
            entry["vehicle_seconds"] += row["vehicle_seconds"]
        traffic = {
            label: {
                "arrivals_per_minute": entry["arrivals"] / entry["minutes"],
                "mean_speed_factor": statistics.fmean(entry["speed"]) if entry["speed"] else None,
                "delay_share": entry["delay"] / entry["vehicle_seconds"] if entry["vehicle_seconds"] else None,
            }
            for label, entry in dynamic.items()
        }

        # Emergency responses by when the call came
        responses = {}
        for e in emergencies:
            emergency = e["emergency"]
            if emergency is None or not emergency.resolved:
                continue
            responses.setdefault(window(e["at"]), []).append(emergency.response_time)

        # Recovery: minutes after the disruption ended until traffic moves as
        # well as it did before (mean speed back within 2 points)
        recovery = None
        before_speed = traffic.get("before", {}).get("mean_speed_factor")
        if end is not None and before_speed is not None:
            for row in timeseries:
                if row["time"] >= end and row["mean_speed_factor"] is not None \
                        and row["mean_speed_factor"] >= before_speed - 0.02:
                    recovery = max(0, row["time"] - end)
                    break

        return {
            "disrupted_from": start,
            "disrupted_until": end,
            "destinations_sampled": len(self.destinations),
            "accessibility": {k: self._accessibility(v) for k, v in snapshots.items()},
            "emergency_access": {k: self._emergency(v) for k, v in snapshots.items()},
            "traffic": traffic,
            "emergency_responses": {
                label: {"count": len(values), "mean_seconds": statistics.fmean(values)}
                for label, values in responses.items()
            },
            "recovery_seconds": recovery,
        }


def live_accessibility(graph, network, demand_nodes, simulation, car_speed, seed=1):
    """For a live run: accessibility now against the same network with no
    disruption, for ordinary traffic and from the run's stations."""
    from types import SimpleNamespace

    from backend.simulation.services import SERVICES

    stations = [
        SimpleNamespace(latitude=s.latitude, longitude=s.longitude, service=s.station_type,
                        speed=SERVICES[s.station_type].speed)
        for s in simulation.stations if s.station_type in SERVICES
    ]
    services = sorted({s.service for s in stations})
    spec = SimpleNamespace(
        seed=seed, stations=stations,
        emergency_groups=[SimpleNamespace(service=x, target_seconds=SERVICES[x].target_seconds, count=1)
                          for x in services],
    )
    world = SimpleNamespace(graph=graph, network=network, demand=SimpleNamespace(nodes=demand_nodes))
    probe = ResilienceProbe(world, simulation, spec, car_speed, undisrupted_before=True)
    now = probe._snapshot("now")
    return {
        "destinations_sampled": len(probe.destinations),
        "closed_roads": now["closed_roads"],
        "affected_roads": now["affected_roads"],
        "accessibility": {"before": probe._accessibility(probe.before), "now": probe._accessibility(now)},
        "emergency_access": {"before": probe._emergency(probe.before), "now": probe._emergency(now)},
    }
