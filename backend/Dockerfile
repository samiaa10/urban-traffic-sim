# The NORWICH//SIM backend (FastAPI). Build from the repository root:
#
#     docker build -f backend/Dockerfile -t norwich-sim-backend .
#
# docker-compose.yml does this for you. The same image runs the database
# migrations (command: alembic upgrade head).

# ---- Install the Python packages into a virtual environment ----
FROM python:3.12-slim AS packages

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY requirements-deploy.txt /tmp/requirements-deploy.txt

RUN python -m venv /venv \
    && /venv/bin/pip install -r /tmp/requirements-deploy.txt


# ---- The image that runs ----
FROM python:3.12-slim

ENV PATH=/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Run as an ordinary user, not root
RUN useradd --create-home --uid 10001 app

WORKDIR /app

COPY --from=packages /venv /venv

# The code, the migrations and the Norwich road map (tests, docs and the
# OpenStreetMap download cache are left out by .dockerignore)
COPY alembic.ini ./
COPY backend ./backend
COPY data/osm/norwich_graph.pkl ./data/osm/norwich_graph.pkl

USER app

EXPOSE 8000

# Loading the map takes a few seconds, hence the start period
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/', timeout=2)"

# One worker on purpose: live simulation runs are kept in this process's
# memory, so every request for a run must reach the same process. Saved
# history is in PostgreSQL and is shared by any number of processes.
CMD ["uvicorn", "backend.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
