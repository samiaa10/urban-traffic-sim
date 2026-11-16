"""Generating many scenarios from one: the building blocks of large studies.

Each function takes a base ScenarioSpec and returns a list of specs, the
first being the baseline where that makes sense. Everything is
deterministic: the same arguments always give the same scenarios.
"""

import random

from backend.scenarios.spec import ScenarioSpec


def _with(base, **changes):
    return ScenarioSpec.model_validate({**base.model_dump(mode="json"), **changes})


def seeds(base, count, first_seed=1):
    """The same scenario with `count` different seeds (different trips and
    emergencies): how much results vary by chance alone."""

    return [
        _with(base, name=f"{base.name} (seed {seed})", seed=seed)
        for seed in range(first_seed, first_seed + count)
    ]


def each_closure(base, roads):
    """The baseline, then one scenario per road with only that road closed
    for the whole run: which single closure would hurt most."""

    specs = [_with(base, name=f"{base.name}: baseline")]

    for start, destination in roads:
        specs.append(_with(
            base,
            name=f"Close {start}-{destination}",
            closures=list(base.model_dump(mode="json")["closures"])
            + [{"start": start, "destination": destination}],
        ))

    return specs


def candidate_roads(network, zone=None, limit=None):
    """Roads (as sorted junction pairs) inside the main connected area,
    optionally only those with both ends in `zone`, in a fixed order."""

    from backend.simulation.demand import metres_between

    nodes = network.main_component()
    roads = set()

    for (a, b) in network.length:
        if a in nodes and b in nodes and a != b:
            roads.add((a, b) if a < b else (b, a))

    if zone is not None:
        def inside(node):
            latitude, longitude = network.position[node]
            return metres_between(latitude, longitude, zone.latitude, zone.longitude) <= zone.radius_metres

        roads = {road for road in roads if inside(road[0]) and inside(road[1])}

    ordered = sorted(roads)
    return ordered[:limit] if limit else ordered


def random_closures(base, roads, count, closures_per_scenario, sweep_seed=1):
    """The baseline, then `count` scenarios each closing
    `closures_per_scenario` roads drawn at random from `roads`
    (Monte Carlo): the spread of outcomes under random disruption."""

    if closures_per_scenario > len(roads):
        raise ValueError("more closures per scenario than candidate roads")

    generator = random.Random(f"closures:{sweep_seed}")
    specs = [_with(base, name=f"{base.name}: baseline")]

    for number in range(1, count + 1):
        chosen = generator.sample(roads, closures_per_scenario)
        specs.append(_with(
            base,
            name=f"Random closures #{number}",
            closures=[{"start": a, "destination": b} for a, b in sorted(chosen)],
        ))

    return specs


def station_layouts(base, layouts):
    """One scenario per station layout (a list of Station dicts each), with
    the same trips and emergencies: which layout answers fastest."""

    return [
        _with(base, name=name, stations=stations)
        for name, stations in layouts
    ]
