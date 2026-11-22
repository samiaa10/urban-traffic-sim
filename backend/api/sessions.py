"""Live simulation runs, each with its own ID.

Before Stage 3 the API kept one simulation in a global variable, so there
could only ever be one run and every request shared it. Now each run is a
LiveRun (its Simulation, its TrafficManager and a lock) kept in a
RunRegistry under a UUID:

* /simulations/{simulation_id}/... act on one run;
* the original endpoints (/simulation/step, /incident/block, ...) act on
  the *default* run, the one most recently started with /simulation/start.
  Starting a new one that way stops the previous default run, exactly as
  the single global simulation used to be replaced.

Requests are handled on a thread pool, so two requests for the same run
(for example the frontend polling /simulation/step while the user closes a
road) could change the simulation at the same time. Every endpoint that
uses a run holds run.lock while it does; different runs never wait for
each other.

At most `max_live` runs are kept in memory (each can hold thousands of
vehicles). Starting one more stops the run that has been idle longest.
"""

import logging
import threading
import time
import uuid
from datetime import datetime, timezone

from backend.api.recorder import NullRecorder


logger = logging.getLogger(__name__)


class LiveRun:
    """One running simulation and everything needed to drive it."""

    def __init__(self, simulation, traffic, start, destination, run_id=None):
        self.id_uuid = uuid.UUID(run_id) if run_id else uuid.uuid4()
        self.id = str(self.id_uuid)
        self.simulation = simulation
        self.traffic = traffic
        self.start = start
        self.destination = destination

        # Set when the run is a restart of an ended run (its id, a UUID)
        self.restarted_from = None

        # Wall-clock time (UTC) the run was started
        self.created_at = datetime.now(timezone.utc)

        # For choosing which run to stop when there are too many
        self.last_used = time.monotonic()

        # Held while a request reads or changes this run
        self.lock = threading.Lock()

        # Saves what happens to the database (see recorder.py)
        self.recorder = NullRecorder()

        # The emergency response command centre, if one was set up for this
        # run (simulation/command.py)
        self.command = None

    def touch(self):
        self.last_used = time.monotonic()

    def summary(self, is_default=False):
        """What the API reports about a run (caller holds the lock)."""

        vehicles = self.simulation.vehicles

        return {
            "simulation_id": self.id,
            "status": "active",
            "live": True,
            "is_default": is_default,
            # "on", "off" (no database) or "failed" (history has a gap)
            "recording": self.recorder.status,
            "created_at": self.created_at.isoformat(),
            "start": self.start,
            "destination": self.destination,
            "simulation_time": self.simulation.time,
            "vehicles": len(vehicles),
            "vehicles_finished": sum(1 for v in vehicles if v.finished),
            "closed_roads": len(self.simulation.get_blocked_segments()),
            "accidents": len(self.simulation.accidents),
            "emergencies": len(self.simulation.get_emergencies()),
        }


class RunRegistry:
    """The live runs, by ID, plus which one is the default."""

    def __init__(self, max_live=8, idle_seconds=None):
        if max_live < 1:
            raise ValueError("max_live must be at least 1")

        self.max_live = max_live
        # Runs unused for longer than this are stopped when a run is added
        # (None: only when the live-run limit needs the room)
        self.idle_seconds = idle_seconds
        self._runs = {}
        self._default_id = None

        # Guards _runs and _default_id (not the runs themselves)
        self._lock = threading.Lock()

    def __len__(self):
        with self._lock:
            return len(self._runs)

    def add(self, run, make_default=False):
        """Keep `run`; returns (run, reason) for each run this stopped.

        reason is "replaced" (make_default=True stops the previous default
        run: that is how /simulation/start has always behaved) or "stopped"
        (the run idle longest, to stay within max_live).
        """

        stopped = []

        with self._lock:
            if self.idle_seconds:
                now = time.monotonic()
                for idle in [r for r in self._runs.values() if now - r.last_used > self.idle_seconds]:
                    del self._runs[idle.id]
                    if idle.id == self._default_id:
                        self._default_id = None
                    stopped.append((idle, "stopped"))
                    logger.info("Stopped simulation %s: unused for over %d s", idle.id, self.idle_seconds)

            if make_default and self._default_id is not None:
                previous = self._runs.pop(self._default_id, None)
                if previous is not None:
                    stopped.append((previous, "replaced"))

            while len(self._runs) >= self.max_live:
                idle = min(self._runs.values(), key=lambda r: r.last_used)
                del self._runs[idle.id]
                if idle.id == self._default_id:
                    self._default_id = None
                stopped.append((idle, "stopped"))
                logger.info(
                    "Stopped simulation %s to make room (limit %d live runs)",
                    idle.id, self.max_live,
                )

            self._runs[run.id] = run

            if make_default:
                self._default_id = run.id

        return stopped

    def get(self, run_id):
        """The live run with this ID, or None."""

        with self._lock:
            run = self._runs.get(run_id)

        if run is not None:
            run.touch()

        return run

    def default(self):
        """The run the original (unscoped) endpoints act on, or None."""

        with self._lock:
            run = self._runs.get(self._default_id)

        if run is not None:
            run.touch()

        return run

    def is_default(self, run):
        with self._lock:
            return run.id == self._default_id

    def remove(self, run_id):
        """Stop a run; returns it, or None if there was no such run."""

        with self._lock:
            run = self._runs.pop(run_id, None)
            if run_id == self._default_id:
                self._default_id = None

        return run

    def runs(self):
        """Live runs, newest first."""

        with self._lock:
            runs = list(self._runs.values())

        return sorted(runs, key=lambda r: r.created_at, reverse=True)

    def clear(self):
        """Forget every run; returns them."""

        with self._lock:
            runs = list(self._runs.values())
            self._runs.clear()
            self._default_id = None

        return runs
