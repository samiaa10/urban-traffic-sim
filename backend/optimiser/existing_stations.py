"""Norwich's real emergency stations, from OpenStreetMap, as the "existing"
layout the planning view compares optimised layouts with.

    python -m backend.optimiser.existing_stations             re-fetch data/osm/norwich_emergency_stations.json
    python -m backend.optimiser.existing_stations saved.json  build it from a saved Overpass answer
                                                              (where Python's TLS cannot reach Overpass)

The project's road download has roads only, so the stations come from one
Overpass API query over the Norwich area (amenity=fire_station,
amenity=police, emergency=ambulance_station), saved with the data's
timestamp. It is OpenStreetMap data, under the ODbL like the road graph.

What OpenStreetMap has (fetched 22 November 2026): three fire stations and
four police stations in or near the city, and, for ambulances, only the East
Anglian Air Ambulance base at the airport, which is a helicopter base and is
left out (road response from it would mean nothing). So there is no existing
ambulance layout to compare with; the planning view says so rather than
invent one.
"""

import json
import urllib.parse
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATIONS_FILE = PROJECT_ROOT / "data" / "osm" / "norwich_emergency_stations.json"

BOUNDS = (52.58, 1.20, 52.68, 1.37)   # south, west, north, east
QUERY = (
    "[out:json][timeout:60];("
    'nwr["amenity"="fire_station"]({b});'
    'nwr["amenity"="police"]({b});'
    'nwr["emergency"="ambulance_station"]({b});'
    'nwr["amenity"="ambulance_station"]({b});'
    ");out center tags;"
)

SERVICE_OF = {"fire_station": "fire", "police": "police", "ambulance_station": "ambulance"}


def _service(tags):
    return SERVICE_OF.get(tags.get("amenity")) or SERVICE_OF.get(tags.get("emergency"))


def _excluded(tags):
    """Why a station is left out (None: kept)."""
    name = (tags.get("name") or "").lower()
    if "air ambulance" in name or tags.get("aeroway") or tags.get("air_ambulance") == "yes":
        return "air ambulance base: aircraft, not road vehicles"
    return None


def parse(data):
    stations = []
    for element in data["elements"]:
        tags = element.get("tags", {})
        service = _service(tags)
        if service is None:
            continue
        centre = element.get("center", element)
        stations.append({
            "service": service,
            "name": tags.get("name") or f"Unnamed {service} station",
            "latitude": centre["lat"],
            "longitude": centre["lon"],
            "osm": f"{element['type']}/{element['id']}",
            "excluded": _excluded(tags),
        })
    return sorted(stations, key=lambda s: (s["service"], s["name"]))


def load(path=STATIONS_FILE):
    """The stations kept for comparison (excluded ones left out)."""
    with open(path) as file:
        return [s for s in json.load(file)["stations"] if not s["excluded"]]


def fetch():
    bounds = ",".join(str(b) for b in BOUNDS)
    body = urllib.parse.urlencode({"data": QUERY.format(b=bounds)}).encode()
    request = urllib.request.Request(
        "https://overpass-api.de/api/interpreter", data=body,
        headers={"User-Agent": "norwich-sim/1.0 (educational project)"},
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        return json.load(response)


def main():
    import sys

    if len(sys.argv) > 1:
        with open(sys.argv[1]) as file:
            data = json.load(file)
    else:
        data = fetch()
    stations = parse(data)
    STATIONS_FILE.write_text(json.dumps({
        "source": "OpenStreetMap (ODbL) via the Overpass API",
        "osm_timestamp": data.get("osm3s", {}).get("timestamp_osm_base"),
        "bounds": BOUNDS,
        "stations": stations,
    }, indent=1) + "\n")
    kept = [s for s in stations if not s["excluded"]]
    print(f"{len(stations)} stations, {len(kept)} kept: " + ", ".join(
        f"{service} {sum(1 for s in kept if s['service'] == service)}" for service in ("ambulance", "fire", "police")))


if __name__ == "__main__":
    main()
