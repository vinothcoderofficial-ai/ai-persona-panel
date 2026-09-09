"""HTTP-level tests for the ShopperTwin API, via TestClient.

Uses the `client` fixture from conftest.py, which points the whole app at an
isolated in-memory SQLite database (startup seeding included) so the real
shoppertwin.db is never touched.
"""
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import Session, select

from api.app import prediction
from api.app.db import EventRecord
from api.app.resolve import resolve
from api.app.routers import sessions as sessions_router

ROOT = Path(__file__).resolve().parents[2]


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def base_planogram() -> dict:
    return load_json(ROOT / "data" / "planograms" / "demo_aisle.json")


def variant(name: str) -> dict:
    return load_json(ROOT / "data" / "variants" / f"{name}.json")


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


# ---------------------------------------------------------------------------
# GET /variants/{id}/resolved
# ---------------------------------------------------------------------------


def test_variants_resolved_endpoint_matches_pure_function(client):
    base = base_planogram()
    for variant_id in ("A", "B", "C"):
        expected = resolve(base, variant(variant_id))

        resp = client.get(f"/variants/{variant_id}/resolved")

        assert resp.status_code == 200
        assert resp.json() == expected


def test_variants_resolved_unknown_returns_404(client):
    resp = client.get("/variants/UNKNOWN/resolved")
    assert resp.status_code == 404


def test_post_variant_returns_resolved_planogram(client):
    base = base_planogram()
    v = {
        "variant_id": "D_test",
        "base_planogram_id": "demo_aisle",
        "name": "test variant",
        "patches": [{"op": "set_price", "sku_id": "SKU_001", "price": 99.0}],
    }
    expected = resolve(base, v)

    resp = client.post("/variants", json=v)
    assert resp.status_code == 201, resp.text
    assert resp.json() == expected

    resp2 = client.get("/variants/D_test/resolved")
    assert resp2.status_code == 200
    assert resp2.json() == expected


def test_post_variant_unknown_base_planogram_404(client):
    v = {
        "variant_id": "E_test",
        "base_planogram_id": "does_not_exist",
        "name": "test variant",
        "patches": [],
    }
    resp = client.post("/variants", json=v)
    assert resp.status_code == 404


def test_post_variant_bad_patch_reference_400_and_not_stored(client):
    v = {
        "variant_id": "F_test",
        "base_planogram_id": "demo_aisle",
        "name": "test variant",
        "patches": [{"op": "move_sku", "sku_id": "SKU_999", "to_slot_id": "B1S1P1"}],
    }
    resp = client.post("/variants", json=v)
    assert resp.status_code == 400

    resp2 = client.get("/variants/F_test/resolved")
    assert resp2.status_code == 404


# ---------------------------------------------------------------------------
# Sessions + events round trip
# ---------------------------------------------------------------------------


def test_session_round_trip(client, test_engine):
    body = valid_session_body()
    session_id = body["session_id"]

    resp = client.post("/sessions", json=body)
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["session_id"] == session_id
    assert created["variant_id"] == "A"

    events = [
        {"t_ms": 1000, "type": "station_enter", "station_id": "B1", "payload": {}},
        {"t_ms": 4500, "type": "pickup", "station_id": "B1", "payload": {"slot_id": "B1S1P1"}},
    ]
    resp = client.post(f"/sessions/{session_id}/events", json=events)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"accepted": 2}

    # events are persisted and retrievable/countable in storage
    with Session(test_engine) as db_session:
        rows = db_session.exec(
            select(EventRecord).where(EventRecord.session_id == session_id)
        ).all()
    assert len(rows) == 2
    stored_types = {json.loads(r.data)["type"] for r in rows}
    assert stored_types == {"station_enter", "pickup"}

    ended_at = datetime.now(timezone.utc).isoformat()
    finish_body = {
        "ended_at": ended_at,
        "quality": {"fixation_coverage": 0.0, "stations_visited": 1, "duration_s": 50.0},
        "accepted": True,
        "reject_reason": None,
    }
    resp = client.post(f"/sessions/{session_id}/finish", json=finish_body)
    assert resp.status_code == 200, resp.text
    finished = resp.json()
    assert finished["session_id"] == session_id
    assert finished["ended_at"] == ended_at
    assert finished["accepted"] is True
    assert finished["quality"]["stations_visited"] == 1


def test_post_events_invalid_batch_rejected_entirely(client, test_engine):
    body = valid_session_body()
    session_id = body["session_id"]
    client.post("/sessions", json=body)

    events = [
        {"t_ms": 1000, "type": "station_enter", "station_id": "B1", "payload": {}},
        {"t_ms": 2000, "type": "not_a_real_type", "station_id": "B1", "payload": {}},
    ]
    resp = client.post(f"/sessions/{session_id}/events", json=events)
    assert resp.status_code == 422

    with Session(test_engine) as db_session:
        rows = db_session.exec(
            select(EventRecord).where(EventRecord.session_id == session_id)
        ).all()
    assert len(rows) == 0


