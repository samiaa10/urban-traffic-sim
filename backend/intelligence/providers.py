"""AI providers behind one interface.

The analyst and the scenario builder talk to a Provider, never to a
vendor's SDK, so the model or the vendor can change without touching them.
A provider takes a system prompt, a conversation and a list of tools
(JSON Schema) and answers with text and/or tool calls; the caller runs the
tools itself (tools.py), so a model can only ever ask for one of the
backend's own read-only operations.

The conversation is kept in the Messages API's block format (text,
tool_use, tool_result), which is the neutral format here: another
provider's adapter translates to and from it.

Configuration (backend environment variables only; nothing reaches the
frontend):

    AI_PROVIDER                 "anthropic", "ollama" or "none". Default:
                                "anthropic" when ANTHROPIC_API_KEY is set,
                                else "none".
    ANTHROPIC_API_KEY           the key for the Anthropic provider.
    OLLAMA_BASE_URL             where Ollama runs (default
                                http://localhost:11434): a free model on your
                                own machine, no key.
    AI_MODEL                    model id (default claude-opus-5-5, or
                                llama3.2:3b with Ollama).
    AI_EFFORT                   low / medium / high (default medium).
    AI_TIMEOUT_SECONDS          per request (default 60).
    AI_MAX_REQUESTS_PER_MINUTE  for the whole server (default 20).

Without a provider everything still works: answers come from the built-in
rules instead (analyst.py, scenarios.py).
"""

import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional


logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "medium"
DEFAULT_TIMEOUT = 60.0
DEFAULT_RATE = 20
DEFAULT_OLLAMA_URL = "http://localhost:11434"
# Small enough for an 8 GB laptop, and able to call tools
DEFAULT_OLLAMA_MODEL = "llama3.2:3b"
MAX_TOKENS = 16000

# Refusals: the API retries the request on a fallback model itself
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ProviderError(Exception):
    """The provider could not answer; `reason` is safe to show a user."""

    def __init__(self, reason, retryable=False):
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class Reply:
    text: str
    tool_calls: list
    stop_reason: str
    # The assistant turn as the provider wants it sent back (unchanged)
    content: list = field(default_factory=list)
    usage: dict = field(default_factory=dict)


class RateLimiter:
    """At most `per_minute` requests in any 60 s, for the whole server."""

    def __init__(self, per_minute, clock=time.monotonic):
        self.per_minute = per_minute
        self.clock = clock
        self.times = deque()
        self.lock = threading.Lock()

    def acquire(self):
        with self.lock:
            now = self.clock()
            while self.times and now - self.times[0] >= 60:
                self.times.popleft()
            if len(self.times) >= self.per_minute:
                raise ProviderError("The AI analyst is busy (request limit reached); try again in a minute", retryable=True)
            self.times.append(now)


class Provider:
    name = "none"
    model = None
    available = False
    reason = "No AI provider is configured"

    def respond(self, system, messages, tools):  # pragma: no cover - interface
        raise ProviderError(self.reason)

    def status(self):
        return {"provider": self.name, "model": self.model, "available": self.available, "reason": None if self.available else self.reason}


class NoProvider(Provider):
    def __init__(self, reason="No AI provider is configured (set AI_PROVIDER and its key: docs/AI.md)"):
        self.reason = reason


