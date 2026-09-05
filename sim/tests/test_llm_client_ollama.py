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
