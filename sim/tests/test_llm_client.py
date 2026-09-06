"""sim/llm_client.py: complete_json talks to the LLM and validates the result (SPEC M4, S12).

Everything here runs against a fake transport -- never a real network call. `complete_json`'s
only job is: build the request, extract the model's text, parse it as JSON, validate it against
the caller's schema, retry with the validation error appended on failure, and raise a clear
exception when the retry budget is exhausted. It does not know about personas, brands, or the
cache -- that is sim/policy.py's job.
"""
from __future__ import annotations

import json as json_module

import httpx
import pytest

from sim.llm_client import (
    LLMClientError,
    LLMHttpError,
    LLMUnavailableError,
    LLMValidationError,
    complete_json,
)

# A small, self-contained schema -- deliberately not policy.schema.json, so these tests exercise
# complete_json's own contract without depending on sim/policy.py's shape.
SCHEMA = {
    "type": "object",
    "required": ["persona_id", "value"],
    "additionalProperties": False,
    "properties": {
        "persona_id": {"type": "string"},
        "value": {"type": "number"},
    },
}

VALID_BODY = {"persona_id": "mission", "value": 0.5}
VALID_TEXT = json_module.dumps(VALID_BODY)
SCHEMA_INVALID_TEXT = json_module.dumps({"persona_id": "mission"})  # missing "value"
NON_JSON_TEXT = "sorry, I can't help with that request"


class FakeResponse:
    """Stands in for httpx.Response: a `.json()` body plus a no-op `.raise_for_status()`."""

    def __init__(self, body: dict):
        self._body = body

    def json(self) -> dict:
        return self._body

    def raise_for_status(self) -> None:
        pass


class FakeTransport:
    """Records every call it receives and replays canned model outputs in order.

    Exposes `.post(url, **kwargs)` -- the same call shape `complete_json` uses against the real
    `httpx` module -- so tests never monkeypatch httpx internals.
    """

    def __init__(self, texts: list[str]):
        self._texts = list(texts)
        self.calls: list[dict] = []

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        text = self._texts[len(self.calls) - 1]
        return FakeResponse({"content": [{"type": "text", "text": text}]})


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    """Pin the whole LLM configuration these tests assume.

    Every test here injects its own transport, so a real key is never used --
    but complete_json still requires *something* configured before it will
    build a request, and these tests assert the **Anthropic** shape.

    LLM_PROVIDER, LLM_BASE_URL and LLM_MODEL are cleared rather than left
    alone, because `sim/llm_client.py` loads `.env` at import: a developer with
    a working `.env` (say `LLM_PROVIDER=ollama` for the persona traces) would
    otherwise see this file fail for a reason that has nothing to do with the
    code. Found exactly that way, with a real Ollama `.env` in place.
    """
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_OFFLINE", "0")
    for name in ("LLM_PROVIDER", "LLM_BASE_URL", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)


def test_valid_response_on_first_call_returns_parsed_dict_and_calls_once():
    transport = FakeTransport([VALID_TEXT])
    result = complete_json("describe the persona", SCHEMA, client=transport)
    assert result == VALID_BODY
    assert len(transport.calls) == 1


def test_two_schema_invalid_responses_then_valid_returns_after_exactly_three_calls():
    transport = FakeTransport([SCHEMA_INVALID_TEXT, SCHEMA_INVALID_TEXT, VALID_TEXT])
    result = complete_json("describe the persona", SCHEMA, client=transport, retries=3)
    assert result == VALID_BODY
    assert len(transport.calls) == 3


def test_always_schema_invalid_raises_after_exactly_retries_attempts_naming_the_violation():
    transport = FakeTransport([SCHEMA_INVALID_TEXT, SCHEMA_INVALID_TEXT, SCHEMA_INVALID_TEXT])
    with pytest.raises(LLMValidationError) as exc_info:
        complete_json("describe the persona", SCHEMA, client=transport, retries=3)
    assert len(transport.calls) == 3
    # SCHEMA_INVALID_TEXT is missing "value" -- the raised error must name that violation.
    assert "value" in str(exc_info.value)


def test_non_json_text_is_retried_not_crashed_on():
    transport = FakeTransport([NON_JSON_TEXT, NON_JSON_TEXT, VALID_TEXT])
    result = complete_json("describe the persona", SCHEMA, client=transport, retries=3)
    assert result == VALID_BODY
    assert len(transport.calls) == 3


