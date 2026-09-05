"""sim/llm_client.py: provider selection, and the Ollama transport.

`complete_json`'s contract -- build a request, extract the text, parse, validate,
retry with the error appended -- is covered in test_llm_client.py against the
Anthropic shape. This file covers the part that differs per provider: where the
request goes, what the payload looks like, which headers are sent, and where the
model's text is found in the reply.

Ollama matters here because it is the only way this project's S13 persona traces
can be produced without an Anthropic key. Everything runs against a fake
transport; no test contacts a model.
"""
from __future__ import annotations

import json as json_module

import httpx
import pytest

from sim import llm_client
from sim.llm_client import LLMUnavailableError, LLMValidationError, complete_json

SCHEMA = {
    "type": "object",
    "required": ["persona_id", "value"],
    "additionalProperties": False,
    "properties": {"persona_id": {"type": "string"}, "value": {"type": "number"}},
}

VALID_TEXT = json_module.dumps({"persona_id": "mission", "value": 0.5})
INVALID_TEXT = json_module.dumps({"persona_id": "mission"})


class FakeResponse:
    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body

    def raise_for_status(self):
        pass


class OllamaTransport:
    """Replies in Ollama's /api/chat shape: {"message": {"content": ...}}."""

    def __init__(self, texts):
        self._texts = list(texts)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        text = self._texts[len(self.calls) - 1]
        return FakeResponse({"message": {"role": "assistant", "content": text}})


