"""Facts about the city model the site is running: the road network, the
stations, the census population and what is live now. Read from the
loaded map and the data files, nothing estimated."""

from functools import lru_cache

from fastapi import APIRouter, Depends

from backend.api.context import AppContext
from backend.api.routes.common import get_context


router = APIRouter(tags=["Service"])


@lru_cache(maxsize=4)
def _static_facts(graph_id, graph, network):
    """What does not change while the server runs (cached per map)."""

    from backend.optimiser.existing_stations import load
    from backend.simulation.population import output_areas

    seen = set()
    metres = 0.0
    for (a, b), length in network.length.items():
        key = (a, b) if a <= b else (b, a)
        if key in seen:
            continue
        seen.add(key)
        metres += length

    stations = {"ambulance": 0, "fire": 0, "police": 0}
    try:
        for station in load():
            stations[station["service"]] = stations.get(station["service"], 0) + 1
    except OSError:
        stations = None

    try:
        areas = output_areas()
        population = {"residents": sum(a[1] for a in areas), "output_areas": len(areas), "source": "Census 2021 (ONS)"}
    except OSError:
        population = None

    try:
        from backend.simulation.signal_sites import load_signal_junctions

        signals = len(load_signal_junctions())
    except OSError:
        signals = None

    return {
        "junctions": len(graph.nodes),
        "road_segments": len(seen),
        "road_km": round(metres / 1000, 1),
        "signalised_junctions": signals,
        "osm_stations": stations,
        "population": population,
    }


@router.get("/city/overview")
def city_overview(context: AppContext = Depends(get_context)):
    """The road network (junctions, roads, kilometres), OpenStreetMap's
    stations, the census population, signalised junctions, live runs and
    whether saving and the AI analyst are available."""

    from backend.intelligence.providers import provider_status

    facts = _static_facts(id(context.graph), context.graph, context.network)
    return {
        **facts,
        "live_runs": len(context.registry),
        "database": "configured" if context.database is not None else "not configured",
        "ai": provider_status(),
    }


# ------------------------------------------------------------
# Live weather (Open-Meteo: free, no key). Shown on the 3D map only: the
# traffic model does not change with the weather.
# ------------------------------------------------------------

WEATHER_URL = (
    "https://api.open-meteo.com/v1/forecast?latitude=52.6286&longitude=1.2925"
    "&current=temperature_2m,precipitation,rain,snowfall,cloud_cover,visibility,"
    "wind_speed_10m,wind_direction_10m,weather_code,is_day&timezone=Europe%2FLondon"
)
WEATHER_TTL = 600
_weather = {"at": 0.0, "value": None}


def fetch_weather(opener=None):
    """Norwich's current weather from Open-Meteo, as plain numbers."""

    import json
    import urllib.request

    opener = opener or (lambda url: urllib.request.urlopen(url, timeout=8))
    with opener(WEATHER_URL) as response:
        current = json.load(response)["current"]
    return {
        "time": current["time"],
        "temperature_c": current["temperature_2m"],
        "precipitation_mm": current["precipitation"],
        "rain_mm": current.get("rain", 0.0),
        "snowfall_cm": current.get("snowfall", 0.0),
        "cloud_cover_percent": current["cloud_cover"],
        "visibility_m": current.get("visibility"),
        "wind_speed_kmh": current["wind_speed_10m"],
        "wind_direction_deg": current.get("wind_direction_10m"),
        "weather_code": current["weather_code"],
        "is_day": bool(current["is_day"]),
        "source": "Open-Meteo",
    }


@router.get("/city/weather")
def city_weather():
    """Norwich's weather now, from Open-Meteo (refreshed every 10 minutes):
    the 3D map's sky, fog and rain. Not used by the traffic model. 503 if
    Open-Meteo cannot be reached."""

    import time

    from fastapi import HTTPException

    now = time.monotonic()
    if _weather["value"] is None or now - _weather["at"] > WEATHER_TTL:
        try:
            _weather["value"] = fetch_weather()
            _weather["at"] = now
        except Exception:
            if _weather["value"] is None:
                raise HTTPException(status_code=503, detail="Live weather is unavailable (Open-Meteo could not be reached)") from None
    return _weather["value"]
