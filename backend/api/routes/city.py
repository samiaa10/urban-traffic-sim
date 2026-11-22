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