class AnthropicTransport:
    def __init__(self, texts):
        self._texts = list(texts)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        text = self._texts[len(self.calls) - 1]
        return FakeResponse({"content": [{"type": "text", "text": text}]})


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.setenv("LLM_OFFLINE", "0")
    for name in ("LLM_PROVIDER", "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)


def use_ollama(monkeypatch, **env):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    for key, value in env.items():
        monkeypatch.setenv(key, value)


# --- where the request goes ------------------------------------------------

def test_ollama_posts_to_api_chat_on_the_local_daemon_by_default(monkeypatch):
    use_ollama(monkeypatch)
    transport = OllamaTransport([VALID_TEXT])
    complete_json("hi", SCHEMA, client=transport)
    assert transport.calls[0]["url"] == "http://localhost:11434/api/chat"


def test_ollama_honours_an_explicit_base_url(monkeypatch):
    use_ollama(monkeypatch, LLM_BASE_URL="https://ollama.example.com/")
    transport = OllamaTransport([VALID_TEXT])
    complete_json("hi", SCHEMA, client=transport)
    assert transport.calls[0]["url"] == "https://ollama.example.com/api/chat"


def test_anthropic_remains_the_default_provider(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "k")
    transport = AnthropicTransport([VALID_TEXT])
    complete_json("hi", SCHEMA, client=transport)
    assert transport.calls[0]["url"] == "https://api.anthropic.com/v1/messages"
    assert transport.calls[0]["headers"]["anthropic-version"] == llm_client.ANTHROPIC_VERSION


# --- payload ---------------------------------------------------------------

def test_ollama_disables_streaming_and_asks_for_json(monkeypatch):
    """A streamed reply would not survive .json(); format=json stops prose."""
    use_ollama(monkeypatch)
    transport = OllamaTransport([VALID_TEXT])
    complete_json("hi", SCHEMA, client=transport)
    payload = transport.calls[0]["json"]
    assert payload["stream"] is False
    assert payload["format"] == "json"


def test_ollama_puts_temperature_in_options_not_at_the_top_level(monkeypatch):
    use_ollama(monkeypatch)
    transport = OllamaTransport([VALID_TEXT])
    complete_json("hi", SCHEMA, temperature=0.7, client=transport)
    payload = transport.calls[0]["json"]
    assert payload["options"]["temperature"] == 0.7
    assert "temperature" not in payload


def test_ollama_defaults_to_temperature_zero(monkeypatch):
    use_ollama(monkeypatch)
    transport = OllamaTransport([VALID_TEXT])
    complete_json("hi", SCHEMA, client=transport)
    assert transport.calls[0]["json"]["options"]["temperature"] == 0.0


def test_ollama_uses_its_own_default_model(monkeypatch):
    use_ollama(monkeypatch)
    transport = OllamaTransport([VALID_TEXT])
    complete_json("hi", SCHEMA, client=transport)
    assert transport.calls[0]["json"]["model"] == llm_client.DEFAULT_OLLAMA_MODEL


def test_an_explicit_model_wins_over_the_provider_default(monkeypatch):
    use_ollama(monkeypatch, LLM_MODEL="llama3.2:3b")
    transport = OllamaTransport([VALID_TEXT])
    complete_json("hi", SCHEMA, client=transport)
    assert transport.calls[0]["json"]["model"] == "llama3.2:3b"


# --- auth ------------------------------------------------------------------

def test_ollama_needs_no_api_key(monkeypatch):
    """A local daemon has no key; requiring one would block the whole point."""
    use_ollama(monkeypatch)
    transport = OllamaTransport([VALID_TEXT])
    assert complete_json("hi", SCHEMA, client=transport) == {"persona_id": "mission",
                                                             "value": 0.5}


def test_ollama_sends_no_authorization_header_without_a_key(monkeypatch):
    use_ollama(monkeypatch)
    transport = OllamaTransport([VALID_TEXT])
    complete_json("hi", SCHEMA, client=transport)
    assert "authorization" not in {k.lower() for k in transport.calls[0]["headers"]}


def test_ollama_sends_a_bearer_token_when_a_key_is_set(monkeypatch):
    """Ollama Cloud authenticates with a bearer token; local ignores it."""
    use_ollama(monkeypatch, LLM_API_KEY="secret-key")
    transport = OllamaTransport([VALID_TEXT])
    complete_json("hi", SCHEMA, client=transport)
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer secret-key"


def test_ollama_never_sends_the_anthropic_key_header(monkeypatch):
    use_ollama(monkeypatch, LLM_API_KEY="secret-key")
    transport = OllamaTransport([VALID_TEXT])
    complete_json("hi", SCHEMA, client=transport)
    assert "x-api-key" not in {k.lower() for k in transport.calls[0]["headers"]}


def test_ollama_without_an_injected_client_and_without_a_key_is_allowed(monkeypatch):
    """The guard that demands a key must not fire for a local daemon.

    Uses a transport that raises so the test proves the *guard* passed rather
    than that a request succeeded.
    """
    use_ollama(monkeypatch)

    class Boom:
        def post(self, url, **kwargs):
            raise RuntimeError("reached the transport")

    with pytest.raises(RuntimeError, match="reached the transport"):
        complete_json("hi", SCHEMA, client=Boom())


# --- reply parsing and the shared retry path -------------------------------

def test_ollama_reads_the_text_from_message_content(monkeypatch):
    use_ollama(monkeypatch)
    transport = OllamaTransport([VALID_TEXT])
    assert complete_json("hi", SCHEMA, client=transport)["value"] == 0.5


def test_ollama_retries_a_schema_violation_with_the_error_appended(monkeypatch):
    use_ollama(monkeypatch)
    transport = OllamaTransport([INVALID_TEXT, VALID_TEXT])
    result = complete_json("hi", SCHEMA, client=transport)
    assert result["value"] == 0.5
    assert len(transport.calls) == 2
    assert "value" in transport.calls[1]["json"]["messages"][0]["content"]


def test_ollama_exhausting_the_retry_budget_raises(monkeypatch):
    use_ollama(monkeypatch)
    transport = OllamaTransport([INVALID_TEXT] * 3)
    with pytest.raises(LLMValidationError):
        complete_json("hi", SCHEMA, retries=3, client=transport)
    assert len(transport.calls) == 3


# --- refusals --------------------------------------------------------------

def test_offline_still_refuses_for_ollama(monkeypatch):
    use_ollama(monkeypatch, LLM_OFFLINE="1")
    with pytest.raises(LLMUnavailableError):
        complete_json("hi", SCHEMA, client=OllamaTransport([VALID_TEXT]))


def test_an_unknown_provider_is_refused_by_name(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gpt-at-home")
    with pytest.raises(llm_client.LLMConfigError, match="gpt-at-home"):
        complete_json("hi", SCHEMA, client=OllamaTransport([VALID_TEXT]))


def test_provider_name_is_case_insensitive_and_trimmed(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "  Ollama  ")
    transport = OllamaTransport([VALID_TEXT])
    complete_json("hi", SCHEMA, client=transport)
    assert transport.calls[0]["url"].endswith("/api/chat")


# --- Ollama Cloud's OpenAI-compatible endpoint -----------------------------
#
# https://ollama.com serves both: the native API at the root, and an
# OpenAI-compatible one under /v1. A base URL ending in /v1 means the caller
# asked for the second, and posting {base}/api/chat there would 404.


class OpenAITransport:
    """Replies in the OpenAI shape: {"choices": [{"message": {"content": ...}}]}."""

    def __init__(self, texts):
        self._texts = list(texts)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        text = self._texts[len(self.calls) - 1]
        return FakeResponse({"choices": [{"index": 0,
                                          "message": {"role": "assistant", "content": text}}]})


def test_a_v1_base_posts_to_chat_completions(monkeypatch):
    use_ollama(monkeypatch, LLM_BASE_URL="https://ollama.com/v1")
    transport = OpenAITransport([VALID_TEXT])
    complete_json("hi", SCHEMA, client=transport)
    assert transport.calls[0]["url"] == "https://ollama.com/v1/chat/completions"


def test_a_v1_base_with_a_trailing_slash_still_works(monkeypatch):
    use_ollama(monkeypatch, LLM_BASE_URL="https://ollama.com/v1/")
    transport = OpenAITransport([VALID_TEXT])
    complete_json("hi", SCHEMA, client=transport)
    assert transport.calls[0]["url"] == "https://ollama.com/v1/chat/completions"


def test_a_v1_base_asks_for_json_the_openai_way(monkeypatch):
    use_ollama(monkeypatch, LLM_BASE_URL="https://ollama.com/v1")
    transport = OpenAITransport([VALID_TEXT])
    complete_json("hi", SCHEMA, client=transport)
    payload = transport.calls[0]["json"]
    assert payload["response_format"] == {"type": "json_object"}
    assert "format" not in payload


def test_a_v1_base_puts_temperature_at_the_top_level(monkeypatch):
    """OpenAI takes temperature top-level; only the native API nests it."""
    use_ollama(monkeypatch, LLM_BASE_URL="https://ollama.com/v1")
    transport = OpenAITransport([VALID_TEXT])
    complete_json("hi", SCHEMA, temperature=0.3, client=transport)
    payload = transport.calls[0]["json"]
    assert payload["temperature"] == 0.3
    assert "options" not in payload


def test_a_v1_base_reads_the_text_from_choices(monkeypatch):
    use_ollama(monkeypatch, LLM_BASE_URL="https://ollama.com/v1")
    transport = OpenAITransport([VALID_TEXT])
    assert complete_json("hi", SCHEMA, client=transport)["value"] == 0.5


def test_a_v1_base_still_sends_the_bearer_token(monkeypatch):
    use_ollama(monkeypatch, LLM_BASE_URL="https://ollama.com/v1", LLM_API_KEY="cloud-key")
    transport = OpenAITransport([VALID_TEXT])
    complete_json("hi", SCHEMA, client=transport)
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer cloud-key"


def test_a_non_v1_base_still_uses_the_native_api(monkeypatch):
    """Regression: the local-daemon path must not move."""
    use_ollama(monkeypatch, LLM_BASE_URL="https://ollama.com")
    transport = OllamaTransport([VALID_TEXT])
    complete_json("hi", SCHEMA, client=transport)
    assert transport.calls[0]["url"] == "https://ollama.com/api/chat"
    assert transport.calls[0]["json"]["format"] == "json"


def test_a_v1_base_retries_a_schema_violation(monkeypatch):
    use_ollama(monkeypatch, LLM_BASE_URL="https://ollama.com/v1")
    transport = OpenAITransport([INVALID_TEXT, VALID_TEXT])
    assert complete_json("hi", SCHEMA, client=transport)["value"] == 0.5
    assert len(transport.calls) == 2


# --- which model actually answered -----------------------------------------
#
# Traces are shown on screen as evidence of persona reasoning. A trace that
# does not name the model that produced it is weaker evidence than it looks,
# and `slow_agent` records whatever the caller passed -- which is None whenever
# the model comes from LLM_MODEL rather than --model.


def test_resolve_model_prefers_an_explicit_override(monkeypatch):
    use_ollama(monkeypatch, LLM_MODEL="from-env")
    assert llm_client.resolve_model("from-the-caller") == "from-the-caller"


def test_resolve_model_falls_back_to_the_environment(monkeypatch):
    use_ollama(monkeypatch, LLM_MODEL="deepseek-v4-pro:cloud")
    assert llm_client.resolve_model(None) == "deepseek-v4-pro:cloud"


def test_resolve_model_falls_back_to_the_provider_default(monkeypatch):
    use_ollama(monkeypatch)
    assert llm_client.resolve_model(None) == llm_client.DEFAULT_OLLAMA_MODEL


def test_resolve_model_provider_default_differs_by_provider(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "k")
    assert llm_client.resolve_model(None) == llm_client.DEFAULT_MODEL


def test_complete_json_sends_exactly_what_resolve_model_reports(monkeypatch):
    """The recorded name and the sent name must not be able to drift apart."""
    use_ollama(monkeypatch, LLM_MODEL="deepseek-v4-pro:cloud")
    transport = OllamaTransport([VALID_TEXT])
    complete_json("hi", SCHEMA, client=transport)
    assert transport.calls[0]["json"]["model"] == llm_client.resolve_model(None)


# --- timeout ---------------------------------------------------------------

def test_the_request_timeout_defaults_to_the_module_constant(monkeypatch):
    use_ollama(monkeypatch)
    transport = OllamaTransport([VALID_TEXT])
    complete_json("hi", SCHEMA, client=transport)
    assert transport.calls[0]["timeout"] == llm_client.DEFAULT_TIMEOUT_S


def test_the_request_timeout_can_be_raised_from_the_environment(monkeypatch):
    """A hosted reasoning model can think for longer than 30 s.

    A trace run is hundreds of sequential calls; one timeout aborts the lot,
    so this must be tunable without editing the module.
    """
    use_ollama(monkeypatch, LLM_TIMEOUT_S="180")
    transport = OllamaTransport([VALID_TEXT])
    complete_json("hi", SCHEMA, client=transport)
    assert transport.calls[0]["timeout"] == 180.0


def test_an_unparsable_timeout_falls_back_to_the_default(monkeypatch):
    use_ollama(monkeypatch, LLM_TIMEOUT_S="soon")
    transport = OllamaTransport([VALID_TEXT])
    complete_json("hi", SCHEMA, client=transport)
    assert transport.calls[0]["timeout"] == llm_client.DEFAULT_TIMEOUT_S


# --- transport failures ----------------------------------------------------
#
# The retry loop handled JSON and schema failures and nothing else, so a single
# dropped connection killed the caller. That is not hypothetical: a real
# `slow_agent --all --n 20` run against Ollama Cloud died on
# `httpx.RemoteProtocolError: Server disconnected without sending a response`
# after one of four personas, losing the rest of a multi-hour run.


class FlakyTransport:
    """Raises `failures` transport errors, then answers normally."""

    def __init__(self, failures, texts, error=None):
        self._left = failures
        self._texts = list(texts)
        self._error = error or httpx.RemoteProtocolError("Server disconnected")
        self.calls = 0
        self.answered = 0

    def post(self, url, **kwargs):
        self.calls += 1
        if self._left > 0:
            self._left -= 1
            raise self._error
        text = self._texts[self.answered]
        self.answered += 1
        return FakeResponse({"message": {"role": "assistant", "content": text}})


def test_a_dropped_connection_is_retried_and_then_succeeds(monkeypatch):
    use_ollama(monkeypatch)
    transport = FlakyTransport(2, [VALID_TEXT])
    slept = []

    result = complete_json("hi", SCHEMA, client=transport, sleep=slept.append)

    assert result["value"] == 0.5
    assert transport.calls == 3
    assert len(slept) == 2, "a retry storm with no pause is not a retry"


def test_transport_retries_back_off_rather_than_hammering(monkeypatch):
    use_ollama(monkeypatch)
    slept = []
    complete_json("hi", SCHEMA, client=FlakyTransport(2, [VALID_TEXT]), sleep=slept.append)
    assert slept == sorted(slept) and slept[0] < slept[-1], f"not increasing: {slept}"


def test_transport_retries_are_bounded_and_then_raise(monkeypatch):
    use_ollama(monkeypatch)
    transport = FlakyTransport(99, [VALID_TEXT])

    with pytest.raises(LLMUnavailableError, match="transport"):
        complete_json("hi", SCHEMA, client=transport, sleep=lambda _s: None)

    assert transport.calls == llm_client.DEFAULT_TRANSPORT_RETRIES


def test_an_oserror_is_treated_as_a_transport_failure(monkeypatch):
    """A socket-level failure is the same class of problem as a dropped HTTP call."""
    use_ollama(monkeypatch)
    transport = FlakyTransport(1, [VALID_TEXT], error=OSError("connection reset"))
    assert complete_json("hi", SCHEMA, client=transport, sleep=lambda _s: None)["value"] == 0.5


def test_transport_retries_do_not_consume_the_schema_retry_budget(monkeypatch):
    """A flaky network must not eat the model's chances to fix its own JSON."""
    use_ollama(monkeypatch)
    # One drop, then two schema-invalid answers, then a good one. With a shared
    # budget of 3 this would raise; with separate budgets it succeeds.
    transport = FlakyTransport(1, [INVALID_TEXT, INVALID_TEXT, VALID_TEXT])

    result = complete_json("hi", SCHEMA, retries=3, client=transport,
                           sleep=lambda _s: None)

    assert result["value"] == 0.5


def test_the_raised_transport_error_is_one_report_py_already_catches(monkeypatch):
    """analytics/report.py catches LLMClientError; the headline must still degrade."""
    use_ollama(monkeypatch)
    with pytest.raises(llm_client.LLMClientError):
        complete_json("hi", SCHEMA, client=FlakyTransport(99, [VALID_TEXT]),
                      sleep=lambda _s: None)
