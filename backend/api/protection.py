"""Protecting a public deployment: rate limits on expensive requests, a
request size limit, one log line per request, and security headers.

All in-process (the backend runs as one process, see sessions.py), so no
extra service is needed:

* Rate limits. Starting runs, adding fleets and surges, the optimiser and
  scenario studies cost real CPU time; each client gets a token bucket of
  `per_minute` such requests (refilled continuously). Over the limit:
  429 with Retry-After. Cheap requests (stepping, status, history) are
  never limited. The client is the connection's address, or, when
  `trust_proxy` is set (behind Render's proxy or the Docker nginx), the
  first address in X-Forwarded-For.
* Request size. Bodies over MAX_BODY_BYTES are refused with 413 (a study
  of 1,000 scenarios is about 0.5 MB).
* Logging. One line per request: method, path, status, milliseconds, and
  the run id when the path names one; the per-second polling requests
  (step, congestion, status) are logged at DEBUG so INFO stays readable.
  LOG_FORMAT=json writes the same as JSON.
* Headers. X-Content-Type-Options, Referrer-Policy and X-Frame-Options on
  every answer (the API serves no pages to frame).
"""

import json
import logging
import math
import re
import threading
import time

from fastapi.responses import JSONResponse


logger = logging.getLogger("backend.requests")

MAX_BODY_BYTES = 2 * 1024 * 1024

# (method, path pattern) of requests that cost real CPU time
EXPENSIVE = [
    ("POST", re.compile(r"^/simulation/start$")),
    ("POST", re.compile(r"^/simulations$")),
    ("POST", re.compile(r"^/simulations/[^/]+/restart$")),
    ("POST", re.compile(r"^(/simulations/[^/]+)?(/simulation)?/fleet$")),
    ("POST", re.compile(r"^(/simulations/[^/]+)?/traffic/surge$")),
    ("GET", re.compile(r"^/optimiser/(stations|station-set)$")),
    ("POST", re.compile(r"^/optimiser/(runs|evaluations)$")),
    ("POST", re.compile(r"^/scenarios/studies(/sweep)?$")),
    ("POST", re.compile(r"^/scenarios/studies/[^/]+/resume$")),
]

# Polled every second by the website: logged at DEBUG
QUIET = re.compile(r"(/step|/congestion|/emergencies/status|/emergency/status|/traffic/status)$")

RUN_ID = re.compile(r"^/simulations/([0-9a-fA-F-]{36})")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}


def is_expensive(method, path):
    return any(method == m and pattern.match(path) for m, pattern in EXPENSIVE)


class RateLimiter:
    """Token buckets per client: `per_minute` tokens, refilled evenly."""

    def __init__(self, per_minute, clock=time.monotonic):
        self.capacity = float(per_minute)
        self.rate = per_minute / 60.0
        self.clock = clock
        self._buckets = {}
        self._lock = threading.Lock()

    def take(self, client):
        """(allowed, seconds until a token is free)."""

        now = self.clock()
        with self._lock:
            tokens, last = self._buckets.get(client, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - last) * self.rate)
            if tokens >= 1:
                self._buckets[client] = (tokens - 1, now)
                # Forget clients whose bucket is full again (bounded memory)
                if len(self._buckets) > 10_000:
                    self._buckets = {
                        key: value for key, value in self._buckets.items()
                        if value[0] + (now - value[1]) * self.rate < self.capacity
                    }
                return True, 0.0
            self._buckets[client] = (tokens, now)
            return False, (1 - tokens) / self.rate


def client_of(request, trust_proxy):
    if trust_proxy:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def install(app, rate_limit_per_minute=0, trust_proxy=False, log_format="text"):
    """Add the protections to `app` (rate limit 0 = off)."""

    limiter = RateLimiter(rate_limit_per_minute) if rate_limit_per_minute > 0 else None

    @app.middleware("http")
    async def protect(request, call_next):
        started = time.perf_counter()
        method, path = request.method, request.url.path

        length = request.headers.get("content-length")
        if length is not None and length.isdigit() and int(length) > MAX_BODY_BYTES:
            response = JSONResponse({"detail": "Request too large"}, status_code=413)
        elif limiter is not None and is_expensive(method, path) and not (
            allowed := limiter.take(client_of(request, trust_proxy))
        )[0]:
            retry = max(1, math.ceil(allowed[1]))
            response = JSONResponse(
                {"detail": f"Too many requests: try again in {retry} s"},
                status_code=429, headers={"Retry-After": str(retry)},
            )
        else:
            response = await call_next(request)

        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)

        elapsed = (time.perf_counter() - started) * 1000
        level = logging.DEBUG if QUIET.search(path) else logging.INFO
        if logger.isEnabledFor(level):
            run = RUN_ID.match(path)
            fields = {
                "method": method, "path": path, "status": response.status_code,
                "ms": round(elapsed, 1), "run": run.group(1) if run else None,
            }
            if log_format == "json":
                logger.log(level, json.dumps(fields))
            else:
                logger.log(
                    level, "%s %s -> %d in %.1f ms%s", method, path, response.status_code,
                    elapsed, f" (run {fields['run']})" if fields["run"] else "",
                )
        return response
