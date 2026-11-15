import pytest

from backend.api.settings import DEFAULT_ORIGINS, allowed_origins, log_level


def test_only_the_local_dev_server_is_allowed_by_default():
    assert allowed_origins(None) == ["http://localhost:5173"]
    assert allowed_origins("") == ["http://localhost:5173"]
    assert allowed_origins("   ") == ["http://localhost:5173"]


def test_default_list_cannot_be_changed_by_a_caller():
    allowed_origins(None).append("https://evil.example")

    assert DEFAULT_ORIGINS == ["http://localhost:5173"]


def test_a_deployed_frontend_origin_is_used_instead_of_the_default():
    assert allowed_origins("https://norwich-sim.onrender.com") == [
        "https://norwich-sim.onrender.com"
    ]


def test_several_origins_can_be_listed_with_spaces_around_them():
    value = " https://norwich-sim.onrender.com , http://localhost:5173 "

    assert allowed_origins(value) == [
        "https://norwich-sim.onrender.com",
        "http://localhost:5173",
    ]


def test_trailing_slash_is_removed_because_browsers_never_send_one():
    assert allowed_origins("https://norwich-sim.onrender.com/") == [
        "https://norwich-sim.onrender.com"
    ]


def test_duplicates_and_empty_items_are_dropped():
    value = "https://a.example,,https://a.example/,"

    assert allowed_origins(value) == ["https://a.example"]


@pytest.mark.parametrize("bad", ["*", "https://*.onrender.com", "https://a.example,*"])
def test_wildcards_are_refused(bad):
    with pytest.raises(ValueError):
        allowed_origins(bad)


@pytest.mark.parametrize("bad", ["norwich-sim.onrender.com", "ftp://a.example"])
def test_origins_must_include_http_or_https(bad):
    with pytest.raises(ValueError):
        allowed_origins(bad)


def test_log_level_defaults_to_info():
    assert log_level(None) == "INFO"
    assert log_level("") == "INFO"
    assert log_level("   ") == "INFO"


@pytest.mark.parametrize(
    "value, expected",
    [("DEBUG", "DEBUG"), ("debug", "DEBUG"), (" warning ", "WARNING"), ("Error", "ERROR")],
)
def test_log_level_accepts_standard_names_in_any_case(value, expected):
    assert log_level(value) == expected


@pytest.mark.parametrize("bad", ["verbose", "INFOO", "10"])
def test_unknown_log_levels_are_refused(bad):
    with pytest.raises(ValueError):
        log_level(bad)


# ------------------------------------------------------------
# MAX_LIVE_RUNS
# ------------------------------------------------------------

from backend.api.settings import max_live_runs  # noqa: E402


def test_eight_live_runs_are_allowed_by_default():
    assert max_live_runs(None) == 8
    assert max_live_runs("  ") == 8


def test_live_run_limit_can_be_set():
    assert max_live_runs("3") == 3
    assert max_live_runs(" 20 ") == 20


@pytest.mark.parametrize("value", ["0", "101", "-1", "two", "2.5"])
def test_a_bad_live_run_limit_fails_at_start_up(value):
    with pytest.raises(ValueError, match="MAX_LIVE_RUNS"):
        max_live_runs(value)
