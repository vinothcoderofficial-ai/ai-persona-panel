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

from api.app.db import EventRecord
from api.app.resolve import resolve

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
