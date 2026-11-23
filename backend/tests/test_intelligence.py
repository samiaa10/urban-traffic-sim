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
    ("Where is fire cover weakest?", "coverage_gaps"),
    ("Is the north of the city well covered by police?", "coverage_gaps"),
    ("How long does it take an ambulance to reach Bowthorpe?", "coverage_gaps"),
    ("Which roads are the weakest links?", "critical_roads"),
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


def test_the_rules_read_the_direction_of_a_rush_hour(norwich):
    def direction(text):
        return next(a for a in scenarios.rule_plan(text, norwich.graph)["actions"] if a["type"] == "surge")["direction"]

    assert direction("An evening rush hour into the city centre") == "inbound"
    assert direction("An evening rush hour") == "outbound"
    assert direction("A morning rush hour") == "inbound"


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
    # Only an Ollama on this machine, unless explicitly allowed
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://studio.local:11434/")
    monkeypatch.setenv("AI_MODEL", "qwen3:4b")
    refused = providers._environment_provider()
    assert isinstance(refused, NoProvider) and "must be on this machine" in refused.reason
    monkeypatch.setenv("OLLAMA_ALLOW_REMOTE", "1")
    provider = providers._environment_provider()
    assert (provider.base_url, provider.model) == ("http://studio.local:11434", "qwen3:4b")


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


def tags(*models):
    """Plays Ollama's /api/tags: which models are downloaded."""
    return lambda url, timeout: {"models": [{"name": m, "model": m} for m in models]}


def down(url, timeout):
    import urllib.error

    raise urllib.error.URLError("connection refused")


def local(answers, model="llama3.2:3b", **kwargs):
    """An Ollama provider on a fake Ollama that has the model."""
    fake = FakeOllama(answers)
    return OllamaProvider(model=model, post=fake, get=tags(model), **kwargs), fake


def structured(value):
    import json

    return {"message": {"role": "assistant", "content": json.dumps(value)}, "done_reason": "stop"}


def calc(tool, **fields):
    return {"tool": tool, "service": "fire", "target_minutes": 0, "stations": 3, "roads": [],
            "central_bridges": False, "disruption": "none", **fields}


def planned(*calculations, intent="answer", scenario="", reply=""):
    return structured({"intent": intent, "calculations": list(calculations), "scenario": scenario, "reply": reply})


def test_the_status_says_whether_the_local_model_is_really_there():
    assert OllamaProvider(get=tags("llama3.2:3b")).status() == {
        "provider": "ollama", "model": "llama3.2:3b", "available": True, "reason": None, "local": True}
    missing = OllamaProvider(model="qwen3:4b", get=tags("llama3.2:3b")).status()
    assert missing["available"] is False and "ollama pull qwen3:4b" in missing["reason"]
    assert "Ollama is not running" in OllamaProvider(get=down).status()["reason"]


def test_the_local_model_plans_the_calculations_and_explains_the_facts(norwich):
    provider, fake = local([
        planned(calc("coverage_gaps", target_minutes=4), calc("recommend_station", target_minutes=4)),
        ollama_says("Bowthorpe is the weakest area, and one new station near it would lift cover from 91.6% to 97.7%."),
    ])
    out = analyst.answer("Where is fire cover weakest, and what would one new station do?", norwich, provider=provider)
    assert out["mode"] == "ai" and out["model"] == "llama3.2:3b"
    assert [s["tool"] for s in out["sources"]] == ["coverage_gaps", "recommend_station"]
    # The facts are the calculations' own write-ups, exact
    assert out["facts"][0]["text"] == analyst.rule_answer("coverage_gaps", out["sources"][0]["result"])
    assert "97.7%" in out["facts"][1]["text"]
    assert out["explanation"].startswith("Bowthorpe is the weakest") and out["explanation_note"] is None
    # The plan was held to the schema; tools offered only by name
    first = fake.requests[0]["body"]
    assert first["format"]["properties"]["calculations"]["items"]["properties"]["tool"]["enum"][0] == "coverage_gaps"
    assert first["options"]["temperature"] == 0 and first["keep_alive"] == "15m" and "tools" not in first
    assert "CALCULATED FACTS" in fake.requests[1]["body"]["messages"][-1]["content"]


