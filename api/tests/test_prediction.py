"""S14 - the prediction lock: POST /sessions writes it before any event is accepted.

This is the project's central scientific claim: the synthetic prediction was
fixed *before* the human shopped. These tests check the ordering
structurally, not by convention - a session row cannot exist without its
lock, and an event posted for a session with no lock is refused.

The `predictions_dir` fixture in conftest.py points api.app.prediction at a
per-test tmp directory, so nothing here ever writes into the repository's
real `predictions/` folder (those files are committed evidence).
"""
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from jsonschema import Draft7Validator
from sqlmodel import Session

from api.app import prediction as prediction_module
from api.app.db import SessionRecord

ROOT = Path(__file__).resolve().parents[2]


def valid_session_body(variant_id: str = "A", mode: str = "cursor_only") -> dict:
    return {
        "session_id": str(uuid.uuid4()),
        "variant_id": variant_id,
        "consent": True,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "screen_w": 1440,
        "screen_h": 900,
        "mode": mode,
    }


def prediction_validator() -> Draft7Validator:
    schema = json.loads(
        (ROOT / "schemas" / "prediction.schema.json").read_text(encoding="utf-8")
    )
    return Draft7Validator(schema)


def create_session(client, variant_id: str = "A") -> tuple[str, dict]:
    body = valid_session_body(variant_id)
    resp = client.post("/sessions", json=body)
    assert resp.status_code == 201, resp.text
    return body["session_id"], resp.json()


