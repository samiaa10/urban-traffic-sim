"""Emergency stations and the vehicles parked at them."""

import math
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from backend.api.routes.common import default_run, run_route
from backend.api.sessions import LiveRun
from backend.simulation.services import SERVICES, service


def known_service(name, what):
    """The Service, or 400 naming the services there are."""

    try:
        return service(name)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=f"{what}: {error}") from None


router = APIRouter()


@run_route(router, "post", "/station/create", "/stations")
def create_station(
    latitude: float,
    longitude: float,
    station_type: str = "ambulance",
    run: LiveRun = Depends(default_run)
):
    """A station of one emergency service: ambulance, fire or police."""

    known_service(station_type, "station_type")

    with run.lock:
        station = run.simulation.add_station(
            latitude,
            longitude,
            station_type
        )
        run.recorder.station_added(len(run.simulation.stations) - 1, station)

        return {
            "status": "station created",
            "station": station.get_location(),
            "station_type": station_type
        }


@run_route(router, "post", "/station/add-vehicle", "/stations/add-vehicle")
def add_station_vehicle(
    station_id: int = 0,
    speed: Optional[float] = None,
    vehicle_type: Optional[str] = None,
    run: LiveRun = Depends(default_run)
):
    """A vehicle of the station's service. vehicle_type defaults to the
    station's service (and must match it); speed to the service's speed
    (services.py: ambulance 20, fire 17, police 22 m/s)."""

    with run.lock:
        simulation = run.simulation

        if station_id < 0 or station_id >= len(simulation.stations):
            raise HTTPException(
                status_code=404,
                detail="Station not found"
            )

        # A vehicle that cannot move would never arrive (and used to crash
        # /emergency/status with a division by zero)
        if speed is not None and (not math.isfinite(speed) or speed <= 0):
            raise HTTPException(
                status_code=400,
                detail="speed must be above 0"
            )

        station = simulation.stations[station_id]

        if vehicle_type is None:
            vehicle_type = station.station_type
        known_service(vehicle_type, "vehicle_type")
        if vehicle_type != station.station_type and station.station_type in SERVICES:
            raise HTTPException(
                status_code=400,
                detail=f"A {station.station_type} station only holds {station.station_type} vehicles",
            )
        if speed is None:
            speed = SERVICES[vehicle_type].speed

        station_location = station.get_location()

        vehicle = simulation.add_emergency_vehicle(
            [station_location, station_location],
            station,
            speed=speed,
            vehicle_type=vehicle_type
        )

        vehicle_id = simulation.vehicles.index(vehicle)
        run.recorder.vehicle_added(station_id, vehicle_id, vehicle)

    return {
        "status": "emergency vehicle added",
        "station_id": station_id,
        "vehicle_id": vehicle_id,
        "vehicle_type": vehicle_type
    }
