"""The AI layer (backend/intelligence/): providers, the read-only tools and
their validation, the analyst's tool loop and its rule fallback, the
congestion anomaly detector, and scenarios from ordinary language. No test
calls a real AI provider: a scripted stand-in plays the model."""

import pickle
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.intelligence import analysis, analyst, providers, scenarios
from backend.intelligence.providers import (
    AnthropicProvider,
    NoProvider,
    Provider,
    ProviderError,
    RateLimiter,
    Reply,
    ToolCall,
)
from backend.intelligence.tools import TOOLS, ToolError, Toolbox, validate
from backend.routing.travel_time import RoadNetwork


GRAPH_FILE = Path(__file__).resolve().parents[2] / "data" / "osm" / "norwich_graph.pkl"


@pytest.fixture(scope="module")
def norwich():
    with open(GRAPH_FILE, "rb") as file:
        graph = pickle.load(file)
    from backend.optimiser.planning import _SEARCHES

    network = RoadNetwork(graph)
    yield SimpleNamespace(graph=graph, network=network, registry=FakeRegistry(), database=None,
                          node_by_point=__import__("backend.simulation.rerouting", fromlist=["x"]).build_node_lookup(graph))
    _SEARCHES.clear()


class FakeRegistry:
    def __init__(self, runs=None):
        self.runs = runs or {}

    def get(self, run_id):
        return self.runs.get(run_id)

    def default(self):
        return None

    def __len__(self):
        return len(self.runs)


class ScriptedProvider(Provider):
    """Plays the model: answers each call with the next scripted Reply."""

    name = "scripted"
    model = "scripted-model"
    available = True
    reason = None

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def respond(self, system, messages, tools):
        self.calls.append({"system": system, "messages": [dict(m) for m in messages], "tools": tools})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def text_reply(text):
    return Reply(text, [], "end_turn", [{"type": "text", "text": text}])


def tool_reply(name, arguments, ident="call-1"):
    return Reply("", [ToolCall(ident, name, arguments)], "tool_use",
                 [{"type": "tool_use", "id": ident, "name": name, "input": arguments}])


# ------------------------------------------------------------
# Providers
# ------------------------------------------------------------

def test_the_rate_limiter_allows_so_many_a_minute():
    now = [0.0]
    limiter = RateLimiter(2, clock=lambda: now[0])
    limiter.acquire()
    limiter.acquire()
    with pytest.raises(ProviderError) as error:
        limiter.acquire()
    assert error.value.retryable
    now[0] = 61
    limiter.acquire()


def test_the_provider_comes_from_the_environment(monkeypatch):
    for name in ("AI_PROVIDER", "ANTHROPIC_API_KEY", "AI_MODEL"):
        monkeypatch.delenv(name, raising=False)
    assert isinstance(providers._environment_provider(), NoProvider)
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    assert "ANTHROPIC_API_KEY is not set" in providers._environment_provider().reason
    monkeypatch.setenv("AI_PROVIDER", "magic")
    assert "Unknown AI_PROVIDER" in providers._environment_provider().reason
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setenv("AI_MODEL", "claude-sonnet-5-5")
    provider = providers._environment_provider()
    assert isinstance(provider, AnthropicProvider)
    assert provider.status() == {"provider": "anthropic", "model": "claude-sonnet-5-5", "available": True, "reason": None}
    monkeypatch.delenv("AI_PROVIDER")
    assert providers._environment_provider().model == "claude-sonnet-5-5"   # a key alone selects Anthropic


class FakeMessages:
    def __init__(self, response):
        self.response = response
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def fake_client(response):
    messages = FakeMessages(response)
    return SimpleNamespace(beta=SimpleNamespace(messages=messages)), messages


