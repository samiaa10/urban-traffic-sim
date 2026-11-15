"""Helpers shared by the route modules: finding the run a request is for,
registering run endpoints twice, and checking junctions and roads."""

import functools
import inspect
import math

from fastapi import Depends, HTTPException, Request

from backend.api.context import AppContext
from backend.api.sessions import LiveRun


# Per-run endpoints live under this prefix
RUN_PREFIX = "/simulations/{simulation_id}"


def get_context(request: Request) -> AppContext:
    return request.app.state.context


def default_run(context: AppContext = Depends(get_context)) -> LiveRun:
    """The run the original endpoints act on (404 if there is none)."""

    run = context.registry.default()

    if run is None:
        raise HTTPException(status_code=404, detail="No simulation is running")

    return run


def run_by_id(
    simulation_id: str, context: AppContext = Depends(get_context)
) -> LiveRun:
    """The live run named in the path (404 if there is no such run)."""

    run = context.registry.get(simulation_id)

    if run is None:
        raise HTTPException(status_code=404, detail="Simulation not found")

    return run


def run_route(router, method, legacy_path, run_path, **options):
    """Register a run endpoint at its original path and per run.

    The endpoint takes `run: LiveRun = Depends(default_run)`. It is
    registered as written at legacy_path (acting on the default run) and
    again at /simulations/{simulation_id}<run_path>, where `run` comes from
    the ID in the path instead. The handler code is the same for both.
    """

    def register(func):
        getattr(router, method)(legacy_path, **options)(func)

        signature = inspect.signature(func)
        parameters = [
            parameter.replace(default=Depends(run_by_id))
            if parameter.name == "run" else parameter
            for parameter in signature.parameters.values()
        ]

        @functools.wraps(func)
        def for_run(*args, **kwargs):
            return func(*args, **kwargs)

        for_run.__name__ = func.__name__ + "_for_run"
        for_run.__signature__ = signature.replace(parameters=parameters)

        getattr(router, method)(RUN_PREFIX + run_path, **options)(for_run)

        return func

    return register


def require_node(graph, node, label):
    if node not in graph.nodes:
        raise HTTPException(status_code=404, detail=f"{label} node not found")


def require_road(graph, start, destination):
    """404 if either junction is unknown, 400 if no road joins them."""

    require_node(graph, start, "Start")
    require_node(graph, destination, "Destination")

    if not (
        graph.has_edge(start, destination)
        or graph.has_edge(destination, start)
    ):
        raise HTTPException(
            status_code=400,
            detail="These two nodes are not directly connected by a road"
        )


def road_between(context, start, destination):
    """Segment key for the road joining two junctions (400/404 if none)."""

    require_road(context.graph, start, destination)

    position = context.network.position

    return tuple(sorted((position[start], position[destination])))


def point_pair(graph, start, destination):
    """((lat, lon), (lat, lon)) of two junctions, in the order given."""

    return (
        (graph.nodes[start]["latitude"], graph.nodes[start]["longitude"]),
        (
            graph.nodes[destination]["latitude"],
            graph.nodes[destination]["longitude"]
        ),
    )


def segment_json(segment):
    start_point, end_point = segment

    return {
        "start": {"latitude": start_point[0], "longitude": start_point[1]},
        "end": {"latitude": end_point[0], "longitude": end_point[1]},
    }


def path_points(graph, path):
    """Junction ids -> the {latitude, longitude} points vehicles follow."""

    return [
        {
            "latitude": graph.nodes[node]["latitude"],
            "longitude": graph.nodes[node]["longitude"]
        }
        for node in path
    ]


def nearest_node_id(graph, latitude, longitude):
    """The junction closest to a point (straight-line, in degrees)."""

    nearest = None
    nearest_distance = float("inf")

    for node, data in graph.nodes.items():
        distance = math.sqrt(
            (data["latitude"] - latitude) ** 2
            + (data["longitude"] - longitude) ** 2
        )

        if distance < nearest_distance:
            nearest_distance = distance
            nearest = node

    return nearest


def closures_for(context, simulation_id):
    """Closed roads to plan around: those of the named run, else of the
    default run, else none. A copy, so planning cannot see a road close
    halfway through."""

    if simulation_id is not None:
        run = context.registry.get(simulation_id)

        if run is None:
            raise HTTPException(status_code=404, detail="Simulation not found")
    else:
        run = context.registry.default()

    if run is None:
        return set(), None

    with run.lock:
        return set(run.simulation.get_blocked_segments()), run
