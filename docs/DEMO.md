# Demo guide

A short walkthrough of the main features: route calculation, traffic simulation, road closures and ambulance rerouting. You can follow it in the web interface, or reproduce the same steps directly against the API (a good way to see exactly what each action does).

Before you start, complete the setup in the [README](../README.md#getting-started): the Norwich data file must exist, the backend must be running on port 8000, and for the web interface the frontend must be open at `http://localhost:5173`.

## A. Using the web interface

The control names below describe what each action does. If the wording on a button differs slightly, trust what is on screen. The endpoint in brackets is what the action calls.

1. **Calculate a route.** Choose a start and a destination in Norwich and calculate the route. The frontend snaps both points to their nearest junctions (`/nearest-node`) and draws the A\* route (`/route`). Route information is shown. Controls that need a route show the tooltip "Calculate a route first" until you have one.
2. **Start the traffic simulation.** Start the simulation (`/simulation/start`). Vehicles begin driving, and the status indicator changes from "Waiting for input" to "Running", then "Completed" when the vehicle has finished. While it runs, the frontend steps the simulation (`/simulation/step`) and draws the congestion on the roads (`/simulation/congestion`).
3. **Close a road.** With the simulation running, close a road on the route (`/incident/block`). The backend gives a new route to vehicles whose remaining route used that road, and the interface also asks for vehicle 0 to be rerouted (`/simulation/reroute`). The vehicle then drives around the closure.
4. **Dispatch an ambulance.** Create an emergency station and an ambulance (`/station/create`, `/station/add-vehicle`), then raise an emergency at a location (`/emergency/create`). The nearest available ambulance is dispatched and its route is drawn. Emergency status (state, ETA, response time, reroutes) is polled from `/emergency/status`.
5. **Close a road ahead of the ambulance.** While the ambulance is driving, use the control that closes the road ahead (`/emergency/block-ahead`). The ambulance is rerouted around the closure, and the extra distance and time of the detour are reported.

Things to watch for: the route changing when a road closes, the ambulance's response time being measured in *simulated* seconds, and the detour making the response slower.

## B. Reproducing the same steps with the API

Open the interactive docs at `http://127.0.0.1:8000/docs` to try each endpoint from the browser, or use `curl` from a terminal. Replace the node ids below with ones returned by your own run. Coordinates are only examples; any point inside Norwich works because it is snapped to the nearest junction.

### 1. Find junctions and calculate a route

```bash
curl "http://127.0.0.1:8000/nearest-node?latitude=52.6309&longitude=1.2974"
curl "http://127.0.0.1:8000/nearest-node?latitude=52.6200&longitude=1.2700"
curl "http://127.0.0.1:8000/route?start=<START_NODE>&destination=<DESTINATION_NODE>"
```

The route response includes `path` (junction ids), `path_coordinates`, `distance_metres` and `nodes_explored`. Note that `/route` takes the running simulation's traffic and closed roads into account. Before a simulation exists it returns the plain shortest route.

### 2. Run the traffic simulation

```bash
curl -X POST "http://127.0.0.1:8000/simulation/start?start=<START_NODE>&destination=<DESTINATION_NODE>"
curl -X POST "http://127.0.0.1:8000/simulation/step"
curl "http://127.0.0.1:8000/simulation/congestion"
```

Each `step` advances one simulated second and returns every vehicle's position. Call it repeatedly to watch the vehicles move. `/simulation/congestion` lists road segments with their vehicle count and congestion level. The simulation starts with two vehicles: one from start to destination, and one coming back if a return route exists.

### 3. Close a road and watch the reroute

Take two consecutive junction ids from the route's `path`, ahead of the vehicle:

```bash
curl -X POST "http://127.0.0.1:8000/incident/block?start=<NODE_A>&destination=<NODE_B>"
curl "http://127.0.0.1:8000/incidents"
curl "http://127.0.0.1:8000/route?start=<START_NODE>&destination=<DESTINATION_NODE>"
```

The block response includes a `rerouting` summary for the affected vehicles. The second `/route` call now avoids the closed road. The two junctions must be directly connected by a road, otherwise the API returns an error. To reopen it:

```bash
curl -X POST "http://127.0.0.1:8000/incident/unblock?start=<NODE_A>&destination=<NODE_B>"
```

### 4. Ambulance dispatch and rerouting

With a simulation running:

```bash
curl -X POST "http://127.0.0.1:8000/station/create?latitude=52.6309&longitude=1.2974"
curl -X POST "http://127.0.0.1:8000/station/add-vehicle?station_id=0"
curl -X POST "http://127.0.0.1:8000/emergency/create?latitude=52.6200&longitude=1.2700"
```

`/emergency/create` dispatches the nearest available ambulance and returns its route and distance. Step the simulation a few times, then:

```bash
curl "http://127.0.0.1:8000/emergency/status"
curl -X POST "http://127.0.0.1:8000/emergency/block-ahead"
curl -X POST "http://127.0.0.1:8000/simulation/step"
curl "http://127.0.0.1:8000/emergency/status"
```

`block-ahead` closes a road ahead of the ambulance and reroutes it, and returns `distance_before_metres`, `distance_after_metres`, `extra_metres` and `extra_seconds`. It returns an error if there is no ambulance on its way, if the ambulance is too close to its destination, or if every road ahead is the only way there. Keep stepping until `/emergency/status` shows that the ambulance has arrived, with its response time.

### 5. Optional: rank candidate station locations

```bash
curl "http://127.0.0.1:8000/optimiser/stations?candidates=52.63,1.29;52.62,1.31&scenarios=200"
```

Candidates are `latitude,longitude` pairs separated by semicolons (2 to 20). Any roads currently closed in the simulation are avoided. The same optimiser is available from the command line (see the README).