def test_the_anthropic_provider_sends_tools_and_reads_tool_calls():
    response = SimpleNamespace(
        stop_reason="tool_use",
        content=[SimpleNamespace(type="text", text="Checking."),
                 SimpleNamespace(type="tool_use", id="t1", name="critical_roads", input={})],
        usage=SimpleNamespace(input_tokens=120, output_tokens=30),
    )
    client, messages = fake_client(response)
    provider = AnthropicProvider("key", client=client)
    reply = provider.respond("system", [{"role": "user", "content": "hi"}], [{"name": "critical_roads"}])
    assert reply.text == "Checking." and reply.tool_calls == [ToolCall("t1", "critical_roads", {})]
    assert reply.usage == {"input_tokens": 120, "output_tokens": 30}
    sent = messages.kwargs
    assert sent["model"] == "claude-opus-5-5" and sent["tools"] == [{"name": "critical_roads"}]
    assert sent["output_config"] == {"effort": "medium"} and sent["fallbacks"] == "default"


def test_a_refusal_is_an_error_not_an_answer():
    client, _ = fake_client(SimpleNamespace(stop_reason="refusal", content=[], usage=None))
    with pytest.raises(ProviderError, match="declined"):
        AnthropicProvider("key", client=client).respond("s", [], [])


# ------------------------------------------------------------
# Tools
# ------------------------------------------------------------

def test_every_tool_schema_is_closed_and_lists_its_arguments():
    for tool in TOOLS:
        schema = tool["input_schema"]
        assert schema["additionalProperties"] is False
        assert sorted(schema["required"]) == sorted(schema["properties"])


def test_tool_arguments_are_checked_before_anything_runs(norwich):
    schema = next(t for t in TOOLS if t["name"] == "coverage_gaps")["input_schema"]
    validate({"service": "fire", "target_minutes": 4, "disruption": "none"}, schema)
    for bad in ({"service": "lifeboat", "target_minutes": 4, "disruption": "none"},
                {"service": "fire", "target_minutes": 400, "disruption": "none"},
                {"service": "fire", "target_minutes": 4, "disruption": "none", "sql": "DROP TABLE"},
                {"service": "fire", "disruption": "none"}):
        with pytest.raises(ToolError):
            validate(bad, schema)
    toolbox = Toolbox(norwich)
    with pytest.raises(ToolError, match="no tool called"):
        toolbox.run("run_python", {"code": "import os"})
    with pytest.raises(ToolError, match="No live simulation"):
        toolbox.run("run_report", {"simulation_id": None})


# ------------------------------------------------------------
# The analyst
# ------------------------------------------------------------

def test_the_model_asks_for_calculations_and_answers_from_them(norwich):
    provider = ScriptedProvider([
        tool_reply("coverage_gaps", {"service": "fire", "target_minutes": 4, "disruption": "none"}),
        text_reply("About 91.6% of residents are reached within 4 minutes."),
    ])
    out = analyst.answer("Where is fire cover worst?", norwich, provider=provider)
    assert out["mode"] == "ai" and out["model"] == "scripted-model" and out["notice"] is None
    assert [s["tool"] for s in out["sources"]] == ["coverage_gaps"]
    assert out["sources"][0]["provenance"] == "calculated"
    assert round(out["sources"][0]["result"]["summary"]["population_within_target_percent"], 1) == 91.6
    # The tool result went back to the model in the same conversation
    second = provider.calls[1]["messages"]
    assert second[-1]["content"][0]["type"] == "tool_result" and second[-1]["content"][0]["tool_use_id"] == "call-1"
    assert "Never invent figures" in provider.calls[0]["system"]


def test_a_bad_tool_call_goes_back_to_the_model_as_an_error(norwich):
    provider = ScriptedProvider([
        tool_reply("coverage_gaps", {"service": "lifeboat", "target_minutes": None, "disruption": "none"}),
        text_reply("That service does not exist."),
    ])
    out = analyst.answer("Lifeboat cover?", norwich, provider=provider)
    result = provider.calls[1]["messages"][-1]["content"][0]
    assert result["is_error"] is True and "service must be one of" in result["content"]
    # No calculation succeeded, so the model's answer is not passed on
    assert out["mode"] == "rules" and "without running any calculation" in out["notice"]


