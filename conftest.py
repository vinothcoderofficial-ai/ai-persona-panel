"""Session-wide test setup.

One job: **the test suite must not depend on the developer's `.env`.**

`sim/llm_client.py` loads `.env` at import (see `envfile.py`), which is what
makes `LLM_PROVIDER=ollama` work for generating persona traces. The side effect
is that anyone with a working `.env` would import that configuration into every
test, and tests asserting the Anthropic default — `sim/tests/test_llm_client.py`
and `analytics/tests/test_report.py` among them — would fail for a reason that
has nothing to do with the code they cover. Found exactly that way, with a real
Ollama `.env` in place: green before it existed, four red files after.

So the ambient LLM configuration is cleared once, at session start, and any
test that needs a value sets it explicitly with monkeypatch. Tests that
exercise the loader itself (`scripts/tests/test_envfile.py`) pass an explicit
path and are unaffected.

This only *removes* ambient configuration; it does not impose any. Setting
`LLM_OFFLINE=1` here was tried and rejected — it is configuration too, and it
changed the behaviour several tests were written to exercise. No test contacts
a real model regardless, because every one of them injects a fake transport.
"""
import os

import pytest

# Everything sim/llm_client.py reads from the environment.
_LLM_VARS = ("LLM_PROVIDER", "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "LLM_OFFLINE")


@pytest.fixture(autouse=True, scope="session")
def _neutral_llm_environment():
    saved = {name: os.environ.get(name) for name in _LLM_VARS}
    for name in _LLM_VARS:
        os.environ.pop(name, None)
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
