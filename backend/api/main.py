import pickle
from pathlib import Path
import math

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from backend.routing.a_star import a_star


PROJECT_ROOT = Path(__file__).resolve().parents[2]
GRAPH_FILE = PROJECT_ROOT / "data" / "osm" / "norwich_graph.pkl"


app = FastAPI(
    title="NORWICH//SIM API",
    description="Urban traffic simulation and routing API",
    version="0.1.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


with open(GRAPH_FILE, "rb") as file:
    graph = pickle.load(file)


@app.get("/")
def root():
    return {
        "name": "NORWICH//SIM",
        "status": "online"
    }

@app.get("/nearest-node")
def nearest_node(latitude: float, longitude: float):
    nearest = None
    nearest_distance = float("inf")

    for node, data in graph.nodes.items():
        node_lat = data["latitude"]
        node_lon = data["longitude"]

        distance = math.sqrt(
            (node_lat - latitude) ** 2 +
            (node_lon - longitude) ** 2
        )

        if distance < nearest_distance:
            nearest_distance = distance
            nearest = node

    return {
        "node": nearest,
        "latitude": graph.nodes[nearest]["latitude"],
        "longitude": graph.nodes[nearest]["longitude"]
    }


@app.get("/route")
def route(start: int, destination: int):
    if start not in graph.nodes:
        raise HTTPException(
            status_code=404,
            detail="Start node not found"
        )

    if destination not in graph.nodes:
        raise HTTPException(
            status_code=404,
            detail="Destination node not found"
        )

    path, distance, nodes_explored = a_star(
        graph,
        start,
        destination,
        return_stats=True
    )

    path_coordinates = [
    {
        "latitude": graph.nodes[node]["latitude"],
        "longitude": graph.nodes[node]["longitude"]
    }
    for node in path
    ]

    return {
    "algorithm": "A*",
    "start": start,
    "destination": destination,
    "path": path,
    "path_coordinates": path_coordinates,
    "distance_metres": distance,
    "nodes_explored": nodes_explored
}