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

RoadNetwork wraps the Graph once and precomputes what every search needs
(road segment keys, road lengths, which junctions can reach each other), so
a search does no repeated set-up work. That matters when hundreds of
vehicles are re-planning during a simulation.
"""

import heapq
import math


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

        # For each junction: (neighbour, length, segment key) of every road
        # leaving it, so a search needs no lookups per road
        self.adjacent = {node: [] for node in graph.nodes}
        for (a, b), length in self.length.items():
            self.adjacent[a].append((b, length, self.segment[(a, b)]))

        # Flat x/y coordinates in metres for a fast distance estimate that
        # is still a guaranteed lower bound (see _planar_lower_bound)
        self._project()

        self._component = None

        # Landmark distances for the ALT lower bound (see use_landmarks)
        self.landmarks = []
        self._from_landmark = []   # list of {node: metres from landmark}
        self._to_landmark = []     # list of {node: metres to landmark}

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

    def _all_distances(self, source, reverse=False):
        """Dijkstra from source over road lengths (backwards if reverse)."""

        if reverse:
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
        """

        nodes = sorted(self.main_component())
        self.landmarks = []
        self._from_landmark = []
        self._to_landmark = []

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
    ):
        """A* search for the quickest route from start to destination.

        speed           the vehicle's top speed in metres per second
        speed_factor    function(segment key) -> fraction of top speed on
                        that road right now (1.0 = free flowing); None
                        means every road is free flowing
        blocked_segments  closed roads (segment keys) that cannot be used

        Returns (path, seconds, nodes_explored). path is [] and seconds is
        infinity if the destination cannot be reached.

        heuristic       "straight_line" (default) or "landmarks"

        The heuristic is a lower bound on the remaining road distance (the
        straight line on a flat projection, shrunk so it never exceeds the
        true straight line, see _project; or with "landmarks" the tighter
        landmark bound, see use_landmarks) driven at top speed. No road can be driven faster
        than that (speed factors are at most 1) and closures only remove
        roads, so the estimate never overestimates and A* returns the true
        fastest route either way.

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

        if heuristic == "landmarks":
            estimate = self._estimator(destination, speed)
        else:
            xy = self._xy
            target_x, target_y = xy[destination]
            scale = self.PLANAR_SAFETY / speed
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

            for neighbour, length, segment in adjacent[node]:

                if segment in blocked:
                    continue

                if speed_factor is None:
                    cost = seconds + length / speed
                else:
                    factor = speed_factor(segment)

                    if factor <= 0:
                        continue

                    cost = seconds + length / (speed * factor)

                if cost < best.get(neighbour, infinity):
                    best[neighbour] = cost
                    previous[neighbour] = node
                    push(queue, (cost + estimate(neighbour), cost, neighbour))

        return [], math.inf, explored

    def path_seconds(self, path, speed, speed_factor=None):
        """Seconds to drive along a path of junctions (same costs as above)."""

        total = 0.0

        for a, b in zip(path, path[1:]):
            factor = (
                1.0 if speed_factor is None
                else speed_factor(self.segment[(a, b)])
            )

            if factor <= 0:
                return math.inf

            total += self.length[(a, b)] / (speed * factor)

        return total