def test_the_facts_are_shown_before_the_model_explains_them(norwich):
    provider, fake = local([
        planned(calc("coverage_gaps", target_minutes=4)),
        ollama_says("Bowthorpe is the weakest area."),
    ])
    events = []
    for event in analyst.answer_stream("Where is fire cover weakest?", norwich, provider=provider):
        events.append((event["stage"], len(fake.requests)))
        if event["stage"] == "calculated":
            early = event["answer"]
    # The facts arrive after the plan (one model call) and before the explanation (the second)
    assert events == [("planning", 0), ("calculated", 1), ("explaining", 1), ("done", 2)]
    assert early["pending"] is True and early["explanation"] is None
    assert early["facts"][0]["tool"] == "coverage_gaps"

    # A proposal is drafted between the facts and the explanation
    proposal = {"supported": True, "title": "Flood", "notes": "", "explanation": "Roads near Riverside close.",
                "actions": [{"type": "flood", "place": "Riverside"}]}
    provider, _ = local([planned(calc("flood_vulnerability"), intent="simulate", scenario="A flood at Riverside"),
                         structured(proposal), ollama_says("Riverside floods.")])
    stages = [e["stage"] for e in analyst.answer_stream("Simulate a flood at Riverside", norwich, provider=provider)]
    assert stages == ["planning", "calculated", "proposing", "explaining", "done"]

    # Without the model: only the rules' answer
    provider = OllamaProvider(get=down)
    assert [e["stage"] for e in analyst.answer_stream("Where is fire cover weakest?", norwich, provider=provider)] == ["done"]


def test_an_explanation_with_invented_figures_is_retried_then_withheld(norwich):
    provider, fake = local([
        planned(calc("critical_roads")),
        ollama_says("Dereham Road carries 987% of trips."),
        ollama_says("It carries 988% of them."),
    ])
    out = analyst.answer("Which roads matter most?", norwich, provider=provider)
    assert out["mode"] == "ai" and out["explanation"] is None
    assert "figures or roads no calculation produced (988)" in out["explanation_note"]
    assert "figures or roads that are not in the facts (987)" in fake.requests[2]["body"]["messages"][-1]["content"]
    # The facts still stand
    assert out["facts"][0]["tool"] == "critical_roads" and "Dereham Road" in out["facts"][0]["text"]

    provider, _ = local([planned(calc("critical_roads")), ollama_says("Dereham Road carries 987%."),
                         ollama_says("Dereham Road carries the most trips.")])
    assert analyst.answer("Which roads matter most?", norwich, provider=provider)["explanation"] == \
        "Dereham Road carries the most trips."


def test_a_sentence_with_an_invented_figure_is_removed_without_asking_again(norwich):
    provider, fake = local([
        planned(calc("critical_roads")),
        ollama_says("Dereham Road carries the most trips. It handles 987 lorries a day. "
                    "Closing it would push trips onto other roads. Planners could simulate that closure."),
    ])
    out = analyst.answer("Which roads matter most?", norwich, provider=provider)
    assert len(fake.requests) == 2   # no second explanation was needed
    assert out["explanation"] == ("Dereham Road carries the most trips. Closing it would push trips onto other roads. "
                                  "Planners could simulate that closure.")
    assert out["explanation_note"] == "1 sentence of the explanation used figures or roads no calculation produced (987) and was removed."
    assert "987" not in out["answer"]


def test_a_calculated_figure_presented_as_a_real_world_statistic_is_removed(norwich):
    # Seen from llama3.2:3b: a real calculated figure claimed for a year
    provider, _ = local([
        planned(calc("critical_roads")),
        ollama_says("In 2024 Dereham Road carried 17.0% of trips. Dereham Road carries the most trips in the model. "
                    "Closing it would push trips onto other roads."),
    ])
    out = analyst.answer("How busy was Dereham Road in 2024?", norwich, provider=provider)
    assert out["explanation"] == "Dereham Road carries the most trips in the model. Closing it would push trips onto other roads."
    assert "(2024)" in out["explanation_note"]
    assert "Census 2021" not in out["explanation_note"]


def test_roads_the_calculations_never_mention_are_removed(norwich):
    # Seen from llama3.2:3b: "the most congested roads are the A140 and A47"
    provider, _ = local([
        planned(calc("critical_roads")),
        ollama_says("Dereham Road carries the most trips. The A140 and Ipswich Road are the most congested. "
                    "Closing Dereham Road would push trips onto other roads."),
    ])
    out = analyst.answer("Which roads matter most?", norwich, provider=provider)
    assert out["explanation"] == "Dereham Road carries the most trips. Closing Dereham Road would push trips onto other roads."
    assert "(A140, Ipswich Road)" in out["explanation_note"]


