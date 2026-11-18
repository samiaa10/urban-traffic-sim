"""The API documents itself: every operation in /docs is grouped under a
declared tag and says what it does, and the original run paths say which
run they act on."""

import pickle

import pytest

pytest.importorskip("fastapi")

from backend.api.app import API_TAGS, create_app  # noqa: E402
from backend.tests.test_api import make_test_graph  # noqa: E402


@pytest.fixture(scope="module")
def spec(tmp_path_factory):
    path = tmp_path_factory.mktemp("graph") / "graph.pkl"
    with open(path, "wb") as file:
        pickle.dump(make_test_graph(), file)
    return create_app(path).openapi()


def operations(spec):
    return [
        (f"{method.upper()} {path}", operation)
        for path, methods in spec["paths"].items()
        for method, operation in methods.items()
    ]


def test_every_operation_is_grouped_and_described(spec):
    declared = {tag["name"] for tag in API_TAGS}

    for name, operation in operations(spec):
        assert operation.get("tags"), f"{name} has no tag"
        assert set(operation["tags"]) <= declared, name
        assert operation.get("description", "").strip(), f"{name} has no description"

    # Every declared tag is used
    used = {tag for _, operation in operations(spec) for tag in operation["tags"]}
    assert used == declared


def test_original_run_paths_say_which_run_they_act_on(spec):
    step = spec["paths"]["/simulation/step"]["post"]["description"]
    per_run = spec["paths"]["/simulations/{simulation_id}/step"]["post"]["description"]

    assert "default run" in step and "/simulations/{simulation_id}/step" in step
    assert "default run" not in per_run


def test_the_overview_explains_runs_and_limits(spec):
    overview = spec["info"]["description"]

    for words in ("/simulations/{simulation_id}", "429", "DATABASE_URL", "/ready"):
        assert words in overview
