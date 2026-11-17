"""Settings for the whole backend test suite.

The server's default traffic model is "osm" (TRAFFIC_MODEL, see
backend/api/settings.py). Most tests were written for the original rules
and load the server module (backend.api.main), so the suite runs it with
the simple model unless TRAFFIC_MODEL is set; tests of the osm model ask
for it explicitly (test_road_model.py, and traffic_model=osm requests).
"""

import os

os.environ.setdefault("TRAFFIC_MODEL", "simple")
