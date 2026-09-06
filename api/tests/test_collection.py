"""GET /collection/status -- what the real panel actually contains (S31).

`RESULTS.md` says "not yet collected" for every real-panel number, and until
now the only way to find out why - how many people had shopped, how many were
accepted, what they were rejected for - was to open `shoppertwin.db` in a SQL
client. An operator running a panel had no way to see it filling up, and the
first real session was rejected for being 29 seconds long against a 45-second
minimum without anybody noticing until afterwards.

This endpoint reports three separate things that are easy to conflate, and the
tests below exist mostly to keep them apart:

* **the live database** - everyone who has shopped, whatever became of them;
* **the committed corpus** in `data/sessions/anon/` - what `scripts/eval.py`
  actually reads, which is only the sessions someone has exported;
* **the prediction locks** in `predictions/` - the pre-registration evidence.

A panel that showed one number for all three would let an operator believe the
evidence had been committed when it had only been collected.
"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app.routers import collection


def _session(client: TestClient, session_id: str, **overrides):
    body = {
        "session_id": session_id,
        "variant_id": "A",
        "consent": True,
        "started_at": "2026-09-06T04:27:22.785Z",
        "screen_w": 1536,
        "screen_h": 864,
        "mode": "cursor_only",
        **overrides,
    }
    response = client.post("/sessions", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _finish(client: TestClient, session_id: str, accepted: bool, reason=None):
    body = {
        "ended_at": "2026-09-06T04:28:22.785Z",
        "quality": {"fixation_coverage": 0.0, "stations_visited": 3, "duration_s": 60.0},
        "accepted": accepted,
        "reject_reason": reason,
    }
    response = client.post(f"/sessions/{session_id}/finish", json=body)
    assert response.status_code == 200, response.text


@pytest.fixture(name="corpus", autouse=True)
def corpus_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the committed-corpus count at a tmp directory.

    The repository's own `data/sessions/anon/` is committed evidence; a test
    that counted it would change its answer every time the panel grew.
    """
    directory = tmp_path / "anon"
    directory.mkdir()
    monkeypatch.setattr(collection, "ANON_DIR", directory)
    return directory


def test_an_empty_panel_reports_zero_rather_than_nothing(client: TestClient) -> None:
    body = client.get("/collection/status").json()

    assert body["live"]["total"] == 0
    assert body["live"]["accepted"] == 0
    assert body["live"]["rejected"] == 0
    assert body["committed"]["sessions"] == 0


def test_counts_everyone_who_has_shopped(client: TestClient) -> None:
    _session(client, "11111111-1111-4111-8111-111111111111")
    _session(client, "22222222-2222-4222-8222-222222222222")

    body = client.get("/collection/status").json()

    assert body["live"]["total"] == 2
    # Neither has finished, so neither is accepted or rejected - they are
    # undecided, which is a third thing and not a rejection.
    assert body["live"]["undecided"] == 2


def test_separates_accepted_from_rejected(client: TestClient) -> None:
    _session(client, "11111111-1111-4111-8111-111111111111")
    _finish(client, "11111111-1111-4111-8111-111111111111", accepted=True)
    _session(client, "22222222-2222-4222-8222-222222222222")
    _finish(client, "22222222-2222-4222-8222-222222222222", accepted=False, reason="too_short")

    body = client.get("/collection/status").json()

    assert body["live"]["accepted"] == 1
    assert body["live"]["rejected"] == 1
    assert body["live"]["undecided"] == 0


def test_reports_why_sessions_were_rejected(client: TestClient) -> None:
    """The single most useful thing on this screen. The first real session was
    rejected as `too_short` and nobody found out until the run was over."""
    for n, reason in enumerate(["too_short", "too_short", "one_station"]):
        session_id = f"{n}{n}{n}{n}{n}{n}{n}{n}-1111-4111-8111-111111111111"
        _session(client, session_id)
        _finish(client, session_id, accepted=False, reason=reason)

    body = client.get("/collection/status").json()

    assert body["live"]["reject_reasons"] == {"too_short": 2, "one_station": 1}


def test_counts_sessions_that_declined_consent_separately(client: TestClient) -> None:
    """A session with `consent: false` is never exported in any form, so it can
    never enter the panel and must not be counted as a rejection - the reject
    histogram under-reports `no_consent` by construction."""
    _session(client, "11111111-1111-4111-8111-111111111111", consent=False)

    body = client.get("/collection/status").json()

    assert body["live"]["no_consent"] == 1
    assert body["live"]["rejected"] == 0


def test_distinguishes_collected_from_committed(client: TestClient, corpus: Path) -> None:
    """Collected is not committed. `scripts/eval.py` reads only the exported
    corpus, so a panel that showed one number for both would let an operator
    believe the evidence was in the repository when it was still in SQLite.
    """
    _session(client, "11111111-1111-4111-8111-111111111111")
    _finish(client, "11111111-1111-4111-8111-111111111111", accepted=True)

    body = client.get("/collection/status").json()

    assert body["live"]["accepted"] == 1
    assert body["committed"]["sessions"] == 0
    assert body["export_needed"] is True


def test_says_no_export_is_needed_when_the_corpus_matches(
    client: TestClient, corpus: Path
) -> None:
    _session(client, "11111111-1111-4111-8111-111111111111")
    _finish(client, "11111111-1111-4111-8111-111111111111", accepted=True)
    (corpus / "11111111-1111-4111-8111-111111111111.json").write_text(
        json.dumps({"session": {}, "events": []}), encoding="utf-8"
    )

    body = client.get("/collection/status").json()

    assert body["export_needed"] is False


def test_names_the_command_that_exports(client: TestClient) -> None:
    """A status that says work is needed and not what to run is half a message."""
    body = client.get("/collection/status").json()

    assert "anonymise_sessions" in body["export_command"]
    assert "eval" in body["eval_command"]


def test_reports_what_acceptance_requires(client: TestClient) -> None:
    """The thresholds, so a rejection is diagnosable from this screen rather
    than by reading web/src/capture/SessionGate.ts."""
    body = client.get("/collection/status").json()

    assert body["gate"]["min_duration_s"] == 45
    assert body["gate"]["min_stations"] == 2
    assert body["gate"]["min_interactions"] == 1
    assert body["gate"]["min_fixation_coverage"] == 0.4


def test_reports_how_many_locks_exist(client: TestClient, predictions_dir: Path) -> None:
    """Counted from the directory the server actually writes to.

    The first version of this test passed against a private constant in the
    router that pointed at the repository's real `predictions/` - which happened
    to hold exactly one file. It would have started failing the moment a second
    lock was committed, for a reason nothing to do with the panel.
    """
    assert not list(predictions_dir.glob("*.json"))

    _session(client, "11111111-1111-4111-8111-111111111111")

    body = client.get("/collection/status").json()

    assert body["locks"] == 1
    assert len(list(predictions_dir.glob("*.json"))) == 1


def test_rehearsal_locks_are_not_counted_as_evidence(
    client: TestClient, predictions_dir: Path
) -> None:
    """`predictions/dev/` is gitignored and invisible to scripts/eval.py, so a
    rehearsal run must not make the panel look better evidenced than it is."""
    _session(client, "11111111-1111-4111-8111-111111111111", consent=False)

    body = client.get("/collection/status").json()

    assert body["locks"] == 0
