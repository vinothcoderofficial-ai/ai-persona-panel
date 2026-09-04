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
from typing import Any

import httpx
from jsonschema import Draft7Validator

DEFAULT_BASE_URL = "https://api.anthropic.com/v1"
# Mirrors the default in .env.example -- keep the two in sync if that file changes.
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 1024
DEFAULT_TIMEOUT_S = 30.0


class LLMClientError(Exception):
    """Base class for every error this module raises."""


class LLMUnavailableError(LLMClientError):
    """The LLM cannot be contacted at all: `LLM_OFFLINE=1`, or no API key and no injected client.

    Callers that want offline behaviour (serving a cache instead of failing the request) should
    catch this specifically -- `sim/policy.py` does exactly that.
    """


class LLMValidationError(LLMClientError):
    """The model never produced schema-valid JSON within the retry budget."""


def _is_offline() -> bool:
    return os.environ.get("LLM_OFFLINE", "0").strip() == "1"


def _extract_text(body: Any) -> str:
    """Pull the assistant's text out of a Messages API response body.

    Raises (KeyError/IndexError/TypeError) on a malformed envelope, which the caller folds into
    the same retry path as "the model's text was not valid JSON" -- both mean this attempt did
    not produce a usable completion.
    """
    for block in body["content"]:
        if block.get("type") == "text":
            return block["text"]
    raise ValueError("LLM response contained no text content block")


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

    api_key = os.environ.get("LLM_API_KEY", "")
    if client is None and not api_key:
        raise LLMUnavailableError(
            "LLM_API_KEY is not set and no client was injected. Set LLM_API_KEY in .env, "
            "or set LLM_OFFLINE=1 to serve cached results instead."
        )

    base_url = os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    model_name = model or os.environ.get("LLM_MODEL") or DEFAULT_MODEL
    transport = client if client is not None else httpx
    url = f"{base_url}/messages"
    headers = {
        "content-type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
    }
    validator = Draft7Validator(schema)

    current_prompt = prompt
    last_error = "no attempts were made"

    for _ in range(retries):
        payload = {
            "model": model_name,
            "max_tokens": DEFAULT_MAX_TOKENS,
            "temperature": temperature,
            "messages": [{"role": "user", "content": current_prompt}],
        }
        response = transport.post(url, json=payload, headers=headers, timeout=DEFAULT_TIMEOUT_S)
        raise_for_status = getattr(response, "raise_for_status", None)
        if callable(raise_for_status):
            raise_for_status()

        try:
            body = response.json()
            text = _extract_text(body)
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
