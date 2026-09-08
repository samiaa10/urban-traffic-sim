class Graph:
    def __init__(self):
        self.nodes = {}
        self.edges = {}

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