def test_retry_prompt_includes_the_previous_validation_error():
    transport = FakeTransport([SCHEMA_INVALID_TEXT, VALID_TEXT])
    complete_json("ORIGINAL PROMPT MARKER", SCHEMA, client=transport, retries=3)
    assert len(transport.calls) == 2
    second_request_content = transport.calls[1]["json"]["messages"][0]["content"]
    assert "ORIGINAL PROMPT MARKER" in second_request_content
    # The missing-property error from the first attempt must be fed back to the model.
    assert "value" in second_request_content


def test_temperature_defaults_to_zero_in_the_request_payload():
    transport = FakeTransport([VALID_TEXT])
    complete_json("describe the persona", SCHEMA, client=transport)
    assert transport.calls[0]["json"]["temperature"] == 0


def test_explicit_temperature_is_forwarded_in_the_request_payload():
    transport = FakeTransport([VALID_TEXT])
    complete_json("describe the persona", SCHEMA, client=transport, temperature=0.7)
    assert transport.calls[0]["json"]["temperature"] == 0.7


def test_llm_offline_raises_without_attempting_a_call(monkeypatch):
    monkeypatch.setenv("LLM_OFFLINE", "1")
    transport = FakeTransport([VALID_TEXT])
    with pytest.raises(LLMUnavailableError):
        complete_json("describe the persona", SCHEMA, client=transport)
    assert transport.calls == []


def test_no_api_key_and_no_injected_client_raises_without_attempting_a_call(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with pytest.raises(LLMUnavailableError):
        complete_json("describe the persona", SCHEMA)


# ---------------------------------------------------------------------------
# The provider answered, and refused (S26)
# ---------------------------------------------------------------------------

class RefusingResponse:
    """A real `httpx.Response` for an error status, in the one respect that matters.

    `httpx.Response.raise_for_status()` raises `HTTPStatusError`, and before
    `LLMHttpError` existed that exception escaped `complete_json` untranslated -
    so a wrong API key surfaced as an httpx traceback in a CLI run, and as an
    opaque `500 Internal Server Error` on the AI panel, which is the exact
    "unavailable, cause unknown" failure that screen exists to prevent.
    """

    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text

    def json(self) -> dict:  # pragma: no cover - never reached; the raise comes first
        return {}

    def raise_for_status(self) -> None:
        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        response = httpx.Response(self.status_code, text=self.text, request=request)
        response.raise_for_status()


class RefusingTransport:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text
        self.calls: list[dict] = []

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return RefusingResponse(self.status_code, self.text)


def test_http_error_is_translated_rather_than_escaping_as_httpx():
    transport = RefusingTransport(401)

    with pytest.raises(LLMHttpError) as excinfo:
        complete_json("prompt", SCHEMA, client=transport)

    assert excinfo.value.status_code == 401


def test_http_error_is_an_llm_client_error():
    """Callers that catch the module's own base class must catch this too."""
    with pytest.raises(LLMClientError):
        complete_json("prompt", SCHEMA, client=RefusingTransport(500))


def test_a_rejected_credential_names_the_setting_to_fix():
    with pytest.raises(LLMHttpError) as excinfo:
        complete_json("prompt", SCHEMA, client=RefusingTransport(401))

    message = str(excinfo.value)
    assert "401" in message
    assert "LLM_API_KEY" in message


def test_an_unknown_model_names_the_model_setting():
    with pytest.raises(LLMHttpError) as excinfo:
        complete_json("prompt", SCHEMA, client=RefusingTransport(404))

    assert "LLM_MODEL" in str(excinfo.value)


def test_a_rate_limit_says_so():
    with pytest.raises(LLMHttpError) as excinfo:
        complete_json("prompt", SCHEMA, client=RefusingTransport(429))

    assert "rate" in str(excinfo.value).lower()


def test_a_refusal_is_not_retried():
    """A 401 is a wrong key; asking again three times just spends three times
    as long being refused. Only schema failures earn a retry."""
    transport = RefusingTransport(401)

    with pytest.raises(LLMHttpError):
        complete_json("prompt", SCHEMA, client=transport, retries=3)

    assert len(transport.calls) == 1


def test_the_message_carries_the_providers_own_body():
    """Whatever the service said is the most specific evidence available, and
    guessing from the status code alone throws it away."""
    with pytest.raises(LLMHttpError) as excinfo:
        complete_json(
            "prompt",
            SCHEMA,
            client=RefusingTransport(402, '{"error":"insufficient credits"}'),
        )

    assert "insufficient credits" in str(excinfo.value)