class AnthropicProvider(Provider):
    """Claude through the official SDK (Messages API with tool use)."""

    name = "anthropic"

    def __init__(self, api_key, model=DEFAULT_MODEL, effort=DEFAULT_EFFORT, timeout=DEFAULT_TIMEOUT,
                 limiter=None, client=None):
        self.model = model
        self.effort = effort
        self.limiter = limiter or RateLimiter(DEFAULT_RATE)
        if client is not None:
            self.client = client
        else:
            import anthropic

            self.client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=2)
        self.available = True
        self.reason = None

    def respond(self, system, messages, tools):
        import anthropic

        self.limiter.acquire()
        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system=system,
                messages=messages,
                tools=tools,
                output_config={"effort": self.effort},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except anthropic.AuthenticationError:
            raise ProviderError("The AI provider rejected its API key") from None
        except anthropic.PermissionDeniedError:
            raise ProviderError("The AI provider's key may not use this model") from None
        except anthropic.NotFoundError:
            raise ProviderError(f"The AI model {self.model} was not found") from None
        except anthropic.RateLimitError:
            raise ProviderError("The AI provider is rate limiting requests; try again shortly", retryable=True) from None
        except anthropic.APITimeoutError:
            raise ProviderError("The AI provider took too long to answer", retryable=True) from None
        except anthropic.APIConnectionError:
            raise ProviderError("The AI provider could not be reached", retryable=True) from None
        except anthropic.APIStatusError as error:
            logger.warning("AI provider error %s: %s", error.status_code, error.message)
            raise ProviderError("The AI provider returned an error", retryable=error.status_code >= 500) from None

        if response.stop_reason == "refusal":
            raise ProviderError("The AI model declined to answer this request")

        text = "".join(block.text for block in response.content if block.type == "text")
        calls = [
            ToolCall(block.id, block.name, dict(block.input))
            for block in response.content if block.type == "tool_use"
        ]
        usage = {
            "input_tokens": getattr(response.usage, "input_tokens", None),
            "output_tokens": getattr(response.usage, "output_tokens", None),
        }
        return Reply(text, calls, response.stop_reason, list(response.content), usage)