def test_follow_ups_reach_the_planner(norwich):
    provider, fake = local([planned(calc("coverage_gaps", service="police", target_minutes=4)), ollama_says("Police cover is wider.")])
    history = [{"role": "user", "content": "How is fire cover?"}, {"role": "assistant", "content": "91.6% within 4 min."}]
    out = analyst.answer("And for police?", norwich, provider=provider, history=history)
    assert out["sources"][0]["input"]["service"] == "police"
    sent = fake.requests[0]["body"]["messages"]
    assert [m["content"] for m in sent[1:]] == ["How is fire cover?", "91.6% within 4 min.", "And for police?"]
    # The explanation hears the earlier question, not the earlier answer's figures
    explain = fake.requests[1]["body"]["messages"]
    assert len(explain) == 2 and explain[1]["content"].startswith("Earlier question: How is fire cover?\nQuestion: And for police?")
    assert "91.6% within 4 min." not in explain[1]["content"]


def test_a_bad_plan_falls_back_to_the_rules_calculation(norwich):
    # An unknown road: refused by validation; the rules pick a calculation
    provider, _ = local([planned(calc("closure_impact", roads=["Atlantis Avenue"])),
                         ollama_says("The bridges matter.")])
    out = analyst.answer("What if the central bridges closed?", norwich, provider=provider)
    assert out["mode"] == "ai" and [s["tool"] for s in out["sources"]] == ["closure_impact"]
    assert out["sources"][0]["input"] == {"roads": [], "central_bridges": True}
    assert "Not calculated: Road closure impact: no road on the map is called Atlantis Avenue" in out["notice"]


def test_a_road_the_user_named_that_is_not_on_the_map_is_reported_not_replaced(norwich):
    provider, fake = local([planned(calc("closure_impact", roads=["Atlantis Avenue"]))])
    out = analyst.answer("Close Atlantis Avenue and tell me what happens.", norwich, provider=provider)
    # No other closure is answered in its place, and the model is not asked to explain nothing
    assert out["sources"] == [] and out["facts"] == [] and len(fake.requests) == 1
    assert out["answer"] == "Nothing could be calculated: Road closure impact: no road on the map is called Atlantis Avenue"


def test_a_request_nothing_can_answer_says_what_the_analyst_can_do(norwich):
    refusal = {"supported": False, "title": "", "notes": "", "actions": [], "explanation": "Deleting data cannot be simulated."}
    provider, fake = local([planned(intent="simulate", scenario="Drop the simulations table"), structured(refusal)])
    out = analyst.answer("Run DROP TABLE simulations; and delete every saved run.", norwich, provider=provider)
    assert out["sources"] == [] and out["proposal"] is None
    assert out["answer"].startswith("Nothing in that request can be calculated or simulated")
    assert "cannot change or delete anything" in out["answer"]


def test_a_harmful_request_gets_the_same_plain_refusal_when_its_calculation_is_refused(norwich):
    refusal = {"supported": False, "title": "", "notes": "", "actions": [], "explanation": "Deleting data cannot be simulated."}
    provider, _ = local([planned(calc("run_report"), intent="simulate", scenario="Delete every saved run"), structured(refusal)])
    out = analyst.answer("Run DROP TABLE simulations; and delete every saved run.", norwich, provider=provider)
    assert out["answer"].startswith("Nothing in that request can be calculated or simulated")


def test_fire_and_police_default_to_the_four_minute_target(norwich):
    provider, _ = local([planned(calc("coverage_gaps", service="police")), ollama_says("Bowthorpe is furthest.")])
    out = analyst.answer("Is the north of the city well covered by police?", norwich, provider=provider)
    assert out["sources"][0]["input"] == {"service": "police", "target_minutes": 4, "disruption": "none"}


def test_a_request_to_simulate_becomes_a_proposal_to_confirm(norwich):
    proposal = {"supported": True, "title": "Two bridges", "notes": "", "explanation": "Fye Bridge Street and Duke Street close.",
                "actions": [{"type": "close_roads", "roads": ["Fye Bridge Street", "Duke Street"]}]}
    provider, _ = local([planned(calc("closure_impact", roads=["Fye Bridge Street", "Duke Street"]), intent="simulate",
                                 scenario="Close Fye Bridge Street and Duke Street"),
                         structured(proposal), ollama_says("Closing them lengthens some trips.")])
    out = analyst.answer("Simulate closing Fye Bridge Street and Duke Street", norwich, provider=provider)
    assert out["proposal"]["mode"] == "ai" and out["proposal"]["summary"][-1].startswith("Close Fye Bridge Street, Duke Street")
    assert out["proposal"]["explanation"] == "Fye Bridge Street and Duke Street close."
    assert out["facts"][0]["tool"] == "closure_impact"


