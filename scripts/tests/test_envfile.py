"""Tests for envfile.py -- the .env loader.

Placed here rather than beside a package because `envfile.py` is a top-level
module: it is imported by both `sim/llm_client.py` and `api/app/db.py`, which
sit in different packages and must not depend on each other. `scripts/tests/`
is already collected by pytest.ini and already holds tests for tooling that
spans packages.

The rule that matters most is that a real environment variable always beats the
file. Anything else and an exported key would be silently replaced by a stale
one in a checked-out .env, which is the kind of bug that costs an afternoon.
"""
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import envfile  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_environment():
    """Undo everything `envfile.load()` writes.

    These tests call the real loader, which mutates `os.environ` directly.
    monkeypatch cannot undo that -- it only reverts what monkeypatch itself
    set -- so without this, `LLM_PROVIDER=ollama` from the integration test
    below leaks into the rest of the session and every test in
    sim/tests/test_llm_client.py that assumes the Anthropic default fails.
    Found exactly that way.
    """
    before = dict(os.environ)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(before)


def write(tmp_path, text):
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_a_simple_assignment(tmp_path, monkeypatch):
    monkeypatch.delenv("SHOPPERTWIN_T1", raising=False)
    envfile.load(write(tmp_path, "SHOPPERTWIN_T1=hello\n"))
    assert os.environ["SHOPPERTWIN_T1"] == "hello"


def test_a_real_environment_variable_wins_over_the_file(tmp_path, monkeypatch):
    """Exporting a value must not be undone by a stale .env."""
    monkeypatch.setenv("SHOPPERTWIN_T2", "from-the-shell")
    envfile.load(write(tmp_path, "SHOPPERTWIN_T2=from-the-file\n"))
    assert os.environ["SHOPPERTWIN_T2"] == "from-the-shell"


def test_comments_and_blank_lines_are_ignored(tmp_path, monkeypatch):
    monkeypatch.delenv("SHOPPERTWIN_T3", raising=False)
    loaded = envfile.load(write(tmp_path, "# a comment\n\n  \nSHOPPERTWIN_T3=v\n"))
    assert loaded == {"SHOPPERTWIN_T3": "v"}


def test_surrounding_quotes_are_stripped(tmp_path, monkeypatch):
    monkeypatch.delenv("SHOPPERTWIN_T4", raising=False)
    monkeypatch.delenv("SHOPPERTWIN_T5", raising=False)
    envfile.load(write(tmp_path, 'SHOPPERTWIN_T4="double"\nSHOPPERTWIN_T5=\'single\'\n'))
    assert os.environ["SHOPPERTWIN_T4"] == "double"
    assert os.environ["SHOPPERTWIN_T5"] == "single"


def test_an_export_prefix_is_accepted(tmp_path, monkeypatch):
    monkeypatch.delenv("SHOPPERTWIN_T6", raising=False)
    envfile.load(write(tmp_path, "export SHOPPERTWIN_T6=v\n"))
    assert os.environ["SHOPPERTWIN_T6"] == "v"


def test_a_value_containing_equals_is_kept_whole(tmp_path, monkeypatch):
    """Base URLs and keys carry '='; splitting on every one would truncate them."""
    monkeypatch.delenv("SHOPPERTWIN_T7", raising=False)
    envfile.load(write(tmp_path, "SHOPPERTWIN_T7=a=b=c\n"))
    assert os.environ["SHOPPERTWIN_T7"] == "a=b=c"


def test_whitespace_around_the_key_and_value_is_trimmed(tmp_path, monkeypatch):
    monkeypatch.delenv("SHOPPERTWIN_T8", raising=False)
    envfile.load(write(tmp_path, "  SHOPPERTWIN_T8  =   spaced   \n"))
    assert os.environ["SHOPPERTWIN_T8"] == "spaced"


def test_an_empty_value_is_treated_as_unset_not_as_empty_string(tmp_path, monkeypatch):
    """.env.example ships `LLM_API_KEY=`; that must not shadow an exported key."""
    monkeypatch.setenv("SHOPPERTWIN_T9", "real-key")
    envfile.load(write(tmp_path, "SHOPPERTWIN_T9=\n"))
    assert os.environ["SHOPPERTWIN_T9"] == "real-key"


def test_an_empty_value_does_not_create_the_variable(tmp_path, monkeypatch):
    monkeypatch.delenv("SHOPPERTWIN_T10", raising=False)
    loaded = envfile.load(write(tmp_path, "SHOPPERTWIN_T10=\n"))
    assert "SHOPPERTWIN_T10" not in loaded
    assert "SHOPPERTWIN_T10" not in os.environ


def test_a_missing_file_is_a_silent_no_op(tmp_path):
    assert envfile.load(tmp_path / "nope.env") == {}


def test_a_malformed_line_is_skipped_not_fatal(tmp_path, monkeypatch):
    """A stray line must not stop the process from starting."""
    monkeypatch.delenv("SHOPPERTWIN_T11", raising=False)
    loaded = envfile.load(write(tmp_path, "this line has no equals sign\nSHOPPERTWIN_T11=v\n"))
    assert loaded == {"SHOPPERTWIN_T11": "v"}


def test_load_returns_only_what_it_actually_set(tmp_path, monkeypatch):
    monkeypatch.setenv("SHOPPERTWIN_T12", "shell")
    monkeypatch.delenv("SHOPPERTWIN_T13", raising=False)
    loaded = envfile.load(write(tmp_path, "SHOPPERTWIN_T12=file\nSHOPPERTWIN_T13=file\n"))
    assert loaded == {"SHOPPERTWIN_T13": "file"}


def test_loading_twice_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.delenv("SHOPPERTWIN_T14", raising=False)
    path = write(tmp_path, "SHOPPERTWIN_T14=v\n")
    first = envfile.load(path)
    second = envfile.load(path)
    assert first == {"SHOPPERTWIN_T14": "v"}
    # Already in the environment the second time, so nothing is set again.
    assert second == {}
    assert os.environ["SHOPPERTWIN_T14"] == "v"


def test_the_llm_client_sees_values_from_the_file(tmp_path, monkeypatch):
    """The end this exists for: sim/llm_client.py reads what .env carries."""
    from sim import llm_client

    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    envfile.load(write(tmp_path, "LLM_PROVIDER=ollama\n"))
    assert llm_client.resolve_provider() == "ollama"
