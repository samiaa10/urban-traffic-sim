import pickle
from pathlib import Path

from fastapi import FastAPI, HTTPException

from backend.routing.a_star import a_star


PROJECT_ROOT = Path(__file__).resolve().parents[2]
GRAPH_FILE = PROJECT_ROOT / "data" / "osm" / "norwich_graph.pkl"


app = FastAPI(
    title="NORWICH//SIM API",
    description="Urban traffic simulation and routing API",
    version="0.1.0"
)


with open(GRAPH_FILE, "rb") as file:
    graph = pickle.load(file)


@app.get("/")
def root():
    return {
        "name": "NORWICH//SIM",
        "status": "online"
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

    return {
        "algorithm": "A*",
        "start": start,
        "destination": destination,
        "path": path,
        "distance_metres": distance,
        "nodes_explored": nodes_explored
    }