"""Everything the API builds once at start-up and shares between requests.

The road graph and the structures derived from it never change while the
API runs, so every simulation run shares them. What does change - the
simulations - lives in the RunRegistry (see sessions.py).

The context is stored on app.state, so each FastAPI app made by
create_app (backend/api/app.py) has its own. Tests use that to start a
fresh app, as if the server had been restarted.
"""

import logging
import pickle
import threading
from pathlib import Path

from backend.api.sessions import RunRegistry
from backend.optimiser.station_optimiser import dense_candidates
from backend.routing.travel_time import RoadNetwork
from backend.simulation import rerouting


logger = logging.getLogger(__name__)


class AppContext:

    def __init__(self, graph, max_live_runs=8, graph_file=None):
        self.graph = graph
        self.graph_file = Path(graph_file) if graph_file else None

        # Lets us turn a route point back into its junction id (rerouting)
        self.node_by_point = rerouting.build_node_lookup(graph)

        # Road lengths, segment keys and the fastest-route search
        self.network = RoadNetwork(graph)

        # Candidate stations the optimiser uses when none are given (about
        # 40 well-connected junctions spread over the map)
        self.default_station_candidates = dense_candidates(graph)

        self.registry = RunRegistry(max_live=max_live_runs)

        # The network builds its landmark tables the first time a traffic
        # manager is made, by emptying and refilling them. Making traffic
        # managers one at a time means no other run can plan a route with
        # half-built tables.
        self.network_lock = threading.Lock()


def load_graph(graph_file):
    with open(graph_file, "rb") as file:
        graph = pickle.load(file)

    logger.info(
        "Loaded road graph %s: %d junctions, %d road segments",
        graph_file,
        len(graph.nodes),
        sum(len(edges) for edges in graph.edges.values()),
    )

    return graph
