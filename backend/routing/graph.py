class Graph:
    def __init__(self):
        self.nodes = {}
        self.edges = {}

        # (from_node, to_node) -> RoadInfo (road_attributes.py): the road's
        # class, speed limit, lanes, one-way and name. Empty for maps
        # built without OpenStreetMap attributes (the hand-made test maps,
        # and map files saved before attributes were kept); code that
        # uses it falls back to the behaviour without attributes.
        self.road_info = {}

    def add_node(self, node_id, latitude, longitude):
        self.nodes[node_id] = {
            "latitude": latitude,
            "longitude": longitude
        }

    def add_edge(self, from_node, to_node, weight):
        if from_node not in self.edges:
            self.edges[from_node] = []

        self.edges[from_node].append(
            (to_node, weight)
        )

    def get_neighbours(self, node):
        return self.edges.get(node, [])

    def has_edge(self, from_node, to_node):
        """True if there is a direct road from from_node to to_node."""
        return any(
            neighbour == to_node
            for neighbour, _ in self.get_neighbours(from_node)
        )

    def road(self, from_node, to_node):
        """The RoadInfo of a directed road, or None if not known."""
        return getattr(self, "road_info", {}).get((from_node, to_node))

    def road_name(self, a, b):
        """The name of the road between junctions a and b (either
        direction), or None if the map does not know it."""

        for info in (self.road(a, b), self.road(b, a)):
            if info is not None and info.name:
                return info.name
        return None

    @property
    def has_road_info(self):
        return bool(getattr(self, "road_info", None))
