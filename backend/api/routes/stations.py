"""Emergency stations and the vehicles parked at them."""

import math

from fastapi import APIRouter, Depends, HTTPException

from backend.api.routes.common import default_run, run_route
from backend.api.sessions import LiveRun


router = APIRouter()


@run_route(router, "post", "/station/create", "/stations")
def create_station(
    latitude: float,
    longitude: float,
    station_type: str = "ambulance",
    run: LiveRun = Depends(default_run)
):
    with run.lock:
        station = run.simulation.add_station(
            latitude,
            longitude,
            station_type
        )

        return {
            "status": "station created",
            "station": station.get_location(),
            "station_type": station_type
        }


@run_route(router, "post", "/station/add-vehicle", "/stations/add-vehicle")
def add_station_vehicle(
    station_id: int = 0,
    speed: float = 20,
    vehicle_type: str = "ambulance",
    run: LiveRun = Depends(default_run)
):
    with run.lock:
        simulation = run.simulation

        if station_id < 0 or station_id >= len(simulation.stations):
            raise HTTPException(
                status_code=404,
                detail="Station not found"
            )

        # A vehicle that cannot move would never arrive (and used to crash
        # /emergency/status with a division by zero)
        if not math.isfinite(speed) or speed <= 0:
            raise HTTPException(
                status_code=400,
                detail="speed must be above 0"
            )

        station = simulation.stations[station_id]
        station_location = station.get_location()

        vehicle = simulation.add_emergency_vehicle(
            [station_location, station_location],
            station,
            speed=speed,
            vehicle_type=vehicle_type
        )

        vehicle_id = simulation.vehicles.index(vehicle)

    return {
        "status": "emergency vehicle added",
        "station_id": station_id,
        "vehicle_id": vehicle_id,
        "vehicle_type": vehicle_type
    }
