"""Talk to the LLM and validate the result (SPEC M4, S12).

This module's single responsibility is `complete_json`: turn a text prompt into a JSON object
that validates against a caller-supplied JSON Schema, retrying with the model's own mistake fed
back to it when it gets the shape wrong. It knows nothing about personas, planograms, brands, or
the on-disk cache -- that split belongs to the caller (`sim/policy.py`), which is why offline mode
raises here instead of silently serving a cached file: "the caller serves the cache, not this
module."

Transport is injectable so tests never need to monkeypatch httpx internals. `client` (or the
`httpx` module itself, by default) only needs a `post(url, **kwargs)` method that accepts the
same keyword arguments `httpx.post` does (`json=`, `headers=`, `timeout=`) and returns an object
with `.json()` and `.raise_for_status()`.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
from typing import Any

import httpx
from jsonschema import Draft7Validator

_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:  # the pattern scripts/ already uses
    sys.path.insert(0, str(_ROOT))

import envfile  # noqa: E402

# Load `.env` once, at import, so LLM_PROVIDER / LLM_MODEL / LLM_API_KEY can be
# configured the way .env.example has always claimed they could. Once rather
# than per call is deliberate: a test that clears one of these must stay
# cleared. A real environment variable always beats the file (envfile.py).
envfile.load()

DEFAULT_BASE_URL = "https://api.anthropic.com/v1"
# Mirrors the default in .env.example -- keep the two in sync if that file changes.
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 1024
DEFAULT_TIMEOUT_S = 30.0

# Providers. `LLM_PROVIDER` selects one; Anthropic stays the default so no
# existing configuration changes meaning.
PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_OLLAMA = "ollama"
PROVIDERS = (PROVIDER_ANTHROPIC, PROVIDER_OLLAMA)

# The on-the-wire request/response shape, which is not one-to-one with the
# provider: ollama.com serves both its native API and an OpenAI-compatible one.
WIRE_ANTHROPIC = "anthropic"
WIRE_OLLAMA = "ollama"
WIRE_OPENAI = "openai"

# Ollama's daemon listens here and needs no key. This is what makes the S13
# persona traces producible without an Anthropic account.
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "llama3.1:8b"


class LLMClientError(Exception):
    """Base class for every error this module raises."""


class LLMConfigError(LLMClientError):
    """The configuration names something this module cannot honour."""


class LLMUnavailableError(LLMClientError):
    """The LLM cannot be contacted at all: `LLM_OFFLINE=1`, or no API key and no injected client.

    Callers that want offline behaviour (serving a cache instead of failing the request) should
    catch this specifically -- `sim/policy.py` does exactly that.
    """


class LLMValidationError(LLMClientError):
    """The model never produced schema-valid JSON within the retry budget."""


def _is_offline() -> bool:
    return os.environ.get("LLM_OFFLINE", "0").strip() == "1"


def resolve_provider() -> str:
    """The configured provider name, validated.

    Unknown names are refused rather than silently falling back to Anthropic: a
    typo in `LLM_PROVIDER` would otherwise send an Ollama-shaped workload to
    api.anthropic.com with whatever key happened to be in the environment.
    """
    name = (os.environ.get("LLM_PROVIDER") or PROVIDER_ANTHROPIC).strip().lower()
    if not name:
        return PROVIDER_ANTHROPIC
    if name not in PROVIDERS:
        raise LLMConfigError(
            f"LLM_PROVIDER={name!r} is not supported; expected one of "
            f"{', '.join(PROVIDERS)}."
        )
    return name


def _extract_text(body: Any, wire: str = WIRE_ANTHROPIC) -> str:
    """Pull the assistant's text out of a response body in `wire` format.

    Raises (KeyError/IndexError/TypeError/ValueError) on a malformed envelope, which the caller
    folds into the same retry path as "the model's text was not valid JSON" -- both mean this
    attempt did not produce a usable completion.
    """
    if wire == WIRE_OLLAMA:
        # /api/chat with stream=false: {"message": {"role": ..., "content": ...}}
        return body["message"]["content"]

    if wire == WIRE_OPENAI:
        # /v1/chat/completions: {"choices": [{"message": {"content": ...}}]}
        return body["choices"][0]["message"]["content"]

    for block in body["content"]:
        if block.get("type") == "text":
            return block["text"]
    raise ValueError("LLM response contained no text content block")


def _request_for(provider: str, *, model: str, prompt: str, temperature: float, api_key: str):
    """The (url, headers, payload, wire) this provider expects."""
    if provider == PROVIDER_OLLAMA:
        base_url = (os.environ.get("LLM_BASE_URL") or DEFAULT_OLLAMA_BASE_URL).rstrip("/")
        headers = {"content-type": "application/json"}
        # A local daemon takes no key. Ollama Cloud does, as a bearer token.
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        # ollama.com serves both APIs: the native one at the root and an
        # OpenAI-compatible one under /v1. A base ending in /v1 is the caller
        # asking for the second, and posting {base}/api/chat there would 404.
        # Both shapes were checked against the live service before this branch
        # was written; both honour a JSON-mode request.
        if base_url.endswith("/v1"):
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "response_format": {"type": "json_object"},
                "temperature": temperature,
            }
            return f"{base_url}/chat/completions", headers, payload, WIRE_OPENAI

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            # A streamed reply would not survive `.json()`, and `format: json`
            # stops the model wrapping its object in prose -- which is the most
            # common way a small local model burns a retry.
            "stream": False,
            "format": "json",
            "options": {"temperature": temperature},
        }
        return f"{base_url}/api/chat", headers, payload, WIRE_OLLAMA

    base_url = (os.environ.get("LLM_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    headers = {
        "content-type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
    }
    payload = {
        "model": model,
        "max_tokens": DEFAULT_MAX_TOKENS,
        "temperature": temperature,
        "messages": [{"role": "user", "content": prompt}],
    }
    return f"{base_url}/messages", headers, payload, WIRE_ANTHROPIC


def resolve_timeout() -> float:
    """Per-request timeout in seconds, `LLM_TIMEOUT_S` overriding the default.

    A hosted reasoning model can think for longer than 30 s, and a trace run is
    hundreds of sequential calls -- one timeout aborts the whole run. An
    unparsable value falls back to the default rather than crashing: a bad
    number in a config file should not stop the process starting.
    """
    raw = os.environ.get("LLM_TIMEOUT_S", "").strip()
    if not raw:
        return DEFAULT_TIMEOUT_S
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT_S
    return value if value > 0 else DEFAULT_TIMEOUT_S


def _default_model_for(provider: str) -> str:
    return DEFAULT_OLLAMA_MODEL if provider == PROVIDER_OLLAMA else DEFAULT_MODEL


def resolve_model(model: str | None = None) -> str:
    """The model `complete_json` would actually send, given the same override.

    Public because callers need to *record* it. `sim/slow_agent.py` writes the
    model into every trace, and those traces are shown on screen as evidence of
    persona reasoning; recording the caller's override meant recording `None`
    whenever the model came from `LLM_MODEL`, which is the normal case. A trace
    that cannot name the model that produced it is weaker evidence than it
    looks.
    """
    return model or os.environ.get("LLM_MODEL") or _default_model_for(resolve_provider())


def _validation_error_summary(errors: list) -> str:
    return "; ".join(
        f"{'/'.join(str(p) for p in e.path)}: {e.message}" if e.path else e.message
        for e in errors
    )


def _with_correction(prompt: str, error: str) -> str:
    return (
        f"{prompt}\n\n"
        f"Your previous response was invalid: {error}\n"
        "Respond again with ONLY the corrected JSON, matching the schema exactly."
    )


def complete_json(
    prompt: str,
    schema: dict,
    *,
    model: str | None = None,
    temperature: float = 0.0,
    retries: int = 3,
    client: Any = None,
) -> dict:
    """Complete `prompt`, returning JSON that validates against `schema`.

    Posts to `{LLM_BASE_URL}/messages` (Anthropic Messages API shape) with a single user-turn
    message. On invalid JSON or a schema violation, the validation error is appended to the
    prompt and the request is retried, up to `retries` attempts total. Raises `LLMValidationError`
    if no attempt succeeds.

    Raises `LLMUnavailableError` immediately, before any request is attempted, when
    `LLM_OFFLINE=1` or when no `client` was injected and no `LLM_API_KEY` is configured.
    """
    if _is_offline():
        raise LLMUnavailableError(
            "LLM_OFFLINE=1: complete_json will not contact the LLM. "
            "The caller is expected to serve a cached result instead."
        )

    provider = resolve_provider()
    api_key = os.environ.get("LLM_API_KEY", "")
    # Only Anthropic needs a key. A local Ollama daemon has none, and demanding
    # one there would block the only path to persona traces without an account.
    if provider == PROVIDER_ANTHROPIC and client is None and not api_key:
        raise LLMUnavailableError(
            "LLM_API_KEY is not set and no client was injected. Set LLM_API_KEY in .env, "
            "or set LLM_PROVIDER=ollama to use a local model, "
            "or set LLM_OFFLINE=1 to serve cached results instead."
        )

    model_name = resolve_model(model)
    transport = client if client is not None else httpx
    timeout_s = resolve_timeout()
    validator = Draft7Validator(schema)

    current_prompt = prompt
    last_error = "no attempts were made"

    for _ in range(retries):
        url, headers, payload, wire = _request_for(
            provider,
            model=model_name,
            prompt=current_prompt,
            temperature=temperature,
            api_key=api_key,
        )
        response = transport.post(url, json=payload, headers=headers, timeout=timeout_s)
        raise_for_status = getattr(response, "raise_for_status", None)
        if callable(raise_for_status):
            raise_for_status()

        try:
            body = response.json()
            text = _extract_text(body, wire)
            data = json.loads(text)
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            last_error = f"response was not valid JSON: {exc}"
            current_prompt = _with_correction(current_prompt, last_error)
            continue

        errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
        if not errors:
            return data

        last_error = _validation_error_summary(errors)
        current_prompt = _with_correction(current_prompt, last_error)

    raise LLMValidationError(
        f"complete_json: no schema-valid JSON after {retries} attempt(s). "
        f"Last error: {last_error}"
    )
