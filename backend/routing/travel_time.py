"""
Travel-time routing
-------------------

a_star.py finds the SHORTEST route (metres, plus a flat penalty per vehicle).
This module finds the FASTEST route: every road costs the seconds it takes
to drive it right now,

    seconds = road length / (vehicle speed x speed factor of that road)

where the speed factor comes from the same function the simulation uses to
move vehicles (traffic and accidents), so the router, the ETA and the
movement all agree on what a road costs.

With speed limits (the osm traffic model, simulation/road_model.py) the
vehicle speed on each road is min(vehicle speed, the road's limit), and
traffic is looked up per direction (directed=True): the speed factor
function is then given the road's key in driving direction,
((lat, lon), (lat, lon)), instead of the sorted segment key.

RoadNetwork wraps the Graph once and precomputes what every search needs
(road segment keys, road lengths, which junctions can reach each other), so
a search does no repeated set-up work. That matters when hundreds of
vehicles are re-planning during a simulation.
"""

import heapq
import math

from backend.routing.road_attributes import default_speed_limit


EARTH_RADIUS_M = 6_371_000


def point_key(latitude, longitude):
    return (latitude, longitude)


class RoadNetwork:
    """The road graph plus lookups that routing and the simulation share."""

    def __init__(self, graph):
        self.graph = graph

        # Coordinates of every junction, and the reverse lookup
        self.position = {
            node: (data["latitude"], data["longitude"])
            for node, data in graph.nodes.items()
        }
        self.node_at = {
            position: node for node, position in self.position.items()
        }

        # Every directed road: (from, to) -> length in metres (the shortest
        # one if OpenStreetMap has two parallel roads between the same
        # junctions), and the undirected segment key the simulation uses
        # for traffic counts, closures and accidents
        self.length = {}
        self.segment = {}

        for node, neighbours in graph.edges.items():
            for neighbour, weight in neighbours:
                key = (node, neighbour)

                if key not in self.length or weight < self.length[key]:
                    self.length[key] = weight

                self.segment[key] = tuple(sorted(
                    (self.position[node], self.position[neighbour])
                ))

        # Lengths keyed by coordinates, which is how vehicles store routes
        self.length_between_points = {
            (self.position[a], self.position[b]): length
            for (a, b), length in self.length.items()
        }

        # Speed limit of every directed road (m/s), from the map's
        # OpenStreetMap attributes; roads the map has none for get the UK
        # built-up default. Only used when a search obeys limits.
        unknown_limit = default_speed_limit("road")
        self.limit = {}
        for a, b in self.length:
            info = graph.road(a, b)
            self.limit[(a, b)] = info.speed_limit if info is not None else unknown_limit
        self.max_limit = max(self.limit.values(), default=unknown_limit)

        # For each junction: (neighbour, length, segment key, directed key,
        # speed limit) of every road leaving it, so a search needs no
        # lookups per road
        self.adjacent = {node: [] for node in graph.nodes}
        for (a, b), length in self.length.items():
            self.adjacent[a].append((
                b,
                length,
                self.segment[(a, b)],
                (self.position[a], self.position[b]),
                self.limit[(a, b)],
            ))

        self._osm_roads = None

        # Flat x/y coordinates in metres for a fast distance estimate that
        # is still a guaranteed lower bound (see _planar_lower_bound)
        self._project()

        self._component = None

        # Landmark distances for the ALT lower bound (see use_landmarks)
        self.landmarks = []
        self._from_landmark = []   # list of {node: metres from landmark}
        self._to_landmark = []     # list of {node: metres to landmark}
        self._seconds_from_landmark = []   # {node: seconds at the limits}
        self._seconds_to_landmark = []

    # --------------------------------------------------------------
    # Fast straight-line lower bound
    # --------------------------------------------------------------

    # Shrink the flat distance by this much so rounding and the curvature
    # of the Earth can never push it above the true distance
    PLANAR_SAFETY = 0.999

    def _project(self):
        """Flat coordinates: y = R * latitude, x = R * longitude * c.

        c is the smallest cos(latitude) on the map. An east-west degree is
        shortest at the highest latitude, so using the smallest cosine
        means east-west distances are never overestimated; with the safety
        factor, the flat distance is a lower bound on the great-circle
        distance for a city-sized map. It needs no trigonometry per call,
        which matters because A* computes it for every junction it reaches.
        """

        if not self.position:
            self._xy = {}
            return

        smallest_cos = min(
            math.cos(math.radians(latitude))
            for latitude, _ in self.position.values()
        )

        self._xy = {
            node: (
                EARTH_RADIUS_M * math.radians(longitude) * smallest_cos,
                EARTH_RADIUS_M * math.radians(latitude),
            )
            for node, (latitude, longitude) in self.position.items()
        }

    def planar_lower_bound(self, a, b):
        (x1, y1), (x2, y2) = self._xy[a], self._xy[b]
        return math.hypot(x2 - x1, y2 - y1) * self.PLANAR_SAFETY

    # --------------------------------------------------------------
    # Landmarks (ALT: A*, Landmarks, Triangle inequality)
    # --------------------------------------------------------------

    def _all_distances(self, source, reverse=False, seconds_at_limit=False):
        """Dijkstra from source over road lengths (backwards if reverse).
        With seconds_at_limit, over each road's time at its speed limit."""

        if seconds_at_limit:
            limit = self.limit
            if reverse:
                incoming = self._incoming()
                neighbours = lambda node: (                     # noqa: E731
                    (n, length / limit[(n, node)]) for n, length in incoming.get(node, ())
                )
            else:
                neighbours = lambda node: (                     # noqa: E731
                    (n, length / limit[(node, n)])
                    for n, length in ((n, self.length[(node, n)]) for n, _ in self.neighbours(node))
                )
        elif reverse:
            incoming = self._incoming()
            neighbours = lambda node: incoming.get(node, ())   # noqa: E731
        else:
            neighbours = lambda node: (                         # noqa: E731
                (n, self.length[(node, n)]) for n, _ in self.neighbours(node)
            )

        best = {source: 0.0}
        queue = [(0.0, source)]

        while queue:
            metres, node = heapq.heappop(queue)

            if metres > best[node]:
                continue

            for neighbour, length in neighbours(node):
                total = metres + length
                if total < best.get(neighbour, math.inf):
                    best[neighbour] = total
                    heapq.heappush(queue, (total, neighbour))

        return best

    def _incoming(self):
        if not hasattr(self, "_incoming_roads"):
            incoming = {}
            for (a, b), length in self.length.items():
                incoming.setdefault(b, []).append((a, length))
            self._incoming_roads = incoming
        return self._incoming_roads

    def use_landmarks(self, count=8):
        """Pick `count` landmarks and precompute distances to and from them.

        For any landmark L and junctions u, v, the triangle inequality gives
        a lower bound on the road distance from u to v:

            d(u, v) >= d(L, v) - d(L, u)     and     d(u, v) >= d(u, L) - d(v, L)

        Landmarks are picked far apart (each one the junction farthest by
        road from those already chosen), which makes the bounds tight for
        most trips. Road lengths never change and congestion only makes
        roads slower, so these free-flow bounds stay valid under any traffic.
        Costs 2 x count Dijkstra searches, once.

        The same landmarks also get travel-time tables: seconds to and from
        them with every road driven at its speed limit. A vehicle that obeys
        limits never covers a road faster than that and traffic only slows
        it, so they give lower bounds in seconds for speed-limited searches
        (see _time_estimator). 2 x count more searches.
        """

        nodes = sorted(self.main_component())
        self.landmarks = []
        self._from_landmark = []
        self._to_landmark = []
        self._seconds_from_landmark = []
        self._seconds_to_landmark = []

        if not nodes or count < 1:
            return

        # Start from the junction farthest from an arbitrary one
        seed_distances = self._all_distances(nodes[0])
        current = max(nodes, key=lambda n: seed_distances.get(n, -1))
        nearest = {node: math.inf for node in nodes}

        for _ in range(min(count, len(nodes))):
            self.landmarks.append(current)
            forward = self._all_distances(current)
            backward = self._all_distances(current, reverse=True)
            self._from_landmark.append(forward)
            self._to_landmark.append(backward)

            self._seconds_from_landmark.append(
                self._all_distances(current, seconds_at_limit=True)
            )
            self._seconds_to_landmark.append(
                self._all_distances(current, reverse=True, seconds_at_limit=True)
            )

            for node in nodes:
                nearest[node] = min(nearest[node], forward.get(node, math.inf))

            current = max(nodes, key=lambda n: nearest[n])

    def lower_bound_metres(self, node, destination):
        """A lower bound on the road distance from node to destination."""

        bound = self.straight_line_metres(node, destination)

        for forward, backward in zip(self._from_landmark, self._to_landmark):
            to_node = forward.get(node)
            to_destination = forward.get(destination)
            if to_node is not None and to_destination is not None:
                bound = max(bound, to_destination - to_node)

            from_node = backward.get(node)
            from_destination = backward.get(destination)
            if from_node is not None and from_destination is not None:
                bound = max(bound, from_node - from_destination)

        return bound

    # How many landmarks a speed-limited search uses: the ones giving the
    # best bound from its start. Checking all 8 at every junction cost more
    # than their extra tightness saved (docs/traffic_benchmark.md).
    ACTIVE_LANDMARKS = 2

    def _landmark_seconds(self, pair, node):
        """Lower bound (seconds at the limits) from node to the pair's
        destination using one landmark's tables."""

        forward, to_destination, backward, from_destination = pair
        bound = 0.0
        if to_destination is not None:
            to_node = forward.get(node)
            if to_node is not None:
                bound = max(bound, to_destination - to_node)
        if from_destination is not None:
            from_node = backward.get(node)
            if from_node is not None:
                bound = max(bound, from_node - from_destination)
        return bound

    def _time_pairs(self, destination):
        return [
            (forward, forward.get(destination), backward, backward.get(destination))
            for forward, backward in zip(self._seconds_from_landmark, self._seconds_to_landmark)
        ]

    def _time_estimator(self, start, destination, speed):
        """Heuristic for a speed-limited search: the larger of the
        straight-line bound (at the fastest limit) and the best
        ACTIVE_LANDMARKS travel-time landmark bounds. Both are lower bounds
        on the time any limit-obeying vehicle needs, under any traffic, so
        A* stays exact."""

        pairs = self._time_pairs(destination)
        pairs.sort(key=lambda pair: -self._landmark_seconds(pair, start))
        chosen = pairs[: self.ACTIVE_LANDMARKS]

        xy = self._xy
        target_x, target_y = xy[destination]
        scale = self.PLANAR_SAFETY / speed
        hypot = math.hypot

        def estimate(node):
            x, y = xy[node]
            bound = hypot(target_x - x, target_y - y) * scale

            for forward, to_destination, backward, from_destination in chosen:
                if to_destination is not None:
                    to_node = forward.get(node)
                    if to_node is not None and to_destination - to_node > bound:
                        bound = to_destination - to_node
                if from_destination is not None:
                    from_node = backward.get(node)
                    if from_node is not None and from_node - from_destination > bound:
                        bound = from_node - from_destination

            return bound

        return estimate

    def lower_bound_seconds(self, node, destination, speed, limits=False):
        """A lower bound on the seconds a vehicle of top speed `speed` needs
        from node to destination under any traffic (obeying limits if
        `limits`): the road-distance bound at its top speed, and with
        limits also every travel-time landmark bound."""

        bound = self.lower_bound_metres(node, destination) / self.top_speed(speed, limits)

        if limits:
            for pair in self._time_pairs(destination):
                bound = max(bound, self._landmark_seconds(pair, node))

        return bound

    def _estimator(self, destination, speed):
        """Fast heuristic function for one search (seconds to destination)."""

        pairs = [
            (forward, forward.get(destination), backward, backward.get(destination))
            for forward, backward in zip(self._from_landmark, self._to_landmark)
        ]
        planar = self.planar_lower_bound

        def estimate(node):
            bound = planar(node, destination)

            for forward, to_destination, backward, from_destination in pairs:
                if to_destination is not None:
                    to_node = forward.get(node)
                    if to_node is not None and to_destination - to_node > bound:
                        bound = to_destination - to_node
                if from_destination is not None:
                    from_node = backward.get(node)
                    if from_node is not None and from_node - from_destination > bound:
                        bound = from_node - from_destination

            return bound / speed

        return estimate

    # --------------------------------------------------------------
    # Lookups
    # --------------------------------------------------------------

    def neighbours(self, node):
        return self.graph.get_neighbours(node)

    def point(self, node):
        latitude, longitude = self.position[node]
        return {"latitude": latitude, "longitude": longitude}

    def points(self, path):
        return [self.point(node) for node in path]

    def path_length(self, path):
        return sum(self.length[(a, b)] for a, b in zip(path, path[1:]))

    def straight_line_metres(self, a, b):
        (lat1, lon1), (lat2, lon2) = self.position[a], self.position[b]
        p1, p2 = math.radians(lat1), math.radians(lat2)
        dp = p2 - p1
        dl = math.radians(lon2 - lon1)
        h = (
            math.sin(dp / 2) ** 2
            + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        )
        return 2 * EARTH_RADIUS_M * math.atan2(math.sqrt(h), math.sqrt(1 - h))

    # --------------------------------------------------------------
    # Which junctions can reach each other
    # --------------------------------------------------------------

    def main_component(self):
        """The largest set of junctions that can all reach one another.

        One-way streets leave small pockets you can drive into but not out
        of (or the reverse). Trips are only generated inside this set, so
        every generated trip has a route. Kosaraju's algorithm, iterative
        so the 5,000-junction map does not hit Python's recursion limit.
        """

        if self._component is not None:
            return self._component

        nodes = list(self.graph.nodes)
        reverse = {node: [] for node in nodes}

        for node, neighbours in self.graph.edges.items():
            for neighbour, _ in neighbours:
                reverse.setdefault(neighbour, []).append(node)

        # Pass 1: order junctions by when their depth-first search finishes
        visited = set()
        order = []

        for root in nodes:
            if root in visited:
                continue

            visited.add(root)
            stack = [(root, iter(self.graph.get_neighbours(root)))]

            while stack:
                node, children = stack[-1]
                advanced = False

                for child, _ in children:
                    if child not in visited:
                        visited.add(child)
                        stack.append(
                            (child, iter(self.graph.get_neighbours(child)))
                        )
                        advanced = True
                        break

                if not advanced:
                    order.append(node)
                    stack.pop()

        # Pass 2: on the reversed roads, in reverse finishing order
        assigned = set()
        best = set()

        for root in reversed(order):
            if root in assigned:
                continue

            component = {root}
            assigned.add(root)
            stack = [root]

            while stack:
                node = stack.pop()

                for parent in reverse.get(node, ()):
                    if parent not in assigned:
                        assigned.add(parent)
                        component.add(parent)
                        stack.append(parent)

            if len(component) > len(best):
                best = component

        self._component = best
        return best

    # --------------------------------------------------------------
    # Fastest route
    # --------------------------------------------------------------

    def fastest_path(
        self,
        start,
        destination,
        speed,
        speed_factor=None,
        blocked_segments=None,
        heuristic="straight_line",
        directed=False,
        limits=False,
    ):
        """A* search for the quickest route from start to destination.

        speed           the vehicle's top speed in metres per second
        speed_factor    function(segment key) -> fraction of top speed on
                        that road right now (1.0 = free flowing); None
                        means every road is free flowing
        blocked_segments  closed roads (segment keys) that cannot be used

        Returns (path, seconds, nodes_explored). path is [] and seconds is
        infinity if the destination cannot be reached.

        heuristic       "straight_line" (default) or "landmarks"; with
                        limits and landmark tables, "straight_line" also
                        uses the best travel-time landmarks
                        (_time_estimator), which are much tighter there
        directed        speed_factor is given each road's key in driving
                        direction instead of its segment key
        limits          obey speed limits: each road is driven at
                        min(speed, its limit)

        The heuristic is a lower bound on the remaining road distance (the
        straight line on a flat projection, shrunk so it never exceeds the
        true straight line, see _project; or with "landmarks" the tighter
        landmark bound, see use_landmarks) driven at top speed. No road can be driven faster
        than that (speed factors are at most 1) and closures only remove
        roads, so the estimate never overestimates and A* returns the true
        fastest route either way. With limits, "top speed" is the lower of
        the vehicle's speed and the highest limit on the network.

        Why straight line by default: on Norwich, landmarks make A* explore
        about a fifth fewer junctions but each estimate checks every
        landmark, which in pure Python costs more than it saves (see
        docs/traffic_benchmark.md). The landmark bound is still used on
        its own, where it is computed once per decision (traffic_manager).
        """

        if not speed > 0:
            raise ValueError("speed must be positive")

        if heuristic not in ("straight_line", "landmarks"):
            raise ValueError("heuristic must be straight_line or landmarks")

        blocked = blocked_segments or ()

        if start == destination:
            return [start], 0.0, 1

        # The fastest any road can be driven, for the estimate
        fastest = min(speed, self.max_limit) if limits else speed

        if heuristic == "landmarks":
            estimate = self._estimator(destination, fastest)
        elif limits and getattr(self, "_seconds_from_landmark", None):
            estimate = self._time_estimator(start, destination, fastest)
        else:
            xy = self._xy
            target_x, target_y = xy[destination]
            scale = self.PLANAR_SAFETY / fastest
            hypot = math.hypot

            def estimate(node):
                x, y = xy[node]
                return hypot(target_x - x, target_y - y) * scale

        adjacent = self.adjacent
        push, pop = heapq.heappush, heapq.heappop
        infinity = math.inf

        best = {start: 0.0}
        previous = {}
        queue = [(estimate(start), 0.0, start)]
        explored = 0

        while queue:
            _, seconds, node = pop(queue)

            if seconds > best.get(node, infinity):
                continue

            explored += 1

            if node == destination:
                path = [node]

                while node in previous:
                    node = previous[node]
                    path.append(node)

                path.reverse()
                return path, seconds, explored

            for neighbour, length, segment, key, limit in adjacent[node]:

                if segment in blocked:
                    continue

                road_speed = limit if limits and limit < speed else speed

                if speed_factor is None:
                    cost = seconds + length / road_speed
                else:
                    factor = speed_factor(key if directed else segment)

                    if factor <= 0:
                        continue

                    cost = seconds + length / (road_speed * factor)

                if cost < best.get(neighbour, infinity):
                    best[neighbour] = cost
                    previous[neighbour] = node
                    push(queue, (cost + estimate(neighbour), cost, neighbour))

        return [], math.inf, explored

    def path_seconds(self, path, speed, speed_factor=None, directed=False, limits=False):
        """Seconds to drive along a path of junctions (same costs as above)."""

        total = 0.0

        for a, b in zip(path, path[1:]):
            if speed_factor is None:
                factor = 1.0
            elif directed:
                factor = speed_factor((self.position[a], self.position[b]))
            else:
                factor = speed_factor(self.segment[(a, b)])

            if factor <= 0:
                return math.inf

            limit = self.limit[(a, b)]
            road_speed = limit if limits and limit < speed else speed

            total += self.length[(a, b)] / (road_speed * factor)

        return total

    def _incoming_adjacent(self):
        """For each junction: the roads INTO it, as adjacent's tuples with
        the road's start junction first (built once)."""

        if getattr(self, "_incoming_adjacency", None) is None:
            incoming = {node: [] for node in self.graph.nodes}
            for node, roads in self.adjacent.items():
                for neighbour, length, segment, key, limit in roads:
                    incoming[neighbour].append((node, length, segment, key, limit))
            self._incoming_adjacency = incoming
        return self._incoming_adjacency

    def fastest_tree_to(
        self, destination, speed, speed_factor=None, blocked_segments=None,
        directed=False, limits=False, targets=None,
    ):
        """Fastest routes from many junctions to ONE destination, with one
        search: Dijkstra backwards from the destination over the same road
        costs as fastest_path. Returns (seconds, next_junction), where
        seconds[n] is the fastest time from n to the destination and
        next_junction[n] the junction to drive to next. With targets, it
        stops once every target junction is settled.

        The traffic manager uses it to re-plan many vehicles heading to the
        same place at once (batched rerouting)."""

        blocked = blocked_segments or ()
        incoming = self._incoming_adjacent()
        push, pop = heapq.heappush, heapq.heappop
        infinity = math.inf

        best = {destination: 0.0}
        next_junction = {}
        settled = set()
        remaining = set(targets) if targets is not None else None
        queue = [(0.0, destination)]

        while queue:
            seconds, node = pop(queue)
            if node in settled:
                continue
            settled.add(node)

            if remaining is not None:
                remaining.discard(node)
                if not remaining:
                    break

            for previous, length, segment, key, limit in incoming[node]:
                if segment in blocked:
                    continue

                road_speed = limit if limits and limit < speed else speed

                if speed_factor is None:
                    cost = seconds + length / road_speed
                else:
                    factor = speed_factor(key if directed else segment)
                    if factor <= 0:
                        continue
                    cost = seconds + length / (road_speed * factor)

                if cost < best.get(previous, infinity):
                    best[previous] = cost
                    next_junction[previous] = node
                    push(queue, (cost, previous))

        return {n: best[n] for n in settled}, next_junction

    def top_speed(self, speed, limits=False):
        """The fastest a vehicle of this speed can drive anywhere."""

        return min(speed, self.max_limit) if limits else speed

    def osm_roads(self):
        """The osm traffic model for this network (built once, shared)."""

        if self._osm_roads is None:
            from backend.simulation.road_model import OsmRoads

            self._osm_roads = OsmRoads(self)

        return self._osm_roads