def test_without_the_local_model_the_rules_answer(norwich):
    provider = OllamaProvider(get=down)
    out = analyst.answer("Which roads do trips depend on most?", norwich, provider=provider)
    assert out["mode"] == "rules" and "local AI model is unavailable (Ollama is not running" in out["notice"]
    assert out["facts"][0]["tool"] == "critical_roads" and out["explanation"] is None
    import urllib.error

    provider, _ = local([urllib.error.URLError("refused")])
    out = analyst.answer("Which roads do trips depend on most?", norwich, provider=provider)
    assert out["mode"] == "rules" and "could not answer (Ollama is not running" in out["notice"]
    provider, _ = local([TimeoutError()])
    assert "took too long" in analyst.answer("Which roads matter?", norwich, provider=provider)["notice"]


def test_one_question_at_a_time_on_the_local_model():
    provider, _ = local([ollama_says("x")])
    provider.QUEUE_SECONDS = 0.01
    provider.busy.acquire()
    with pytest.raises(ProviderError, match="busy with another question"):
        provider.generate("s", [{"role": "user", "content": "q"}])
    provider.busy.release()
    assert provider.generate("s", [{"role": "user", "content": "q"}]) == "x"


def test_a_thinking_model_is_asked_not_to_think():
    provider, fake = local([ollama_says("x")], model="qwen3:4b")
    provider.generate("s", [{"role": "user", "content": "q"}])
    assert fake.requests[0]["body"]["think"] is False
    provider, fake = local([ollama_says("x")])
    provider.generate("s", [{"role": "user", "content": "q"}])
    assert "think" not in fake.requests[0]["body"]


def test_the_local_model_proposes_scenarios_through_the_same_checks(norwich):
    ok = {"supported": True, "title": "Bridges", "notes": "", "explanation": "The central bridges close.",
          "actions": [{"type": "close_bridges"}]}
    provider, fake = local([structured(ok)])
    out = scenarios.plan("Shut the bridges", norwich, provider=provider)
    assert out["mode"] == "ai" and out["summary"][-1].startswith("Close the central bridges")
    assert out["explanation"] == "The central bridges close."
    assert fake.requests[0]["body"]["format"]["required"][0] == "supported"
    # An invented duration in its description is dropped, not shown
    noisy = {**ok, "explanation": "The bridges close for 90 minutes."}
    provider, _ = local([structured(noisy)])
    assert scenarios.plan("Shut the bridges", norwich, provider=provider)["explanation"] is None
    # An invented place: the rules read the request
    bad = {**ok, "actions": [{"type": "flood", "place": "Narnia"}]}
    provider, _ = local([structured(bad)])
    out = scenarios.plan("flood in the city centre", norwich, provider=provider)
    assert out["mode"] == "rules" and "not valid (unknown place 'Narnia')" in out["notice"]
    # Not something the engine can do: the model says so, the rules agree
    provider, _ = local([structured({**ok, "supported": False, "explanation": "Earthquakes are not modelled."})])
    with pytest.raises(scenarios.ScenarioError):
        scenarios.plan("Simulate an earthquake", norwich, provider=provider)


