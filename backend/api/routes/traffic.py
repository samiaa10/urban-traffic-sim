"""Demand surges, traffic status and the dynamic rerouting switch."""

import math

from fastapi import APIRouter, Depends, HTTPException

from backend.api.routes.common import default_run, run_route
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
