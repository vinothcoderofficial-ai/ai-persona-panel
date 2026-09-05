"""Load `.env` into the process environment.

`.env.example` says "Copy to .env and fill in", and until now nothing on the
Python side read the result: `python-dotenv` is not a dependency, so a filled-in
`.env` did nothing and `LLM_PROVIDER=ollama` in that file silently fell back to
the Anthropic default. This closes that trap without adding a dependency --
CLAUDE.md pins the dependency list and asks before anything is added, and a
key-value file does not justify one.

Deliberately small. It supports what `.env.example` actually contains:
`KEY=value`, `#` comments, blank lines, an optional `export` prefix, and
surrounding quotes. It does not do variable interpolation, multi-line values or
`.env.local` layering. If this file ever needs those, that is the moment to
reach for a real library rather than growing this one.

Two rules worth stating, because both prevent a specific bug:

  * **A real environment variable always wins.** Anything else and an exported
    value would be silently replaced by a stale one in a checked-out `.env`.
  * **An empty value means "not set", not "empty string".** `.env.example`
    ships `LLM_API_KEY=`, and treating that as an empty string would shadow an
    exported key with nothing.

Top-level on purpose: `sim/llm_client.py` and `api/app/db.py` both read the
environment and live in different packages that must not depend on each other.
"""
from __future__ import annotations

import os
import pathlib

DEFAULT_ENV_PATH = pathlib.Path(__file__).resolve().parent / ".env"

_QUOTE_PAIRS = (('"', '"'), ("'", "'"))


def _unquote(value: str) -> str:
    for opening, closing in _QUOTE_PAIRS:
        if len(value) >= 2 and value.startswith(opening) and value.endswith(closing):
            return value[1:-1]
    return value


def parse(text: str) -> dict:
    """Every `KEY=value` pair in `text`, in file order.

    A line that is blank, a comment, or has no `=` is skipped rather than
    raising: a stray line in a config file must not stop the process starting.
    """
    found: dict = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, separator, value = line.partition("=")
        if not separator:
            continue
        key = key.strip()
        if not key:
            continue
        found[key] = _unquote(value.strip())
    return found


def load(path=None) -> dict:
    """Set anything in `path` that is not already in the environment.

    Returns only the keys it actually set, so a caller can report what came
    from the file rather than guessing. A missing file is a silent no-op --
    running without a `.env` is the normal case, not an error.
    """
    path = pathlib.Path(path) if path is not None else DEFAULT_ENV_PATH
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}

    applied: dict = {}
    for key, value in parse(text).items():
        # Empty means "not set": see the module docstring.
        if not value:
            continue
        if os.environ.get(key):
            continue
        os.environ[key] = value
        applied[key] = value
    return applied
