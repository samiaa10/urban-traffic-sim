"""Where people live: emergencies placed in proportion to population.

Each 2021 Census output area in Norwich (data/population/, ONS, Open
Government Licence) has a population and a population-weighted centroid.
Its population is given to the usable junction nearest that centroid (the
junctions trips and emergencies use: the main connected area of the map),
so each junction's weight is the number of residents it stands for.
Junctions no centroid is nearest to get no weight.

An emergency location is then drawn with probability proportional to the
weight: busier residential areas get more emergencies, as they do in
reality (emergencies also happen at work, on roads and in town centres,
which this does not model). The uniform distribution (every junction
equally likely) is kept as the default, so results made before stay the
same.
"""

import bisect
import csv
import itertools
import weakref
from pathlib import Path

from backend.routing.graph import Graph
from backend.routing.spatial import GridIndex


PROJECT_ROOT = Path(__file__).resolve().parents[2]
POPULATION_FILE = PROJECT_ROOT / "data" / "population" / "norwich_output_areas_2021.csv"

UNIFORM = "uniform"
POPULATION = "population"
DISTRIBUTIONS = (UNIFORM, POPULATION)

_WEIGHTS = weakref.WeakKeyDictionary()


def output_areas(path=POPULATION_FILE):
    """[(code, population, latitude, longitude)] from the census file."""

    with open(path, newline="") as file:
        return [
            (row["oa21cd"], int(row["population"]), float(row["latitude"]), float(row["longitude"]))
            for row in csv.DictReader(file)
        ]


def junction_weights(network, nodes, areas=None):
    """{junction: residents} for the given usable junctions (cached per
    network for the default census file)."""

    cache_key = tuple(nodes) if areas is None else None
    cached = _WEIGHTS.get(network)
    if cached is not None and cache_key is not None and cached[0] == cache_key:
        return cached[1]

    if areas is None:
        areas = output_areas()

    usable = Graph()
    for node in nodes:
        latitude, longitude = network.position[node]
        usable.add_node(node, latitude, longitude)
    index = GridIndex(usable)

    weights = {}
    for _, population, latitude, longitude in areas:
        nearest = index.nearest(latitude, longitude)
        if nearest:
            weights[nearest[0]] = weights.get(nearest[0], 0) + population

    if cache_key is not None:
        _WEIGHTS[network] = (cache_key, weights)
    return weights


class _GraphPositions:
    """A graph seen as junction -> (latitude, longitude), like a RoadNetwork."""

    def __init__(self, graph):
        self.position = {
            node: (data["latitude"], data["longitude"]) for node, data in graph.nodes.items()
        }


_GRAPH_WEIGHTS = weakref.WeakKeyDictionary()


def graph_junction_weights(graph, nodes):
    """junction_weights for a plain Graph (the optimiser's), cached per
    graph and junction set."""

    key = tuple(nodes)
    cached = _GRAPH_WEIGHTS.get(graph)
    if cached is None or cached[0] != key:
        weights = junction_weights(_GraphPositions(graph), list(nodes), output_areas())
        _GRAPH_WEIGHTS[graph] = cached = (key, weights)
    return cached[1]


def weighted_draws(weights, generator, count):
    """`count` junctions drawn in proportion to their weights."""

    junctions = sorted(weights)
    cumulative = list(itertools.accumulate(weights[n] for n in junctions))
    total = cumulative[-1]
    return [
        junctions[bisect.bisect_right(cumulative, generator.random() * total)]
        for _ in range(count)
    ]


class Sampler:
    """Draws junctions: uniformly, or weighted by population."""

    def __init__(self, network, nodes, distribution=UNIFORM):
        if distribution not in DISTRIBUTIONS:
            raise ValueError(f"distribution must be one of {', '.join(DISTRIBUTIONS)}")

        self.distribution = distribution
        self.nodes = list(nodes)

        if distribution == POPULATION:
            weights = junction_weights(network, self.nodes)
            self.weighted = sorted(weights)
            self.cumulative = list(itertools.accumulate(weights[n] for n in self.weighted))

    def draw(self, generator):
        if self.distribution == UNIFORM:
            # The same call as before, so uniform draws are unchanged
            return generator.choice(self.nodes)

        point = generator.random() * self.cumulative[-1]
        return self.weighted[bisect.bisect_right(self.cumulative, point)]