class OllamaProvider(Provider):
    """A model running locally in Ollama (free, no key), through its chat
    API with tool calling. The conversation's blocks are translated to
    Ollama's messages and back; tool calls come back as ToolCalls exactly
    as from any other provider, so the same validation applies."""

    name = "ollama"

    # A local model answers one request at a time (it shares the machine
    # with the simulation); others wait up to QUEUE_SECONDS, then fall back
    QUEUE_SECONDS = 60
    CONTEXT_TOKENS = 8192
    KEEP_ALIVE = "15m"

    def __init__(self, base_url=DEFAULT_OLLAMA_URL, model=DEFAULT_OLLAMA_MODEL, timeout=DEFAULT_TIMEOUT,
                 limiter=None, post=None, get=None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.limiter = limiter or RateLimiter(DEFAULT_RATE)
        self.post = post or self._post
        self.get = get or self._get
        self.available = True
        self.reason = None
        self.busy = threading.BoundedSemaphore(1)
        self._checked = None   # (when, available, reason) once checked

    # Planning (structured), writing (text) and tool calls all supported
    supports_structured = True

    def _get(self, url, timeout):
        import json
        import urllib.request

        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.load(response)

    def check(self):
        """Whether Ollama answers and has the model: (available, reason),
        cached for 15 s so the status endpoint stays fast."""
        if self._checked is not None and time.monotonic() - self._checked[0] < 15:
            return self._checked[1], self._checked[2]
        try:
            tags = self.get(f"{self.base_url}/api/tags", 2)
            names = {m.get("name") for m in tags.get("models", [])} | {m.get("model") for m in tags.get("models", [])}
            wanted = self.model if ":" in self.model else f"{self.model}:latest"
            ok = wanted in names or self.model in names
            reason = None if ok else f"The model {self.model} is not downloaded (run: ollama pull {self.model})"
        except Exception:
            ok, reason = False, f"Ollama is not running at {self.base_url}"
        self._checked = (time.monotonic(), ok, reason)
        return ok, reason

    def status(self):
        ok, reason = self.check()
        return {"provider": self.name, "model": self.model, "available": ok, "reason": reason, "local": True}

    def _options(self, temperature, max_tokens):
        options = {"temperature": temperature, "num_ctx": self.CONTEXT_TOKENS, "num_predict": max_tokens}
        body = {"model": self.model, "stream": False, "keep_alive": self.KEEP_ALIVE, "options": options}
        # Thinking models answer faster and keep to the format without it
        if self.model.split(":")[0] in ("qwen3", "deepseek-r1", "gpt-oss"):
            body["think"] = False
        return body

    def _chat(self, body):
        """One request to /api/chat, one at a time, with errors as ProviderError."""
        import socket
        import urllib.error

        self.limiter.acquire()
        if not self.busy.acquire(timeout=self.QUEUE_SECONDS):
            raise ProviderError("The local model is busy with another question", retryable=True)
        try:
            data = self.post(f"{self.base_url}/api/chat", body, self.timeout)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                raise ProviderError(f"Ollama has no model {self.model} (run: ollama pull {self.model})") from None
            raise ProviderError(f"Ollama returned an error ({error.code})", retryable=error.code >= 500) from None
        except (socket.timeout, TimeoutError):
            raise ProviderError("The local model took too long to answer", retryable=True) from None
        except (urllib.error.URLError, ConnectionError, OSError):
            raise ProviderError(f"Ollama is not running at {self.base_url}", retryable=True) from None
        except ValueError:
            raise ProviderError("Ollama sent an answer that could not be read") from None
        finally:
            self.busy.release()
        if not isinstance(data, dict) or "error" in data:
            message = data.get("error") if isinstance(data, dict) else None
            raise ProviderError(f"Ollama: {message}" if message else "Ollama sent no answer")
        return data

    def structured(self, system, messages, schema, max_tokens=600):
        """A JSON object matching `schema`: Ollama constrains the model's
        output to the schema, which small models follow far more reliably
        than free-form tool calls."""
        import json

        body = {**self._options(0, max_tokens), "messages": self.to_ollama(system, messages), "format": schema}
        data = self._chat(body)
        text = (data.get("message") or {}).get("content") or ""
        try:
            value = json.loads(text)
        except ValueError:
            raise ProviderError("The local model's answer was not valid JSON") from None
        if not isinstance(value, dict):
            raise ProviderError("The local model's answer was not a JSON object")
        return value

    def generate(self, system, messages, max_tokens=500, temperature=0.2):
        """Plain text from the model (the explanation step)."""
        body = {**self._options(temperature, max_tokens), "messages": self.to_ollama(system, messages)}
        data = self._chat(body)
        text = ((data.get("message") or {}).get("content") or "").strip()
        if data.get("done_reason") == "length":
            # Cut off at the token limit: keep the whole sentences only
            import re

            ends = [m.end() for m in re.finditer(r"[.!?](?=\s|$)", text)]
            text = text[:ends[-1]] if ends else ""
        return text

    def _post(self, url, body, timeout):
        import json
        import urllib.request

        request = urllib.request.Request(
            url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)

    @staticmethod
    def to_ollama(system, messages):
        """The conversation in Ollama's message format."""
        out = [{"role": "system", "content": system}]
        names = {}   # tool_use id -> tool name (Ollama's tool messages name the tool)
        for message in messages:
            role, content = message["role"], message["content"]
            if isinstance(content, str):
                out.append({"role": role, "content": content})
                continue
            if role == "assistant":
                text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
                calls = []
                for block in content:
                    if block.get("type") == "tool_use":
                        names[block["id"]] = block["name"]
                        calls.append({"function": {"name": block["name"], "arguments": block["input"]}})
                entry = {"role": "assistant", "content": text}
                if calls:
                    entry["tool_calls"] = calls
                out.append(entry)
                continue
            for block in content:
                if block.get("type") == "tool_result":
                    result = block.get("content", "")
                    if block.get("is_error"):
                        result = f"Error: {result}"
                    out.append({"role": "tool", "content": result, "tool_name": names.get(block.get("tool_use_id"), "")})
                elif block.get("type") == "text":
                    out.append({"role": role, "content": block["text"]})
        return out

    def respond(self, system, messages, tools):
        import json

        body = {
            **self._options(0.2, 1000),
            "messages": self.to_ollama(system, messages),
            "tools": [{"type": "function", "function": {
                "name": t["name"], "description": t["description"], "parameters": t["input_schema"],
            }} for t in tools],
        }
        data = self._chat(body)
        message = data.get("message") or {}
        text = message.get("content") or ""
        calls, content = [], ([{"type": "text", "text": text}] if text else [])
        for i, call in enumerate(message.get("tool_calls") or []):
            function = call.get("function") or {}
            arguments = function.get("arguments") or {}
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except ValueError:
                    arguments = {"_unreadable": arguments}
            if not isinstance(arguments, dict):
                arguments = {"_unreadable": arguments}
            ident = f"ollama-{len(messages)}-{i}"
            calls.append(ToolCall(ident, function.get("name", ""), arguments))
            content.append({"type": "tool_use", "id": ident, "name": function.get("name", ""), "input": arguments})
        usage = {"input_tokens": data.get("prompt_eval_count"), "output_tokens": data.get("eval_count")}
        return Reply(text, calls, "tool_use" if calls else (data.get("done_reason") or "stop"), content, usage)


_provider = None
_provider_lock = threading.Lock()


def _local(url):
    from urllib.parse import urlparse

    return (urlparse(url).hostname or "") in ("localhost", "127.0.0.1", "::1")


def _environment_provider():
    choice = os.environ.get("AI_PROVIDER", "").strip().lower()
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not choice:
        choice = "anthropic" if key else "none"
    if choice == "none":
        return NoProvider()
    if choice == "ollama":
        try:
            timeout = float(os.environ.get("AI_TIMEOUT_SECONDS", 120))
            rate = int(os.environ.get("AI_MAX_REQUESTS_PER_MINUTE", DEFAULT_RATE))
        except ValueError:
            return NoProvider("AI_TIMEOUT_SECONDS or AI_MAX_REQUESTS_PER_MINUTE is not a number")
        base = os.environ.get("OLLAMA_BASE_URL", DEFAULT_OLLAMA_URL).strip() or DEFAULT_OLLAMA_URL
        if not _local(base) and os.environ.get("OLLAMA_ALLOW_REMOTE") != "1":
            return NoProvider("OLLAMA_BASE_URL must be on this machine (localhost); set OLLAMA_ALLOW_REMOTE=1 "
                              "only for an Ollama you run privately")
        return OllamaProvider(
            base,
            os.environ.get("AI_MODEL", DEFAULT_OLLAMA_MODEL).strip() or DEFAULT_OLLAMA_MODEL,
            timeout=timeout, limiter=RateLimiter(max(1, rate)),
        )
    if choice != "anthropic":
        return NoProvider(f"Unknown AI_PROVIDER {choice!r} (supported: anthropic, ollama, none)")
    if not key:
        return NoProvider("AI_PROVIDER is anthropic but ANTHROPIC_API_KEY is not set")
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return NoProvider("The anthropic package is not installed")
    try:
        timeout = float(os.environ.get("AI_TIMEOUT_SECONDS", DEFAULT_TIMEOUT))
        rate = int(os.environ.get("AI_MAX_REQUESTS_PER_MINUTE", DEFAULT_RATE))
    except ValueError:
        return NoProvider("AI_TIMEOUT_SECONDS or AI_MAX_REQUESTS_PER_MINUTE is not a number")
    effort = os.environ.get("AI_EFFORT", DEFAULT_EFFORT)
    if effort not in ("low", "medium", "high"):
        effort = DEFAULT_EFFORT
    return AnthropicProvider(
        key, model=os.environ.get("AI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        effort=effort, timeout=timeout, limiter=RateLimiter(max(1, rate)),
    )


def get_provider():
    """The configured provider (built once per process)."""
    global _provider
    with _provider_lock:
        if _provider is None:
            _provider = _environment_provider()
        return _provider


def set_provider(provider: Optional[Provider]):
    """Replace the provider (tests), or None to read the environment again."""
    global _provider
    with _provider_lock:
        _provider = provider


def provider_status():
    return get_provider().status()