def test_the_rules_answer_when_the_provider_fails(norwich):
    provider = ScriptedProvider([ProviderError("The AI provider could not be reached", retryable=True)])
    out = analyst.answer("What would happen if the central bridges closed?", norwich, provider=provider)
    assert out["mode"] == "rules" and "could not be reached" in out["notice"]
    assert out["sources"][0]["tool"] == "closure_impact"
    assert out["answer"].startswith("Closing Duke Street, Fye Bridge Street, Prince of Wales Road, Whitefriars")
    out = analyst.answer("Hello", norwich, provider=NoProvider())
    assert out["mode"] == "rules" and "No AI model is configured" in out["notice"]
    assert "403.4 km of roads" in out["answer"]


@pytest.mark.parametrize("question, tool", [
    ("Which areas have the worst emergency response coverage?", "coverage_gaps"),
    ("Where are the biggest traffic bottlenecks?", "critical_roads"),
    ("What would happen if this bridge closed?", "closure_impact"),
    ("Which emergency station locations would improve coverage?", "recommend_station"),
    ("Why did this simulation perform poorly?", "run_report"),
    ("Which scenario produced the best results?", "scenario_results"),
])
def test_the_rules_route_each_kind_of_question(norwich, question, tool):
    assert analyst.choose_tool(question, norwich.graph, has_run=False)[0] == tool


def test_the_rules_find_roads_named_in_a_question(norwich):
    name, arguments = analyst.choose_tool("What if Dereham Road closed?", norwich.graph, False)
    assert name == "closure_impact" and arguments["roads"] == ["Dereham Road"]
    assert analyst.choose_tool("ambulance coverage within 7 minutes", norwich.graph, False)[1] == \
        {"service": "ambulance", "target_minutes": 7.0, "disruption": "none"}


def test_questions_are_checked(norwich):
    with pytest.raises(ValueError):
        analyst.answer("   ", norwich, provider=NoProvider())
    with pytest.raises(ValueError):
        analyst.answer("x" * 1001, norwich, provider=NoProvider())


# ------------------------------------------------------------
# Analyses
# ------------------------------------------------------------

