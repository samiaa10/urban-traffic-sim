# Demo guide

A short walkthrough of the main features: route calculation, traffic simulation, road closures and ambulance rerouting. You can follow it in the web interface, or reproduce the same steps directly against the API (a good way to see exactly what each action does).

Before you start, complete the setup in the [README](../README.md#getting-started): the Norwich data file must exist, the backend must be running on port 8000, and for the web interface the frontend must be open at `http://localhost:5173`.

## A. Using the web interface

The control names below describe what each action does. If the wording on a button differs slightly, trust what is on screen. The endpoint in brackets is what the action calls.

1. **Calculate a route.** Choose a start and a destination in Norwich and calculate the route. The frontend snaps both points to their nearest junctions (`/nearest-node`) and draws the A\* route (`/route`). Route information is shown. Controls that need a route show the tooltip "Calculate a route first" until you have one.
2. **Start the traffic simulation.** Start the simulation (`/simulation/start`). Vehicles begin driving, and the status indicator changes from "Waiting for input" to "Running", then "Completed" when the vehicle has finished. While it runs, the frontend steps the simulation (`/simulation/step`) and draws the congestion on the roads (`/simulation/congestion`). The status panel also gets a *Traffic* line saying how many vehicles are held up and the average speed as a percentage of free flow.

   **Vehicles slow down on busy roads.** A road with one vehicle is "light" (green) and the vehicle drives at full speed. With two vehicles it is "moderate" (orange) and both drive at 60% speed; with three or more it is "heavy" (red) and they crawl at 30%. The interface only starts two vehicles (plus an ambulance if you raise an emergency), so you will only see this when they share a road: in a check of 40 random journeys on the Norwich map, about half had a moment where the two vehicles shared a road and slowed down. The *Traffic* line then reads "2 of 2 vehicles held up | average speed 60% of free flow" until they pass each other. For heavy traffic across the whole city, run the congestion benchmark (see the README).
3. **Close a road.** With the simulation running, close a road on the route (`/incident/block`). The backend gives a new route to vehicles whose remaining route used that road, and the interface also asks for vehicle 0 to be rerouted (`/simulation/reroute`). The vehicle then drives around the closure.
4. **Dispatch an ambulance.** Create an emergency station and an ambulance (`/station/create`, `/station/add-vehicle`), then raise an emergency at a location (`/emergency/create`). The nearest available ambulance is dispatched and its route is drawn. Emergency status (state, ETA, response time, reroutes) is polled from `/emergency/status`.
5. **Close a road ahead of the ambulance.** While the ambulance is driving, use the control that closes the road ahead (`/emergency/block-ahead`). The ambulance is rerouted around the closure, and the extra distance and time of the detour are reported.

6. **Find the best ambulance station locations.** Click *Find best ambulance station locations* (`/optimiser/stations`). After a couple of seconds the ten best candidates appear as numbered markers on the map (1 is green, the recommended one) with a ranked table underneath showing average response time, worst case, the share of emergencies reached within 8 minutes, and how many emergencies each station could not reach. A few sentences explain why #1 won. Any roads you have closed are avoided, so try it again after closing a road. *Clear results* removes the markers.

7. **Choose several stations at once.** Under the first button, pick a number of stations (2 to 5) and a target time (3, 4, 5 or 8 minutes) and click *Find best set of N stations* (`/optimiser/station-set`). The chosen stations appear as navy markers numbered in the order they add value. Under the map, a list shows how the average response improves as each station is added, followed by a comparison table of the chosen set against three baselines: the best single station, the top-ranked stations picked one by one, and random stations. Columns are average response, worst case, the share of emergencies reached within the target and how many emergencies could not be reached by any chosen station. A few sentences summarise the result, including when a baseline does as well as the chosen set.

   With the default 8 minute target even one station reaches over 99% of emergencies (the model has no traffic and Norwich is compact), so try 3 or 4 minutes to see the stations make a difference. *Clear results* removes both the single-station and the set markers.

Things to watch for: the route changing when a road closes, vehicles slowing on shared roads, the ambulance's response time being measured in *simulated* seconds, and the detour making the response slower.

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

Each `step` advances one simulated second and returns every vehicle's position, `speed_factor` (1.0 = driving freely, 0.6 or 0.3 = held up by traffic) and `journey_seconds` (time on the trip so far, including time waiting). Call it repeatedly to watch the vehicles move. `/simulation/congestion` lists road segments with their vehicle count and congestion level. The simulation starts with two vehicles: one from start to destination, and one coming back if a return route exists.

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
# the default spread of about 38 candidates over the whole map
curl "http://127.0.0.1:8000/optimiser/stations?scenarios=500"

# or your own candidates
curl "http://127.0.0.1:8000/optimiser/stations?candidates=52.63,1.29;52.62,1.31&scenarios=200"
```

Candidates are optional; if you give them they are `latitude,longitude` pairs separated by semicolons (2 to 20). `speed` and `target_seconds` must be above 0, and `scenarios` between 1 and 5000, otherwise the API answers 400. The response contains the ranked results and an `explanation` list in plain English. Any roads currently closed in the simulation are avoided. The same optimiser is available from the command line (see the README).

### 6. Optional: choose several stations together

```bash
# 3 stations from the default candidates, with a 4 minute (240 s) target
curl "http://127.0.0.1:8000/optimiser/station-set?stations=3&scenarios=500&target_seconds=240"
```

The response has `stations` (in the order they were picked, each with the whole set's average after it was added), `result` (average, worst case, `within_target_percent`, `unreachable`), `baselines` (`best_single`, `top_individual`, `random`) and an `explanation` list. `stations` can be 1 to 10; asking for more stations than there are distinct candidate junctions gives a 400 error. The command line does the same: `python -m backend.optimiser.run --stations 3 --target 240`.

### 7. Optional: see a slowdown through the API

Start a simulation, then step it a few times and watch `speed_factor` in each vehicle. It stays at 1.0 until two vehicles are on the same road, when both drop to 0.6. `/simulation/congestion` shows the same road as "moderate".
