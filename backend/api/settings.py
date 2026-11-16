"""Settings read from environment variables (kept free of FastAPI so they
can be tested on their own)."""

# What the API allowed before CORS_ORIGINS existed: the Vite dev server.
DEFAULT_ORIGINS = ["http://localhost:5173"]

# Log levels LOG_LEVEL may be set to (Python's standard level names)
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
DEFAULT_LOG_LEVEL = "INFO"


def log_level(value):
    """Turn the LOG_LEVEL environment variable into a logging level name.

    Not set (or blank) means INFO. Case does not matter ("debug" works).
    Anything else is refused, so a typo fails loudly at start-up instead
    of silently hiding the logs.
    """

    if value is None or not value.strip():
        return DEFAULT_LOG_LEVEL

    level = value.strip().upper()

    if level not in LOG_LEVELS:
        raise ValueError(
            "LOG_LEVEL must be one of " + ", ".join(LOG_LEVELS) + ": " + value
        )

    return level


def allowed_origins(value):
    """Turn the CORS_ORIGINS environment variable into a list of origins.

    CORS_ORIGINS is a comma-separated list of the websites that may call
    this API from a browser, for example
    "https://norwich-sim.onrender.com". If it is not set, only the local
    development server is allowed.

    An origin is scheme + host (+ port), with no path, so a trailing
    slash is removed. "*" (everyone) is refused: the point of this setting
    is to say exactly which site may use the API.
    """

    if value is None or not value.strip():
        return list(DEFAULT_ORIGINS)

    origins = []

    for item in value.split(","):
        origin = item.strip().rstrip("/")

        if not origin:
            continue

        if "*" in origin:
            raise ValueError(
                "CORS_ORIGINS must list exact origins, not '*': " + origin
            )

        if not origin.startswith(("http://", "https://")):
            raise ValueError(
                "CORS_ORIGINS entries must start with http:// or https://: "
                + origin
            )

        if origin not in origins:
            origins.append(origin)

    return origins or list(DEFAULT_ORIGINS)


# How many simulation runs may be live (in memory) at once
DEFAULT_MAX_LIVE_RUNS = 8


def max_live_runs(value):
    """Turn the MAX_LIVE_RUNS environment variable into a whole number.

    Not set (or blank) means 8. Each live run can hold thousands of
    vehicles, so this caps the memory the API can use; starting one more
    stops the run that has been idle longest. Anything that is not a whole
    number from 1 to 100 is refused at start-up.
    """

    if value is None or not value.strip():
        return DEFAULT_MAX_LIVE_RUNS

    try:
        count = int(value.strip())
    except ValueError:
        raise ValueError("MAX_LIVE_RUNS must be a whole number: " + value)

    if not 1 <= count <= 100:
        raise ValueError("MAX_LIVE_RUNS must be between 1 and 100: " + value)

    return count


def database_url(value):
    """Turn the DATABASE_URL environment variable into a SQLAlchemy URL.

    Not set (or blank) means None: no database, runs live in memory only
    (as before Stage 3). PostgreSQL is the only database supported.
    Hosting providers hand out postgres://... or postgresql://... URLs;
    both are pointed at the psycopg 3 driver SQLAlchemy needs.
    """

    if value is None or not value.strip():
        return None

    url = value.strip()

    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]

    if url.startswith("postgresql+psycopg://"):
        return url

    raise ValueError(
        "DATABASE_URL must be a PostgreSQL URL (postgresql://...): "
        + url.split("://")[0] + "://..."
    )


def scenario_workers(value, cpu_count=None):
    """Turn SCENARIO_WORKERS into how many processes run scenarios.

    Not set: min(4, number of CPUs). 0 runs scenarios one at a time on a
    thread in the API process (no extra processes: for small machines and
    tests). Each worker process holds its own copy of the map (about
    80-100 MB on Norwich, see docs/scenario_benchmark.md).
    """

    import os

    if value is None or not value.strip():
        return min(4, cpu_count or os.cpu_count() or 1)

    try:
        workers = int(value.strip())
    except ValueError:
        raise ValueError("SCENARIO_WORKERS must be a whole number: " + value)

    if not 0 <= workers <= 64:
        raise ValueError("SCENARIO_WORKERS must be between 0 and 64: " + value)

    return workers


def resume_studies(value):
    """Turn RESUME_STUDIES into whether scenario studies interrupted by a
    restart are queued again at start-up. Not set: yes."""

    if value is None or not value.strip():
        return True

    text = value.strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False

    raise ValueError("RESUME_STUDIES must be true or false: " + value)


DEFAULT_MAX_STUDY_SCENARIOS = 1000


def max_study_scenarios(value):
    """Turn MAX_STUDY_SCENARIOS into the most scenarios one study may have
    (default 1000; the database allows up to 5000)."""

    if value is None or not value.strip():
        return DEFAULT_MAX_STUDY_SCENARIOS

    try:
        count = int(value.strip())
    except ValueError:
        raise ValueError("MAX_STUDY_SCENARIOS must be a whole number: " + value)

    if not 1 <= count <= 5000:
        raise ValueError("MAX_STUDY_SCENARIOS must be between 1 and 5000: " + value)

    return count
