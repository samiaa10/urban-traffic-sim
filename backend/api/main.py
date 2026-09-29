import pickle
from pathlib import Path
import math

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from backend.routing.a_star import a_star
from backend.simulation.simulation import Simulation


PROJECT_ROOT = Path(__file__).resolve().parents[2]
GRAPH_FILE = PROJECT_ROOT / "data" / "osm" / "norwich_graph.pkl"


app = FastAPI(
    title="NORWICH//SIM API",
    description="Urban Traffic Simulation and routing API",
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


simulation = None


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

    traffic = None

    if simulation is not None:
        traffic = simulation.get_traffic_density()

    blocked_segments = set()

    if simulation is not None:
        blocked_segments = simulation.get_blocked_segments()

    path, distance, nodes_explored = a_star(
    graph,
    start,
    destination,
    return_stats=True,
    traffic=traffic,
    blocked_segments=blocked_segments
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


@app.post("/simulation/start")
def start_simulation(start: int, destination: int):

    global simulation

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

    # --------------------------------
    # Vehicle 1 route
    # --------------------------------

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

    # Create simulation with Vehicle 1
    simulation = Simulation(path_coordinates)


    # --------------------------------
    # Vehicle 2 route
    # --------------------------------

    second_path, second_distance, second_nodes_explored = a_star(
        graph,
        destination,
        start,
        return_stats=True
    )

    second_path_coordinates = [
        {
            "latitude": graph.nodes[node]["latitude"],
            "longitude": graph.nodes[node]["longitude"]
        }
        for node in second_path
    ]

    # Add Vehicle 2
    simulation.add_vehicle(second_path_coordinates)


    # --------------------------------
    # Return simulation information
    # --------------------------------

    return {
        "status": "simulation started",
        "distance_metres": distance,
        "nodes_explored": nodes_explored,
        "vehicle_position": simulation.vehicles[0].get_current_location()
    }


@app.get("/simulation/state")
def simulation_state():

    if simulation is None:
        raise HTTPException(
            status_code=404,
            detail="No simulation is running"
        )

    vehicle = simulation.vehicles[0]

    return {
        "vehicle_position": vehicle.get_current_location(),
        "distance_travelled": vehicle.distance_travelled,
        "finished": vehicle.finished
    }


@app.post("/simulation/step")
def simulation_step():

    if simulation is None:
        raise HTTPException(
            status_code=404,
            detail="No simulation is running"
        )

    # Move all vehicles
    simulation.step()

    vehicles = []

    for index, vehicle in enumerate(simulation.vehicles):

        location = vehicle.get_current_location()

        vehicles.append({
            "vehicle_id": index,
            "latitude": location["latitude"],
            "longitude": location["longitude"],
            "distance_travelled": vehicle.distance_travelled,
            "finished": vehicle.finished
        })

    return {
        "vehicles": vehicles
    }

@app.get("/simulation/congestion")
def simulation_congestion():

    if simulation is None:
        raise HTTPException(
            status_code=404,
            detail="No simulation is running"
        )

    congestion = simulation.get_congestion()

    congestion_segments = []

    for segment, data in congestion.items():

        start_point, end_point = segment

        congestion_segments.append({
            "start": {
                "latitude": start_point[0],
                "longitude": start_point[1]
            },
            "end": {
                "latitude": end_point[0],
                "longitude": end_point[1]
            },
            "vehicle_count": data["vehicle_count"],
            "level": data["level"]
        })

    return {
        "congestion": congestion_segments
    }

@app.post("/incident/block")
def block_incident(
    start: int,
    destination: int
):
    if simulation is None:
        raise HTTPException(
            status_code=404,
            detail="No simulation is running"
        )

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

    segment = (
        (
            graph.nodes[start]["latitude"],
            graph.nodes[start]["longitude"]
        ),
        (
            graph.nodes[destination]["latitude"],
            graph.nodes[destination]["longitude"]
        )
    )

    simulation.block_segment(segment)

    return {
        "status": "incident created",
        "blocked_segment": segment
    }


@app.post("/incident/unblock")
def unblock_incident(
    start: int,
    destination: int
):
    if simulation is None:
        raise HTTPException(
            status_code=404,
            detail="No simulation is running"
        )

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

    segment = (
        (
            graph.nodes[start]["latitude"],
            graph.nodes[start]["longitude"]
        ),
        (
            graph.nodes[destination]["latitude"],
            graph.nodes[destination]["longitude"]
        )
    )

    simulation.unblock_segment(segment)

    return {
        "status": "incident removed",
        "blocked_segment": segment
    }


@app.get("/incidents")
def get_incidents():

    if simulation is None:
        raise HTTPException(
            status_code=404,
            detail="No simulation is running"
        )

    incidents = []

    for segment in simulation.get_blocked_segments():

        start_point, end_point = segment

        incidents.append({
            "start": {
                "latitude": start_point[0],
                "longitude": start_point[1]
            },
            "end": {
                "latitude": end_point[0],
                "longitude": end_point[1]
            }
        })

    return {
        "incidents": incidents
    }


@app.post("/simulation/reroute")
def reroute_vehicle(vehicle_id: int = 0):

    if simulation is None:
        raise HTTPException(
            status_code=404,
            detail="No simulation is running"
        )

    if vehicle_id < 0 or vehicle_id >= len(simulation.vehicles):
        raise HTTPException(
            status_code=404,
            detail="Vehicle not found"
        )

    vehicle = simulation.vehicles[vehicle_id]

    if vehicle.finished:
        raise HTTPException(
            status_code=400,
            detail="Vehicle has already finished"
        )

    # Find the vehicle's current location
    current_location = vehicle.get_current_location()

    # Find the nearest graph node to the vehicle
    nearest = None
    nearest_distance = float("inf")

    for node, data in graph.nodes.items():

        distance = math.sqrt(
            (data["latitude"] - current_location["latitude"]) ** 2
            + (data["longitude"] - current_location["longitude"]) ** 2
        )

        if distance < nearest_distance:
            nearest_distance = distance
            nearest = node

    # Find the vehicle's destination
    destination_point = vehicle.route[-1]

    destination_node = None
    nearest_distance = float("inf")

    for node, data in graph.nodes.items():

        distance = math.sqrt(
            (data["latitude"] - destination_point["latitude"]) ** 2
            + (data["longitude"] - destination_point["longitude"]) ** 2
        )

        if distance < nearest_distance:
            nearest_distance = distance
            destination_node = node

    # Current traffic
    traffic = simulation.get_traffic_density()

    # Current blocked roads
    blocked_segments = simulation.get_blocked_segments()

    # Calculate new route
    path, distance, nodes_explored = a_star(
        graph,
        nearest,
        destination_node,
        return_stats=True,
        traffic=traffic,
        blocked_segments=blocked_segments
    )

    if not path:
        raise HTTPException(
            status_code=404,
            detail="No alternative route available"
        )

    # Convert graph route to coordinates
    new_route = [
        {
            "latitude": graph.nodes[node]["latitude"],
            "longitude": graph.nodes[node]["longitude"]
        }
        for node in path
    ]

    # Replace vehicle's route
    vehicle.route = new_route
    vehicle.position = 0
    vehicle.distance_on_segment = 0

    return {
        "status": "vehicle rerouted",
        "vehicle_id": vehicle_id,
        "distance_metres": distance,
        "nodes_explored": nodes_explored,
        "route": new_route
    }

@app.post("/emergency/create")
def create_emergency(
    latitude: float,
    longitude: float,
    emergency_type: str = "ambulance"
):
    if simulation is None:
        raise HTTPException(
            status_code=404,
            detail="No simulation is running"
        )

    # Create the emergency
    emergency = simulation.create_emergency(
        latitude,
        longitude,
        emergency_type
    )

    # Find the nearest station with an available vehicle
    vehicle = simulation.dispatch_from_nearest_station(
        emergency
    )

    if vehicle is None:
        raise HTTPException(
            status_code=404,
            detail="No available emergency vehicle"
        )

    # Find the nearest graph node to the emergency
    nearest_emergency_node = None
    nearest_emergency_distance = float("inf")

    for node, data in graph.nodes.items():

        distance = math.sqrt(
            (data["latitude"] - latitude) ** 2
            + (data["longitude"] - longitude) ** 2
        )

        if distance < nearest_emergency_distance:
            nearest_emergency_distance = distance
            nearest_emergency_node = node

    # Find the nearest graph node to the dispatched vehicle
    current_location = vehicle.get_current_location()

    nearest_vehicle_node = None
    nearest_vehicle_distance = float("inf")

    for node, data in graph.nodes.items():

        distance = math.sqrt(
            (data["latitude"] - current_location["latitude"]) ** 2
            + (data["longitude"] - current_location["longitude"]) ** 2
        )

        if distance < nearest_vehicle_distance:
            nearest_vehicle_distance = distance
            nearest_vehicle_node = node

    # Get current traffic and blocked roads
    traffic = simulation.get_traffic_density()
    blocked_segments = simulation.get_blocked_segments()

    # Calculate emergency route
    path, distance, nodes_explored = a_star(
        graph,
        nearest_vehicle_node,
        nearest_emergency_node,
        return_stats=True,
        traffic=traffic,
        blocked_segments=blocked_segments
    )

    if not path:
        raise HTTPException(
            status_code=404,
            detail="No route to emergency"
        )

    # Convert graph path into coordinates
    new_route = [
        {
            "latitude": graph.nodes[node]["latitude"],
            "longitude": graph.nodes[node]["longitude"]
        }
        for node in path
    ]

    # Give the emergency vehicle its new route
    vehicle.route = new_route
    vehicle.position = 0
    vehicle.distance_on_segment = 0
    vehicle.distance_travelled = 0
    vehicle.finished = False

    return {
        "status": "emergency dispatched",
        "emergency_type": emergency_type,
        "vehicle_id": simulation.vehicles.index(vehicle),
        "emergency_location": {
            "latitude": latitude,
            "longitude": longitude
        },
        "route_distance_metres": distance,
        "nodes_explored": nodes_explored,
        "route": new_route
    }


@app.post("/station/create")
def create_station(
    latitude: float,
    longitude: float,
    station_type: str = "ambulance"
):
    if simulation is None:
        raise HTTPException(
            status_code=404,
            detail="No simulation is running"
        )

    station = simulation.add_station(
        latitude,
        longitude,
        station_type
    )

    return {
        "status": "station created",
        "station": station.get_location(),
        "station_type": station_type
    }


@app.post("/station/add-vehicle")
def add_station_vehicle(
    station_id: int = 0,
    speed: float = 20,
    vehicle_type: str = "ambulance"
):
    if simulation is None:
        raise HTTPException(
            status_code=404,
            detail="No simulation is running"
        )

    if station_id < 0 or station_id >= len(simulation.stations):
        raise HTTPException(
            status_code=404,
            detail="Station not found"
        )

    station = simulation.stations[station_id]

    station_location = station.get_location()

    route = [
        station_location,
        station_location
    ]

    vehicle = simulation.add_emergency_vehicle(
        route,
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
