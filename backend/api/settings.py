"""Settings read from environment variables (kept free of FastAPI so they
can be tested on their own)."""

# What the API allowed before CORS_ORIGINS existed: the Vite dev server.
DEFAULT_ORIGINS = ["http://localhost:5173"]


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
