"""Running scenario studies in the background.

A study can be thousands of full simulations, each taking seconds to a
minute and a half (docs/scenario_benchmark.md), so the API never runs one
inside a request: POST /scenarios/studies saves the study and answers 202
at once, and this JobManager runs it.

How it runs:

* A pool of worker processes (SCENARIO_WORKERS, default min(4, CPUs)).
  Simulation is pure Python and CPU-bound, so threads would share one
  core (the GIL); processes use several. Each worker loads the map and
  builds its World once, when it starts, and keeps it (and its cache of
  free-flow trip times) for every scenario it runs.
* A dispatcher thread hands the pool only as many scenarios as there are
  workers. So a scenario marked "running" in the database really is
  running, and cancelling a study only has to stop handing out its
  remaining scenarios.
* Workers only simulate: they get a spec and return the plain result
  dict (or an error). The API process writes every result to the
  database, one transaction per scenario, so workers never touch it.

workers=0 runs scenarios one at a time on a thread in the API process
instead (no extra processes or memory; for small machines and tests).

Studies left queued or running when the server stopped are marked
interrupted at the next start (ScenarioRepository.interrupt_unfinished),
like live simulation runs.
"""

import logging
import multiprocessing
import pickle
import queue
import threading
import traceback
from concurrent.futures import ProcessPoolExecutor

from backend.db.models import StudyStatus
from backend.db.scenario_repository import ScenarioRepository


logger = logging.getLogger(__name__)


# ------------------------------------------------------------
# In the worker processes
# ------------------------------------------------------------

_world = None


def _init_worker(graph_file, graph_sha256):
    """Load the map once per worker process."""

    global _world

    from backend.scenarios.runner import World

    with open(graph_file, "rb") as file:
        _world = World(pickle.load(file), graph_sha256)


def _run(spec_dict, world=None):
    """Run one scenario; ("ok", result) or ("error", message)."""

    from backend.scenarios.runner import ScenarioError, run_scenario

    try:
        return "ok", run_scenario(world or _world, spec_dict)
    except ScenarioError as error:
        return "error", str(error)
    except Exception:   # report any failure instead of losing the worker
        return "error", traceback.format_exc(limit=3)


# ------------------------------------------------------------
# In the API process
# ------------------------------------------------------------

_STOP = object()


class JobManager:

    def __init__(self, database, graph_file, graph_sha256, workers, world=None):
        """world is only needed for workers=0 (scenarios run in-process)."""

        self.database = database
        self.graph_file = str(graph_file)
        self.graph_sha256 = graph_sha256
        self.workers = workers
        self.world = world

        self._queue = queue.Queue()
        self._slots = threading.Semaphore(max(1, workers))
        self._cancelled = set()
        self._lock = threading.Lock()
        self._executor = None
        self._running = 0
        self._idle = threading.Condition(self._lock)

        self._dispatcher = threading.Thread(
            target=self._dispatch, name="scenario-dispatcher", daemon=True
        )
        self._dispatcher.start()

    # -------- public --------

    def submit_study(self, study_id):
        """Queue every queued scenario of a study (already saved)."""

        with self.database.transaction() as session:
            repo = ScenarioRepository(session)
            scenario_ids = [
                (s.id, s.spec) for s in repo.queued_scenarios(study_id)
            ]

        with self._lock:
            self._cancelled.discard(study_id)
            self._running += len(scenario_ids)

        for scenario_id, spec in scenario_ids:
            self._queue.put((study_id, scenario_id, spec))

        logger.info("Study %s queued: %d scenario(s)", study_id, len(scenario_ids))

    def cancel_study(self, study_id):
        """Stop handing out the study's scenarios and mark it cancelled.
        Scenarios already running finish, but their results are dropped."""

        with self._lock:
            self._cancelled.add(study_id)

        with self.database.transaction() as session:
            return ScenarioRepository(session).end_study(study_id, StudyStatus.CANCELLED)

    def wait_until_idle(self, timeout=None):
        """Block until every queued scenario has finished (for tests and
        the command-line runner). Returns False on timeout."""

        with self._idle:
            return self._idle.wait_for(lambda: self._running == 0, timeout)

    def shutdown(self):
        self._queue.put(_STOP)
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)

    # -------- the dispatcher --------

    def _pool(self):
        if self._executor is None:
            self._executor = ProcessPoolExecutor(
                max_workers=self.workers,
                # "spawn" starts clean processes on every platform (fork
                # would copy the API's threads and open connections)
                mp_context=multiprocessing.get_context("spawn"),
                initializer=_init_worker,
                initargs=(self.graph_file, self.graph_sha256),
            )
        return self._executor

    def _dispatch(self):
        while True:
            item = self._queue.get()
            if item is _STOP:
                return

            study_id, scenario_id, spec = item
            self._slots.acquire()

            with self._lock:
                skip = study_id in self._cancelled

            if skip or not self._start(study_id, scenario_id):
                self._slots.release()
                self._done_one()
                continue

            if self.workers == 0:
                self._finish(scenario_id, _run(spec, self.world))
                self._slots.release()
                self._done_one()
                continue

            future = self._pool().submit(_run, spec)
            future.add_done_callback(
                lambda f, scenario_id=scenario_id: self._collect(scenario_id, f)
            )

    def _start(self, study_id, scenario_id):
        with self.database.transaction() as session:
            repo = ScenarioRepository(session)
            repo.start_study(study_id)
            return repo.start_scenario(scenario_id)

    def _collect(self, scenario_id, future):
        try:
            outcome = future.result()
        except Exception as error:   # the worker process died
            outcome = ("error", f"worker failed: {error!r}")

        try:
            self._finish(scenario_id, outcome)
        finally:
            self._slots.release()
            self._done_one()

    def _finish(self, scenario_id, outcome):
        kind, value = outcome

        try:
            with self.database.transaction() as session:
                repo = ScenarioRepository(session)
                if kind == "ok":
                    repo.save_result(scenario_id, value)
                else:
                    repo.fail_scenario(scenario_id, value)
        except Exception:
            logger.exception("Could not save the result of scenario %s", scenario_id)
            try:
                with self.database.transaction() as session:
                    ScenarioRepository(session).fail_scenario(
                        scenario_id, "the result could not be saved"
                    )
            except Exception:
                logger.exception("Could not mark scenario %s as failed", scenario_id)

    def _done_one(self):
        with self._idle:
            self._running -= 1
            if self._running == 0:
                self._idle.notify_all()