def test_a_valid_proposal_that_adds_unrequested_things_is_not_used(norwich):
    from backend.intelligence.scenarios import unrequested

    graph = norwich.graph
    # Seen from real small models: valid forms that do not match the request
    flood = "Simulate a big flood near Riverside during the evening rush hour"
    assert unrequested({"actions": [{"type": "flood", "place": "Riverside"}, {"type": "surge", "place": "Riverside"}]},
                       flood, graph) == []
    assert unrequested({"actions": [{"type": "flood", "place": "Riverside"},
                                    {"type": "close_roads", "roads": ["Prince of Wales Road"]}]}, flood, graph) == \
        ["road closures, which the request did not ask for"]
    three = "Three emergencies at once in Earlham, Lakenham and the city centre"
    assert unrequested({"actions": [{"type": "incidents", "incidents": [
        {"kind": "medical", "place": "Earlham"}, {"kind": "fire", "place": "Norwich station"}]}]}, three, graph) == \
        ["Norwich station, which the request did not name"]
    bridges = "What would happen if two major bridges closed? Simulate it."
    assert unrequested({"actions": [{"type": "close_roads", "roads": ["Prince of Wales Road", "Whitefriars"]}]},
                       bridges, graph) == []
    assert unrequested({"actions": [{"type": "close_roads", "roads": ["Dereham Road"]},
                                    {"type": "surge", "place": "Sprowston"}]}, bridges, graph) == \
        ["closing Dereham Road, which the request did not name", "a rush-hour surge, which the request did not ask for"]

    # A form with nothing in it but traffic, for a request that was not about traffic
    assert unrequested({"actions": [{"type": "traffic", "vehicles": 0}]}, "Run DROP TABLE simulations;", graph) == \
        ["only background traffic, which the request did not ask for"]

    # In the planner: the rules read the request instead, and say why
    loose = {"supported": True, "title": "Emergencies", "notes": "", "explanation": "Three incidents.",
             "actions": [{"type": "incidents", "incidents": [{"kind": "medical", "place": "Norwich station"}]}]}
    provider, _ = local([structured(loose)])
    out = scenarios.plan(three, norwich, provider=provider)
    assert out["mode"] == "rules" and "did not match the request (Norwich station" in out["notice"]
    assert [a["type"] for a in out["proposal"]["actions"]] == ["traffic", "incidents"]


def test_an_explanation_cut_off_at_the_limit_keeps_whole_sentences():
    cut = {**ollama_says("Bowthorpe is furthest. Earlham is next. Lakenham has"), "done_reason": "length"}
    provider, _ = local([cut, {**ollama_says("No full stop here at all"), "done_reason": "length"}])
    assert provider.generate("system", [{"role": "user", "content": "Why?"}]) == "Bowthorpe is furthest. Earlham is next."
    assert provider.generate("system", [{"role": "user", "content": "Why?"}]) == ""


def test_coverage_facts_group_areas_by_place_and_say_when_none_are_beyond():
    def area(where, residents, median, km):
        return {"where": where, "residents_beyond_target": residents, "median_seconds": median, "road_km": km,
                "nearest_station": "Earlham Police Station"}

    result = {"layout": "existing", "stations": ["Earlham Police Station"], "target_seconds": 240,
              "summary": {"population_within_target_percent": 98.3, "population_beyond_target": 2409},
              "worst_areas": [area("Bowthorpe", 696, 276, 6.1), area("Lakenham", 900, 250, 3.0), area("Bowthorpe", 345, 240, 5.5)]}
    lines = analyst.rule_answer("coverage_gaps", result).splitlines()
    assert lines[2:] == [
        "- Bowthorpe: 1,041 residents beyond it in 2 areas, median 4.0 to 4.6 min, 5.5 to 6.1 km by road from Earlham Police Station",
        "- Lakenham: 900 residents beyond it, median 4.2 min, 3.0 km by road from Earlham Police Station",
    ]
    result["worst_areas"] = []
    assert analyst.rule_answer("coverage_gaps", result).endswith("No residents are beyond the target.")


def test_measured_small_numbers_must_come_from_calculations():
    assert analyst.ungrounded_figures("a 10-minute closure", []) == ["10"]
    assert analyst.ungrounded_figures("two bridges and 3 stations; point 2", []) == []


def test_new_calculations_answer_flooding_and_station_plans(norwich):
    tool, arguments = analyst.choose_tool("Find the areas most vulnerable to flooding.", norwich.graph, False)
    assert tool == "flood_vulnerability"
    tool, arguments = analyst.choose_tool("How could we improve ambulance response times?", norwich.graph, False)
    assert (tool, arguments["service"]) == ("station_plan", "ambulance")
    flood = Toolbox(norwich).run("flood_vulnerability", {})
    assert flood["places"] and all(p["place"] in analysis.RIVERSIDE_PLACES for p in flood["places"])
    plan_ = Toolbox(norwich).run("station_plan", {"service": "ambulance", "stations": 2, "target_minutes": None})
    assert plan_["existing"] is None and len(plan_["optimised_sites"]) == 2
    gaps = Toolbox(norwich).run("coverage_gaps", {"service": "fire", "target_minutes": 4, "disruption": "none"})
    assert gaps["worst_areas"][0]["nearest_station"].endswith("Fire Station") and gaps["worst_areas"][0]["road_km"] > 0


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