def read_lock(predictions_dir: Path, session_id: str) -> dict:
    return json.loads((predictions_dir / f"{session_id}.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# THE ORDERING TEST (CLAUDE.md's non-negotiable rule, docs/PLAN.md S14)
# ---------------------------------------------------------------------------


def test_lock_is_written_before_any_event_is_accepted(client, predictions_dir):
    session_id, _created = create_session(client)
    lock_file = predictions_dir / f"{session_id}.json"

    # The lock exists the moment POST /sessions has returned - before this test
    # (or a shopper) has had any chance to post an event.
    assert lock_file.exists(), "POST /sessions must write the lock before it returns"
    locked_before_events = json.loads(lock_file.read_text(encoding="utf-8"))

    resp = client.post(
        f"/sessions/{session_id}/events",
        json=[{"t_ms": 1000, "type": "station_enter", "station_id": "B1", "payload": {}}],
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"accepted": 1}

    # And the accepted event did not change it.
    assert json.loads(lock_file.read_text(encoding="utf-8")) == locked_before_events


def test_events_for_a_session_with_no_lock_are_rejected(client, test_engine, predictions_dir):
    """A session row that never went through POST /sessions has no lock, and
    must not be able to accept events at all."""
    session_id = str(uuid.uuid4())
    body = valid_session_body()
    body["session_id"] = session_id
    with Session(test_engine) as db_session:
        db_session.add(SessionRecord(session_id=session_id, data=json.dumps(body)))
        db_session.commit()

    assert not (predictions_dir / f"{session_id}.json").exists()

    resp = client.post(
        f"/sessions/{session_id}/events",
        json=[{"t_ms": 1000, "type": "station_enter", "station_id": "B1", "payload": {}}],
    )
    assert resp.status_code == 409, resp.text
    assert "lock" in resp.json()["detail"].lower()


def test_events_rejected_after_the_lock_is_removed(client, predictions_dir):
    session_id, _ = create_session(client)
    (predictions_dir / f"{session_id}.json").unlink()

    resp = client.post(
        f"/sessions/{session_id}/events",
        json=[{"t_ms": 1000, "type": "station_enter", "station_id": "B1", "payload": {}}],
    )
    assert resp.status_code == 409, resp.text


def test_no_lock_check_does_not_replace_the_existing_404_and_422(client, predictions_dir):
    resp = client.post("/sessions/does-not-exist/events", json=[])
    assert resp.status_code == 404

    session_id, _ = create_session(client)
    resp = client.post(
        f"/sessions/{session_id}/events",
        json=[{"t_ms": 1000, "type": "not_a_real_type", "station_id": "B1", "payload": {}}],
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# The lock document itself (SPEC 4.6 / schemas/prediction.schema.json)
# ---------------------------------------------------------------------------


def test_lock_validates_against_the_schema(client, predictions_dir):
    session_id, _ = create_session(client, variant_id="B")
    lock = read_lock(predictions_dir, session_id)

    errors = sorted(prediction_validator().iter_errors(lock), key=str)
    assert not errors, [e.message for e in errors]

    assert lock["session_id"] == session_id
    assert lock["variant_id"] == "B"
    assert lock["population_fixation_prob"]
    assert all(isinstance(v, float) for v in lock["population_fixation_prob"].values())


def test_sha256_is_reproducible_from_the_stored_fields(client, predictions_dir):
    session_id, _ = create_session(client)
    lock = read_lock(predictions_dir, session_id)

    # The documented recipe, spelled out here independently of prediction.py -
    # scripts/eval.py (S19) has to be able to do exactly this.
    payload = json.dumps(
        {
            "population_fixation_prob": lock["population_fixation_prob"],
            "sim_run_id": lock["sim_run_id"],
            "created_at": lock["created_at"],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    assert hashlib.sha256(payload.encode("utf-8")).hexdigest() == lock["sha256"]


def test_sha256_changes_when_the_prediction_changes(client, predictions_dir):
    """A constant would satisfy "there is a hash"; a commitment must move when
    the thing it commits to moves."""
    session_id, _ = create_session(client)
    lock = read_lock(predictions_dir, session_id)

    tampered = dict(lock["population_fixation_prob"])
    first_slot = sorted(tampered)[0]
    tampered[first_slot] = tampered[first_slot] + 0.01

    assert prediction_module.compute_sha256(
        tampered, lock["sim_run_id"], lock["created_at"]
    ) != lock["sha256"]
    assert prediction_module.compute_sha256(
        lock["population_fixation_prob"], lock["sim_run_id"], lock["created_at"]
    ) == lock["sha256"]


def test_created_at_is_utc_iso8601_with_milliseconds(client, predictions_dir):
    before = datetime.now(timezone.utc)
    session_id, _ = create_session(client)
    lock = read_lock(predictions_dir, session_id)

    created_at = lock["created_at"]
    assert created_at.endswith("Z")
    assert len(created_at) == len("2026-09-14T10:32:07.412Z")
    parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    assert parsed.tzinfo is timezone.utc or parsed.utcoffset().total_seconds() == 0

    # ... and it precedes the wall-clock arrival of the first event.
    assert parsed >= before.replace(microsecond=(before.microsecond // 1000) * 1000)
    first_event_arrival = datetime.now(timezone.utc)
    resp = client.post(
        f"/sessions/{session_id}/events",
        json=[{"t_ms": 0, "type": "station_enter", "station_id": "B1", "payload": {}}],
    )
    assert resp.status_code == 200
    assert parsed < first_event_arrival


def test_git_commit_is_a_hash_or_null(client, predictions_dir):
    session_id, _ = create_session(client)
    lock = read_lock(predictions_dir, session_id)

    git_commit = lock["git_commit"]
    assert git_commit is None or (
        isinstance(git_commit, str) and len(git_commit) >= 7 and git_commit.isalnum()
    )


# ---------------------------------------------------------------------------
# What POST /sessions hands the spectator page (SPEC M9)
# ---------------------------------------------------------------------------


def test_post_sessions_returns_prediction_id_and_hash_prefix(client, predictions_dir):
    session_id, created = create_session(client)
    lock = read_lock(predictions_dir, session_id)

    assert created["prediction_id"] == lock["prediction_id"]
    badge = created["prediction"]
    assert badge["sha256_prefix"] == lock["sha256"][:8]
    assert len(badge["sha256_prefix"]) == 8
    assert badge["created_at"] == lock["created_at"]
    assert badge["sim_run_id"] == lock["sim_run_id"]


def test_stored_session_document_stays_schema_valid_and_carries_prediction_id(
    client, test_engine, predictions_dir
):
    session_id, _ = create_session(client)
    lock = read_lock(predictions_dir, session_id)

    with Session(test_engine) as db_session:
        stored = json.loads(db_session.get(SessionRecord, session_id).data)

    schema = json.loads(
        (ROOT / "schemas" / "session.schema.json").read_text(encoding="utf-8")
    )
    errors = sorted(Draft7Validator(schema).iter_errors(stored), key=str)
    assert not errors, [e.message for e in errors]
    assert stored["prediction_id"] == lock["prediction_id"]


# ---------------------------------------------------------------------------
# Determinism: same variant -> same simulation, different session -> new lock
# ---------------------------------------------------------------------------


def test_two_sessions_on_one_variant_share_sim_run_id_but_not_identity(
    client, predictions_dir
):
    first_id, _ = create_session(client, variant_id="B")
    second_id, _ = create_session(client, variant_id="B")

    first = read_lock(predictions_dir, first_id)
    second = read_lock(predictions_dir, second_id)

    assert first["sim_run_id"] == second["sim_run_id"]
    assert first["population_fixation_prob"] == second["population_fixation_prob"]
    assert first["session_id"] != second["session_id"]
    assert first["prediction_id"] != second["prediction_id"]
    # created_at differs (or at worst is equal at ms resolution), so the two
    # hashes commit to two distinct documents rather than being interchangeable.
    assert first["sha256"] == prediction_module.compute_sha256(
        first["population_fixation_prob"], first["sim_run_id"], first["created_at"]
    )


def test_different_variants_predict_differently(client, predictions_dir):
    a_id, _ = create_session(client, variant_id="A")
    b_id, _ = create_session(client, variant_id="B")

    a_lock = read_lock(predictions_dir, a_id)
    b_lock = read_lock(predictions_dir, b_id)

    assert a_lock["sim_run_id"] != b_lock["sim_run_id"]
    assert a_lock["population_fixation_prob"] != b_lock["population_fixation_prob"]


def test_reposting_a_session_does_not_rewrite_its_lock(client, predictions_dir):
    """The lock is evidence. Re-registering a session must not re-timestamp it,
    which would move the commitment after events had already been recorded."""
    body = valid_session_body()
    session_id = body["session_id"]
    assert client.post("/sessions", json=body).status_code == 201
    original = read_lock(predictions_dir, session_id)

    client.post(f"/sessions/{session_id}/events",
                json=[{"t_ms": 5, "type": "station_enter", "station_id": "B1", "payload": {}}])

    body["screen_w"] = 1920
    resp = client.post("/sessions", json=body)
    assert resp.status_code == 201, resp.text

    assert read_lock(predictions_dir, session_id) == original
    assert resp.json()["prediction_id"] == original["prediction_id"]


def test_unknown_variant_is_refused_and_writes_no_lock(client, predictions_dir, test_engine):
    body = valid_session_body(variant_id="NOPE")
    resp = client.post("/sessions", json=body)

    assert resp.status_code == 404, resp.text
    assert not (predictions_dir / f"{body['session_id']}.json").exists()
    with Session(test_engine) as db_session:
        assert db_session.get(SessionRecord, body["session_id"]) is None


# ---------------------------------------------------------------------------
# The vocabulary the lock commits to
# ---------------------------------------------------------------------------


def test_population_fixation_prob_covers_every_occupied_slot(client, predictions_dir):
    session_id, _ = create_session(client, variant_id="A")
    lock = read_lock(predictions_dir, session_id)

    base = json.loads(
        (ROOT / "data" / "planograms" / "demo_aisle.json").read_text(encoding="utf-8")
    )
    occupied = {
        slot["slot_id"]
        for bay in base["bays"]
        for shelf in bay["shelves"]
        for slot in shelf["slots"]
        if slot["sku_id"] is not None
    }
    assert set(lock["population_fixation_prob"]) == occupied


@pytest.mark.parametrize("variant_id", ["A", "B", "C"])
def test_every_seeded_variant_can_be_locked(client, predictions_dir, variant_id):
    session_id, _ = create_session(client, variant_id=variant_id)
    lock = read_lock(predictions_dir, session_id)
    errors = sorted(prediction_validator().iter_errors(lock), key=str)
    assert not errors, [e.message for e in errors]