def test_events_unknown_session_404(client):
    resp = client.post("/sessions/does-not-exist/events", json=[])
    assert resp.status_code == 404


def test_finish_unknown_session_404(client):
    resp = client.post(
        "/sessions/does-not-exist/finish",
        json={"ended_at": None, "quality": {}, "accepted": None, "reject_reason": None},
    )
    assert resp.status_code == 404


def test_post_session_invalid_mode_rejected(client):
    body = valid_session_body()
    body["mode"] = "not_a_real_mode"
    resp = client.post("/sessions", json=body)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Experiments
# ---------------------------------------------------------------------------


def test_experiment_round_trip(client):
    session_body = valid_session_body()
    client.post("/sessions", json=session_body)

    resp = client.post(
        "/experiments",
        json={"variant_id": "A", "session_id": session_body["session_id"]},
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert "experiment_id" in created
    assert created["variant_id"] == "A"
    assert created["session_id"] == session_body["session_id"]

    resp = client.get(f"/experiments/{created['experiment_id']}")
    assert resp.status_code == 200
    assert resp.json() == created

    resp = client.get("/experiments/nope")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Planograms
# ---------------------------------------------------------------------------


def test_post_invalid_planogram_rejected(client):
    bad = base_planogram()
    bad["planogram_id"] = "bad_test_planogram"
    bad["source"] = "webcam"  # not in the schema enum ["manual", "video"]

    resp = client.post("/planograms", json=bad)
    assert resp.status_code == 422

    resp = client.get("/planograms/bad_test_planogram")
    assert resp.status_code == 404

    resp = client.get("/planograms")
    assert "bad_test_planogram" not in resp.json()


def test_planograms_list_and_get(client):
    resp = client.get("/planograms")
    assert resp.status_code == 200
    assert "demo_aisle" in resp.json()

    resp = client.get("/planograms/demo_aisle")
    assert resp.status_code == 200
    assert resp.json() == base_planogram()

    resp = client.get("/planograms/does-not-exist")
    assert resp.status_code == 404


def test_post_planogram_round_trip(client):
    new_pg = base_planogram()
    new_pg["planogram_id"] = "second_aisle"
    new_pg["name"] = "Second aisle"

    resp = client.post("/planograms", json=new_pg)
    assert resp.status_code == 201, resp.text
    assert resp.json() == new_pg

    resp = client.get("/planograms/second_aisle")
    assert resp.status_code == 200
    assert resp.json() == new_pg

    resp = client.get("/planograms")
    assert {"demo_aisle", "second_aisle"} <= set(resp.json())


# ---------------------------------------------------------------------------
# first_event_at: the ordering guarantee, measured on one clock (S31)
# ---------------------------------------------------------------------------

def _open_session(client, session_id: str = "11111111-1111-4111-8111-111111111111"):
    body = {
        "session_id": session_id,
        "variant_id": "A",
        "consent": True,
        "started_at": "2026-09-06T04:27:22.785Z",
        "screen_w": 1536,
        "screen_h": 864,
        "mode": "cursor_only",
    }
    response = client.post("/sessions", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _event(t_ms: int, type_: str = "station_enter"):
    return {"t_ms": t_ms, "type": type_, "station_id": "B1", "payload": {}}


def _lock_is_not_after(created_at: str, first_event_at: str) -> bool:
    """The ordering the two tests below assert: the lock, then the stamp.

    `<=`, not `<`, and the equal case is the whole reason this helper exists.
    Both moments are milliseconds -- `created_at` through
    `prediction.utc_now_iso`, whose three decimal places are fixed by SPEC 4.6
    and, worse, are hashed into the lock's own `sha256`, so it cannot simply be
    given more digits; `first_event_at` through
    `datetime.isoformat(timespec="milliseconds")`. In this process nothing
    separates the two writes but one validated event batch, and measuring the
    flow 300 times put the gap at 0-22 ms with 4 runs at exactly 0. The strict
    version passed alone and failed under the full suite, both stamps reading
    `2026-09-09T01:53:24.028Z`.

    Allowing the tie is not a weakening, because the guarantee being defended
    is program order, not clock arithmetic: `POST /sessions` writes the lock
    and returns before `POST /sessions/{id}/events` can be called at all, so a
    tie is a millisecond clock failing to resolve two writes and never
    evidence that the event came first. The thing that must never be tolerated
    -- the lock landing *after* the event that it claims to have predicted --
    still fails here.

    `scripts/eval.py` keeps the strict `<` for committed sessions, and should:
    there the two moments are separated by a browser round trip and a human
    being walking up to a shelf, so a tie really would mean something is
    wrong.
    """

    def parse(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    return parse(created_at) <= parse(first_event_at)


def test_a_new_session_has_no_first_event_time(client):
    """Absent until an event arrives - never a fabricated timestamp for a
    session nobody has shopped yet."""
    session = _open_session(client)

    assert session.get("first_event_at") in (None, ...) or "first_event_at" not in session


def test_the_first_event_stamps_the_session(client):
    session_id = "22222222-2222-4222-8222-222222222222"
    _open_session(client, session_id)

    client.post(f"/sessions/{session_id}/events", json=[_event(204)])

    stored = client.get(f"/sessions/{session_id}").json()
    assert stored["first_event_at"] is not None


def test_the_stamp_is_the_first_event_and_never_moves(client):
    """Written once. A later batch that overwrote it would report the ordering
    guarantee against the wrong moment - and always a safer-looking one."""
    session_id = "33333333-3333-4333-8333-333333333333"
    _open_session(client, session_id)

    client.post(f"/sessions/{session_id}/events", json=[_event(204)])
    first = client.get(f"/sessions/{session_id}").json()["first_event_at"]

    client.post(f"/sessions/{session_id}/events", json=[_event(9000, "checkout")])
    second = client.get(f"/sessions/{session_id}").json()["first_event_at"]

    assert first == second


def test_the_stamp_is_after_the_prediction_lock(client):
    """The whole point, and the thing scripts/eval.py checks.

    Both timestamps are written by this process on this clock, so the
    comparison is between two moments in one ordering rather than between a
    browser's clock and a server's. Reconstructing the event time as
    `started_at + t_ms` instead understates it by the POST /sessions round
    trip, and failed a real session whose ordering was correct.

    See `_lock_is_not_after` for why a same-millisecond tie is allowed and a
    lock stamped after the event is still a failure.
    """
    session_id = "44444444-4444-4444-8444-444444444444"
    _open_session(client, session_id)
    client.post(f"/sessions/{session_id}/events", json=[_event(0)])

    lock = client.get(f"/sessions/{session_id}/prediction").json()
    stamp = client.get(f"/sessions/{session_id}").json()["first_event_at"]

    assert _lock_is_not_after(lock["created_at"], stamp)


def test_a_lock_and_a_stamp_in_the_same_millisecond_are_still_in_order(
    client, monkeypatch
):
    """A tie, made deterministic instead of waited for.

    Both stamps have millisecond resolution and both are written by this
    process inside one request pair, so they can land in the same millisecond.
    Driving this exact flow 300 times measured gaps of 0-22 ms with 4 of the
    300 exactly zero -- which is why the strict `<` the test above used to
    assert failed about one full-suite run in seventy-five while passing every
    time that test was run alone. Freezing both clocks on one instant makes
    the tie the test rather than an accident of scheduling.
    """
    frozen_iso = "2026-09-09T01:53:24.028Z"
    frozen = datetime(2026, 9, 9, 1, 53, 24, 28000, tzinfo=timezone.utc)

    class FrozenClock(datetime):
        """`datetime` with `now()` pinned, so the rest of the class still works."""

        @classmethod
        def now(cls, tz=None):
            return frozen

    monkeypatch.setattr(prediction, "utc_now_iso", lambda: frozen_iso)
    monkeypatch.setattr(sessions_router, "datetime", FrozenClock)

    session_id = "77777777-7777-4777-8777-777777777777"
    _open_session(client, session_id)
    client.post(f"/sessions/{session_id}/events", json=[_event(0)])

    lock = client.get(f"/sessions/{session_id}/prediction").json()
    stamp = client.get(f"/sessions/{session_id}").json()["first_event_at"]

    assert lock["created_at"] == frozen_iso
    assert stamp == frozen_iso
    assert _lock_is_not_after(lock["created_at"], stamp)

    # And the tie is as far as the tolerance goes: a lock stamped after the
    # event it claims to have predicted is still a failure, which is the
    # assertion this whole section exists to make.
    assert not _lock_is_not_after("2026-09-09T01:53:24.029Z", stamp)


def test_a_rejected_batch_does_not_stamp_the_session(client):
    """An invalid batch is not a first event. Stamping on the attempt would
    record a session as having been shopped when nothing was accepted."""
    session_id = "55555555-5555-4555-8555-555555555555"
    _open_session(client, session_id)

    bad = client.post(f"/sessions/{session_id}/events", json=[{"type": "nope"}])
    assert bad.status_code == 422

    assert client.get(f"/sessions/{session_id}").json().get("first_event_at") is None


def test_an_empty_batch_does_not_stamp_the_session(client):
    session_id = "66666666-6666-4666-8666-666666666666"
    _open_session(client, session_id)

    client.post(f"/sessions/{session_id}/events", json=[])

    assert client.get(f"/sessions/{session_id}").json().get("first_event_at") is None
