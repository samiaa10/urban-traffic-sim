"""Everything the API builds once at start-up and shares between requests.

The road graph and the structures derived from it never change while the
API runs, so every simulation run shares them. What does change - the
simulations - lives in the RunRegistry (see sessions.py).

The context is stored on app.state, so each FastAPI app made by
create_app (backend/api/app.py) has its own. Tests use that to start a
fresh app, as if the server had been restarted.
"""

import hashlib
import logging
import pickle
import threading
from pathlib import Path

from backend.api.recorder import METRICS_INTERVAL, RunRecorder
from backend.api.sessions import RunRegistry
from backend.optimiser.station_optimiser import dense_candidates
from backend.routing.travel_time import RoadNetwork
from backend.simulation import rerouting


logger = logging.getLogger(__name__)


class AppContext:

    def __init__(
        self, graph, max_live_runs=8, graph_file=None, database=None,
        metrics_interval=METRICS_INTERVAL, max_study_scenarios=1000,
        traffic_model="simple", run_idle_seconds=None,
    ):
        self.graph = graph

        # Why the database could not be opened at start-up (the API then
        # runs without saving; /ready reports it), or None
        self.database_error = None

        # The traffic model (simulation/road_model.py) new runs and
        # scenarios use when they do not ask for one
        self.traffic_model = traffic_model
        self.graph_file = Path(graph_file) if graph_file else None

        # Saved runs record which map their junction ids belong to
        self.graph_sha256 = graph_fingerprint(graph, self.graph_file)

        # backend.db.database.Database, or None: runs are not saved
        self.database = database
        self.metrics_interval = metrics_interval

        # Scenario studies (Stage 4): the JobManager that runs them (set by
        # create_app when there is a database) and the largest allowed
        self.jobs = None
        self.max_study_scenarios = max_study_scenarios
        self._scenario_world = None
        self._scenario_world_lock = threading.Lock()

        # Lets us turn a route point back into its junction id (rerouting)
        self.node_by_point = rerouting.build_node_lookup(graph)

        # Road lengths, segment keys and the fastest-route search
        self.network = RoadNetwork(graph)

        # The real shape of each road, for drawing vehicles and congestion
        # on the road (routing/road_geometry.py); roads it does not know are
        # drawn straight
        from backend.routing.road_geometry import RoadGeometry, load

        self.road_geometry = load() or RoadGeometry([])

        # Candidate stations the optimiser uses when none are given (about
        # 40 well-connected junctions spread over the map)
        self.default_station_candidates = dense_candidates(graph)

        self.registry = RunRegistry(max_live=max_live_runs, idle_seconds=run_idle_seconds)

        # The network builds its landmark tables the first time a traffic
        # manager is made, by emptying and refilling them. Making traffic
        # managers one at a time means no other run can plan a route with
        # half-built tables.
        self.network_lock = threading.Lock()

    @property
    def no_database_reason(self):
        """Why nothing can be saved, for 503 answers."""

        if self.database_error is not None:
            return "the database could not be reached when the server started"
        return "DATABASE_URL is not set"

    def scenario_world(self):
        """The scenario engine's World for this map (built on first use:
        its own RoadNetwork with landmarks and demand model). Used to check
        scenario specs and, with SCENARIO_WORKERS=0, to run them."""

        with self._scenario_world_lock:
            if self._scenario_world is None:
                from backend.scenarios.runner import World
                self._scenario_world = World(self.graph, self.graph_sha256)
            return self._scenario_world

    def attach_recorder(self, run):
        """Give a new run its recorder and save it. False if the database
        could not save it (the run should then not be started)."""

        if self.database is None:
            return True

        run.recorder = RunRecorder(
            self.database, run, self.node_by_point, self.metrics_interval
        )
        return run.recorder.started(self.graph_sha256)

    def end_run(self, run, reason):
        """A run stopped being live ("stopped", "replaced", "interrupted").

        Waits for any request still using the run, so nothing is recorded
        for it after it ended.
        """

        with run.lock:
            run.recorder.ended(reason)

    def end_all_runs(self, reason="interrupted"):
        for run in self.registry.clear():
            self.end_run(run, reason)


def graph_fingerprint(graph, graph_file=None):
    """SHA-256 of the graph file (or of the pickled graph if there is none)."""

    digest = hashlib.sha256()

    if graph_file is not None and graph_file.exists():
        with open(graph_file, "rb") as file:
            for block in iter(lambda: file.read(1 << 20), b""):
                digest.update(block)
    else:
        digest.update(pickle.dumps(graph))

    return digest.hexdigest()


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
