"""Demand surges, traffic status, the dynamic rerouting switch and traffic
signals."""

import math
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.context import AppContext
from backend.api.routes.common import default_run, get_context, run_route
from backend.api.sessions import LiveRun
from backend.simulation.demand import Zone


router = APIRouter(tags=["Traffic"])


@run_route(router, "post", "/traffic/surge", "/traffic/surge")
def start_surge(
    latitude: float,
    longitude: float,
    radius_metres: float = 800,
    direction: str = "inbound",
    vehicles_per_minute: float = 60,
    duration_seconds: float = 900,
    run: LiveRun = Depends(default_run)
):
    """Rush-hour style demand: keep adding trips into (inbound) or out of
    (outbound) a circular zone for duration_seconds of simulated time."""

    checks = [
        (math.isfinite(latitude) and math.isfinite(longitude),
         "latitude and longitude must be numbers"),
        (math.isfinite(radius_metres) and 100 <= radius_metres <= 5000,
         "radius_metres must be between 100 and 5000"),
        (direction in ("inbound", "outbound"),
         "direction must be inbound or outbound"),
        (math.isfinite(vehicles_per_minute) and 0 < vehicles_per_minute <= 600,
         "vehicles_per_minute must be above 0 and at most 600"),
        (math.isfinite(duration_seconds) and 0 < duration_seconds <= 7200,
         "duration_seconds must be above 0 and at most 7200"),
    ]

    for ok, message in checks:
        if not ok:
            raise HTTPException(status_code=400, detail=message)

    with run.lock:
        try:
            surge = run.traffic.add_surge(
                Zone(latitude, longitude, radius_metres),
                direction,
                vehicles_per_minute,
                duration_seconds
            )
        except ValueError as error:
            # e.g. the zone is outside the road network
            raise HTTPException(status_code=400, detail=str(error))

        run.recorder.surge_started(surge)

        return {
            "status": "surge started",
            "surge": surge.describe(run.simulation.time)
        }


@run_route(router, "get", "/traffic/status", "/traffic/status")
def traffic_status(run: LiveRun = Depends(default_run)):
    """Vehicles, surges, accidents, closures and rerouting counters."""

    with run.lock:
        return run.traffic.describe()


@run_route(router, "post", "/traffic/rerouting", "/traffic/rerouting")
def configure_rerouting(
    enabled: bool,
    run: LiveRun = Depends(default_run)
):
    """Turn dynamic rerouting on or off (closures still reroute)."""

    with run.lock:
        run.traffic.policy.enabled = enabled
        run.recorder.rerouting_changed(enabled)

        return {
            "status": "rerouting " + ("enabled" if enabled else "disabled"),
            "policy": run.traffic.policy.describe()
        }


# ------------------------------------------------------------
# Traffic signals (simulation/signals.py)
# ------------------------------------------------------------

@run_route(router, "post", "/traffic/signals", "/traffic/signals")
def configure_signals(
    controller: Literal["none", "fixed", "adaptive"],
    emergency_priority: bool = False,
    junctions: Optional[List[int]] = Query(
        None, description="junction ids; default: those OpenStreetMap shows with traffic lights"
    ),
    green: float = Query(20, ge=5, le=120, description="fixed time: seconds of green per phase"),
    min_green: float = Query(7, ge=3, le=60, description="adaptive: shortest green"),
    max_green: float = Query(45, ge=5, le=180, description="adaptive: longest green"),
    run: LiveRun = Depends(default_run),
    context: AppContext = Depends(get_context),
):
    """Switch the run's traffic signals: none, fixed time or adaptive, with
    or without emergency vehicle priority. The signals take effect from the
    next simulated second and start from the first phase."""

    from backend.simulation.signal_sites import load_signal_junctions
    from backend.simulation.signals import SignalTiming, make_signals

    if min_green > max_green:
        raise HTTPException(status_code=422, detail="min_green must be at most max_green")

    nodes = junctions if junctions is not None else load_signal_junctions()
    timing = SignalTiming(
        green=green, min_green=min_green, max_green=max_green, max_wait=max(120.0, max_green)
    )
    with context.network_lock:
        signals = make_signals(run.traffic.network, nodes, controller, emergency_priority, timing)

    with run.lock:
        run.simulation.signals = signals

    return {
        "status": f"signals {controller}",
        "controller": controller,
        "emergency_priority": emergency_priority if signals else False,
        "junctions": len(signals.junctions) if signals else 0,
    }


@run_route(router, "get", "/traffic/signals", "/traffic/signals")
def signal_states(run: LiveRun = Depends(default_run)):
    """Every signalised junction now: its phase, the light each approach
    sees and the vehicles queued on it, from the simulation's state."""

    with run.lock:
        signals = run.simulation.signals
        if signals is None:
            return {"controller": "none", "emergency_priority": False, "junctions": []}
        return {
            "controller": signals.controller,
            "emergency_priority": signals.priority,
            "junctions": signals.describe(),
            "summary": signals.summary(),
        }