def test_the_monitor_flags_a_road_far_above_its_own_traffic():
    monitor = analysis.CongestionMonitor(warmup=60, every=5)
    quiet, busy = ("a", "b"), ("c", "d")
    for t in range(0, 300, 5):
        monitor.observe(t, {quiet: 3 + (t // 5) % 2, busy: 2})
    assert monitor.current() == []
    monitor.observe(300, {quiet: 4, busy: 18})
    flagged = monitor.current()
    assert len(flagged) == 1 and flagged[0]["vehicles"] == 18 and flagged[0]["usual"] < 3
    assert monitor.events[-1]["count"] == 18
    # Back to normal: no longer flagged
    for t in range(305, 600, 5):
        monitor.observe(t, {quiet: 3, busy: 2})
    assert monitor.current() == []
    # Times between samples are ignored
    monitor.observe(601, {busy: 50})
    assert monitor.current() == []


def test_a_station_recommendation_beats_what_is_there(norwich):
    result = analysis.recommend_station(norwich.graph, norwich.network, "fire", 4, top=2)
    best = result["best"][0]
    assert best["population_within_target_percent"] > result["before"]["population_within_target_percent"]
    assert best["residents_brought_within_target"] > 0
    assert result["candidates_tried"] == 38


def test_closing_an_unknown_road_is_refused(norwich):
    with pytest.raises(ValueError, match="no road on the map"):
        analysis.closure_impact(norwich.graph, norwich.network, roads=["Atlantis Avenue"])


# ------------------------------------------------------------
# Scenarios from ordinary language
# ------------------------------------------------------------

@pytest.mark.parametrize("text, types", [
    ("Simulate a major flood in Norwich city centre.", ["traffic", "flood"]),
    ("Create a rush-hour traffic surge.", ["traffic", "surge"]),
    ("Close a major bridge and measure the consequences.", ["traffic", "close_bridges"]),
    ("Simulate multiple simultaneous emergency incidents.", ["traffic", "incidents"]),
    ("Close Dereham Road with 1500 vehicles", ["traffic", "close_roads"]),
])
def test_the_rules_turn_requests_into_supported_actions(norwich, text, types):
    raw = scenarios.rule_plan(text, norwich.graph)
    checked = scenarios.validate(raw, norwich.graph, norwich.network)
    assert [a["type"] for a in checked["actions"]] == types


def test_the_rules_say_when_nothing_can_be_simulated(norwich):
    with pytest.raises(scenarios.ScenarioError, match="nothing they can simulate"):
        scenarios.rule_plan("Simulate an earthquake", norwich.graph)


def test_a_proposal_is_checked_against_what_the_engine_supports(norwich):
    graph, network = norwich.graph, norwich.network
    for bad, message in [
        ({"actions": [{"type": "earthquake"}]}, "unsupported action"),
        ({"actions": [{"type": "flood", "place": "Atlantis"}]}, "unknown place"),
        ({"actions": [{"type": "surge", "vehicles_per_minute": 5000}]}, "vehicles per minute"),
        ({"actions": [{"type": "close_roads", "roads": ["Atlantis Avenue"]}]}, "no road on the map"),
        ({"actions": [{"type": "incidents", "incidents": [{"kind": "alien", "place": "Eaton"}]}]}, "incident kinds"),
        ({"actions": []}, "between 1 and 8"),
    ]:
        with pytest.raises(scenarios.ScenarioError, match=message):
            scenarios.validate(bad, graph, network)
    flood = scenarios.validate({"actions": [{"type": "flood", "place": "the city centre", "radius_metres": 300}]}, graph, network)
    assert flood["actions"][0]["type"] == "traffic"   # background traffic added
    assert flood["actions"][1]["roads"] > 10


def test_the_model_proposes_only_through_the_tool(norwich):
    proposal = {"title": "Bridge closure", "notes": "", "actions": [{"type": "close_bridges"}]}
    provider = ScriptedProvider([tool_reply("propose_scenario", proposal)])
    out = scenarios.plan("Shut the bridges", norwich, provider=provider)
    assert out["mode"] == "ai" and out["summary"][-1].startswith("Close the central bridges")
    tools = provider.calls[0]["tools"]
    assert [t["name"] for t in tools] == ["propose_scenario"]
    places = tools[0]["input_schema"]["properties"]["actions"]["items"]["properties"]["place"]["enum"]
    assert "the city centre" in places
    # A model that declines in words: the rules read the request, and say
    # what they cannot simulate
    with pytest.raises(scenarios.ScenarioError, match="nothing they can simulate"):
        scenarios.plan("Earthquake", norwich, provider=ScriptedProvider([text_reply("I cannot simulate earthquakes.")]))
    # An invented place from a model is refused; the rules read the request instead
    bad = {"title": "x", "notes": "", "actions": [{"type": "flood", "place": "Narnia"}]}
    out = scenarios.plan("flood in the city centre", norwich, provider=ScriptedProvider([tool_reply("propose_scenario", bad)]))
    assert out["mode"] == "rules" and "not valid (unknown place 'Narnia')" in out["notice"]
    assert [a["type"] for a in out["proposal"]["actions"]] == ["traffic", "flood"]


# ------------------------------------------------------------
# A free local model (Ollama)
# ------------------------------------------------------------

from backend.intelligence.providers import OllamaProvider  # noqa: E402


class FakeOllama:
    """Plays Ollama's /api/chat: records each request, answers in turn."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.requests = []

    def __call__(self, url, body, timeout):
        self.requests.append({"url": url, "body": body, "timeout": timeout})
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def ollama_says(content="", calls=()):
    return {"message": {"role": "assistant", "content": content,
                        "tool_calls": [{"function": {"name": n, "arguments": a}} for n, a in calls]},
            "done_reason": "stop", "prompt_eval_count": 300, "eval_count": 40}


def test_ollama_is_chosen_from_the_environment(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.delenv("AI_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    provider = providers._environment_provider()
    assert isinstance(provider, OllamaProvider)
    assert (provider.base_url, provider.model, provider.timeout) == ("http://localhost:11434", "llama3.2:3b", 120.0)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://studio.local:11434/")
    monkeypatch.setenv("AI_MODEL", "qwen3:4b")
    provider = providers._environment_provider()
    assert (provider.base_url, provider.model) == ("http://studio.local:11434", "qwen3:4b")
    assert provider.status()["provider"] == "ollama"


def test_ollama_gets_the_tools_and_the_conversation_in_its_own_format():
    fake = FakeOllama([ollama_says(calls=[("critical_roads", {})])])
    provider = OllamaProvider(post=fake)
    reply = provider.respond("Be careful.", [{"role": "user", "content": "Which roads matter?"}], TOOLS)
    sent = fake.requests[0]
    assert sent["url"] == "http://localhost:11434/api/chat" and sent["body"]["stream"] is False
    assert sent["body"]["messages"] == [{"role": "system", "content": "Be careful."},
                                        {"role": "user", "content": "Which roads matter?"}]
    tool = sent["body"]["tools"][0]
    assert tool["type"] == "function" and tool["function"]["name"] == TOOLS[0]["name"]
    assert tool["function"]["parameters"] == TOOLS[0]["input_schema"]
    assert reply.stop_reason == "tool_use" and reply.tool_calls[0].name == "critical_roads"
    assert reply.usage == {"input_tokens": 300, "output_tokens": 40}


def test_tool_results_go_back_to_ollama_as_tool_messages():
    assistant = [{"type": "text", "text": "Let me check."},
                 {"type": "tool_use", "id": "ollama-1-0", "name": "coverage_gaps", "input": {"service": "fire"}}]
    results = [{"type": "tool_result", "tool_use_id": "ollama-1-0", "content": '{"x": 1}'},
               {"type": "tool_result", "tool_use_id": "ollama-1-0", "content": "bad service", "is_error": True}]
    converted = OllamaProvider.to_ollama("s", [
        {"role": "user", "content": "q"}, {"role": "assistant", "content": assistant}, {"role": "user", "content": results},
    ])
    assert converted[2] == {"role": "assistant", "content": "Let me check.",
                            "tool_calls": [{"function": {"name": "coverage_gaps", "arguments": {"service": "fire"}}}]}
    assert converted[3] == {"role": "tool", "content": '{"x": 1}', "tool_name": "coverage_gaps"}
    assert converted[4]["content"] == "Error: bad service"


def test_arguments_sent_as_text_are_read_and_unreadable_ones_are_caught(norwich):
    fake = FakeOllama([
        {"message": {"content": "", "tool_calls": [{"function": {"name": "coverage_gaps",
            "arguments": '{"service": "police", "target_minutes": 4, "disruption": "none"}'}}]}},
        {"message": {"content": "", "tool_calls": [{"function": {"name": "coverage_gaps", "arguments": "{oops"}}]}},
        ollama_says("Police cover is good."),
    ])
    out = analyst.answer("Police cover?", norwich, provider=OllamaProvider(post=fake))
    assert out["mode"] == "ai" and out["model"] == "llama3.2:3b"
    assert [s["input"]["service"] for s in out["sources"]] == ["police"]
    # The unreadable call was refused by validation and the model told so
    refused = fake.requests[2]["body"]["messages"][-1]
    assert refused["role"] == "tool" and refused["content"].startswith("Error: unknown argument")


def test_without_ollama_running_the_rules_answer(norwich):
    import urllib.error

    down = OllamaProvider(post=FakeOllama([urllib.error.URLError("connection refused")]))
    out = analyst.answer("Which roads do trips depend on most?", norwich, provider=down)
    assert out["mode"] == "rules"
    assert "Ollama is not running at http://localhost:11434" in out["notice"]
    missing = OllamaProvider(post=FakeOllama([urllib.error.HTTPError("u", 404, "not found", {}, None)]))
    with pytest.raises(ProviderError, match="ollama pull llama3.2:3b"):
        missing.respond("s", [{"role": "user", "content": "q"}], [])
    slow = OllamaProvider(post=FakeOllama([TimeoutError()]))
    with pytest.raises(ProviderError, match="took too long"):
        slow.respond("s", [{"role": "user", "content": "q"}], [])
    with pytest.raises(ProviderError, match="Ollama: model is loading"):
        OllamaProvider(post=FakeOllama([{"error": "model is loading"}])).respond("s", [], [])


def test_a_local_model_proposes_scenarios_through_the_same_checks(norwich):
    proposal = {"title": "Bridges", "notes": "", "actions": [{"type": "close_bridges"}]}
    fake = FakeOllama([ollama_says(calls=[("propose_scenario", proposal)])])
    out = scenarios.plan("Shut the bridges", norwich, provider=OllamaProvider(post=fake))
    assert out["mode"] == "ai" and out["model"] == "llama3.2:3b"
    assert out["summary"][-1].startswith("Close the central bridges")
    # A small model that writes its "tool call" as text gives no proposal: the rules read it
    garbled = '{"name": "propose_scenario", "parameters": {"actions": "[{\\"type\\": \\"close_bridges\\"}"}}'
    out = scenarios.plan("Close a major bridge", norwich, provider=OllamaProvider(post=FakeOllama([ollama_says(garbled)])))
    assert out["mode"] == "rules" and "gave no proposal" in out["notice"]
    assert out["summary"][-1].startswith("Close the central bridges")


# ------------------------------------------------------------
# Never passing on a figure no calculation produced
# ------------------------------------------------------------

def test_figures_must_come_from_the_calculations():
    sources = [{"tool": "coverage_gaps", "input": {"target_minutes": 4},
                "result": {"summary": {"population_within_target_percent": 91.6254, "mean_seconds": 150.4},
                           "method": "Census 2021", "worst": [{"residents_beyond_target": 1081}]}}]
    assert analysis_ok("91.6% of residents within 4 minutes; 1,081 beyond, mean 2.5 min (Census 2021)", sources)
    assert analyst.ungrounded_figures("A 30% drop and 43 seconds of waiting", sources) == ["30", "43"]
    assert analyst.ungrounded_figures("Three areas, 2 of them central", []) == []
    assert analyst.ungrounded_figures("Fire cover is 97.7%", []) == ["97.7"]


def analysis_ok(text, sources):
    return analyst.ungrounded_figures(text, sources) == []


def test_an_answer_with_invented_figures_is_replaced_by_the_rules(norwich):
    provider = ScriptedProvider([
        tool_reply("critical_roads", {}),
        text_reply("The worst junction is St Edmunds Road, with 43 seconds of waiting."),
    ])
    out = analyst.answer("Where are the biggest traffic bottlenecks?", norwich, provider=provider)
    assert out["mode"] == "rules" and out["sources"][0]["tool"] == "critical_roads"
    assert "figures no calculation produced (43)" in out["notice"]
    assert "St Edmunds" not in out["answer"]


def test_an_answer_with_no_calculation_behind_it_is_replaced(norwich):
    provider = ScriptedProvider([text_reply("Stations should be near population centres.")])
    out = analyst.answer("Which emergency station locations would improve fire coverage?", norwich, provider=provider)
    assert out["mode"] == "rules" and "without running any calculation" in out["notice"]
    assert out["sources"][0]["tool"] == "recommend_station"
