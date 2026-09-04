"""S14 - the live engine and the spectator socket.

The headline test here is parity: replaying a session through `live.py` in
several batches must produce exactly the attention vector `analytics/fusion.py`
produces offline in one shot. `live.py` imports that formula rather than
re-deriving it, so the two are equal by construction and this test is what
holds that property in place.

The `predictions_dir` fixture in conftest.py redirects api.app.prediction at a
per-test tmp directory, so nothing here writes into the repository's real
`predictions/` folder.
"""
import json
import random
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlmodel import Session, select
from starlette.websockets import WebSocketDisconnect

from analytics.fusion import fuse_session
from analytics.metrics import attention_spearman
from api.app import live as live_module
from api.app.db import EventRecord, SessionRecord
from api.app.routers import ws as ws_module

ROOT = Path(__file__).resolve().parents[2]

LIVE_MESSAGE_FIELDS = {
    "session_id",
    "t_ms",
    "n_fixations",
    "stations_visited",
    "attention",
    "latest_gaze",
    "spearman",
    "meaningful",
    "prediction_id",
}

STATIONS = ("B1", "B2", "B3")


def valid_session_body(variant_id: str = "A", mode: str = "webcam") -> dict:
    return {
        "session_id": str(uuid.uuid4()),
        "variant_id": variant_id,
        "consent": True,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "screen_w": 1440,
        "screen_h": 900,
        "mode": mode,
    }


def create_session(client, predictions_dir: Path, mode: str = "webcam",
                   variant_id: str = "A") -> tuple[str, dict]:
    """POST a session and return its id together with its prediction lock."""
    body = valid_session_body(variant_id, mode)
    resp = client.post("/sessions", json=body)
    assert resp.status_code == 201, resp.text
    lock = json.loads(
        (predictions_dir / f"{body['session_id']}.json").read_text(encoding="utf-8")
    )
    return body["session_id"], lock


def recorded_session(slot_ids, n_events: int = 240, seed: int = 7) -> list[dict]:
    """A deterministic stand-in for a recorded shopper session.

    Every event validates against schemas/event.schema.json, and the mix
    covers everything fusion reads (fixation, cursor_dwell, hover, pickup,
    add_to_cart) plus everything it ignores (gaze, station_enter, remove),
    so the parity check is not run on an artificially clean stream.
    """
    rng = random.Random(seed)
    slots = list(slot_ids)
    events: list[dict] = []
    t_ms = 0
    for i in range(n_events):
        t_ms += rng.randint(20, 180)
        station = STATIONS[(i // 40) % len(STATIONS)]
        roll = rng.random()
        if i % 40 == 0:
            events.append({"t_ms": t_ms, "type": "station_enter",
                           "station_id": station, "payload": {}})
        elif roll < 0.35:
            events.append({
                "t_ms": t_ms, "type": "fixation", "station_id": station,
                "payload": {"x": rng.randint(0, 1439), "y": rng.randint(0, 899),
                            "dur_ms": rng.randint(80, 700),
                            "slot_id": rng.choice(slots), "shelf_id": None},
            })
        elif roll < 0.5:
            # A fixation that landed on the shelf, not on a product slot.
            events.append({
                "t_ms": t_ms, "type": "fixation", "station_id": station,
                "payload": {"x": rng.randint(0, 1439), "y": rng.randint(0, 899),
                            "dur_ms": rng.randint(80, 400),
                            "slot_id": None, "shelf_id": f"{station}S2"},
            })
        elif roll < 0.7:
            events.append({
                "t_ms": t_ms, "type": "cursor_dwell", "station_id": station,
                "payload": {"slot_id": rng.choice(slots), "dur_ms": rng.randint(50, 900)},
            })
        elif roll < 0.8:
            events.append({
                "t_ms": t_ms, "type": "hover", "station_id": station,
                "payload": {"sku_id": "SKU_001", "slot_id": rng.choice(slots)},
            })
        elif roll < 0.86:
            events.append({
                "t_ms": t_ms, "type": "pickup", "station_id": station,
                "payload": {"sku_id": "SKU_002", "slot_id": rng.choice(slots)},
            })
        elif roll < 0.9:
            events.append({
                "t_ms": t_ms, "type": "add_to_cart", "station_id": station,
                "payload": {"sku_id": "SKU_003", "slot_id": rng.choice(slots)},
            })
        else:
            events.append({
                "t_ms": t_ms, "type": "gaze", "station_id": station,
                "payload": {"x": rng.randint(0, 1439), "y": rng.randint(0, 899),
                            "conf": round(rng.random(), 3), "t": t_ms},
            })
    return events


def batches(events: list[dict], sizes=(1, 7, 23, 4, 60, 12)) -> list[list[dict]]:
    """Split a session into deliberately uneven batches - a fold that is
    correct only for one batch size is not a fold."""
    out: list[list[dict]] = []
    i = 0
    k = 0
    while i < len(events):
        size = sizes[k % len(sizes)]
        out.append(events[i:i + size])
        i += size
        k += 1
    return out


# ---------------------------------------------------------------------------
# THE PARITY TEST: running fusion == offline fusion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["webcam", "cursor_only"])
def test_running_fusion_equals_offline_fusion(client, predictions_dir, mode):
    session_id, lock = create_session(client, predictions_dir, mode=mode)
    slot_ids = live_module.slot_vocabulary(lock)
    events = recorded_session(slot_ids)

    state = live_module.open_state(session_id, mode=mode, lock=lock)
    message = None
    for batch in batches(events):
        message = state.fold(batch)

    offline = fuse_session(events, slot_ids, mode=mode)

    # Exact equality, not a tolerance: live.py accumulates the events and hands
    # the whole list to the same fuse_session call the offline path uses, in the
    # same order, so the two are the identical sequence of float operations.
    assert message["attention"] == offline


@pytest.mark.parametrize("mode", ["webcam", "cursor_only"])
def test_replay_through_the_socket_equals_offline_fusion(client, predictions_dir, mode):
    """The same parity property, end to end through ws/session (SPEC M9)."""
    session_id, lock = create_session(client, predictions_dir, mode=mode)
    slot_ids = live_module.slot_vocabulary(lock)
    events = recorded_session(slot_ids)

    with client.websocket_connect(f"/ws/spectator/{session_id}") as spectator:
        with client.websocket_connect(f"/ws/session/{session_id}") as ingest:
            last = None
            for batch in batches(events):
                ingest.send_json({"events": batch})
                last = spectator.receive_json()

    offline = fuse_session(events, slot_ids, mode=mode)
    assert last["attention"] == offline
    assert set(last) == LIVE_MESSAGE_FIELDS
    assert last["session_id"] == session_id
    assert last["prediction_id"] == lock["prediction_id"]


def test_replayed_events_reach_the_database(client, predictions_dir, test_engine):
    session_id, lock = create_session(client, predictions_dir)
    events = recorded_session(live_module.slot_vocabulary(lock), n_events=30)

    with client.websocket_connect(f"/ws/session/{session_id}") as ingest:
        for batch in batches(events, sizes=(10,)):
            ingest.send_json({"events": batch})

    with Session(test_engine) as db_session:
        rows = db_session.exec(
            select(EventRecord).where(EventRecord.session_id == session_id)
        ).all()
    assert len(rows) == len(events)
    assert json.loads(rows[0].data) == events[0]


# ---------------------------------------------------------------------------
# meaningful: false below 15 fixations, true at 15 (SPEC 4.7)
# ---------------------------------------------------------------------------


def fixation(slot_id: str, t_ms: int) -> dict:
    return {"t_ms": t_ms, "type": "fixation", "station_id": "B1",
            "payload": {"x": 10, "y": 20, "dur_ms": 120, "slot_id": slot_id,
                        "shelf_id": None}}


def test_meaningful_flips_exactly_at_fifteen_fixations(client, predictions_dir):
    session_id, lock = create_session(client, predictions_dir)
    slot_ids = live_module.slot_vocabulary(lock)
    state = live_module.open_state(session_id, mode="webcam", lock=lock)

    assert live_module.MEANINGFUL_MIN_FIXATIONS == 15

    for i in range(14):
        message = state.fold([fixation(slot_ids[i % len(slot_ids)], 100 * i)])
        assert message["n_fixations"] == i + 1
        assert message["meaningful"] is False, f"meaningful at {i + 1} fixations"

    message = state.fold([fixation(slot_ids[0], 9999)])
    assert message["n_fixations"] == 15
    assert message["meaningful"] is True

    message = state.fold([fixation(slot_ids[1], 10_000)])
    assert message["n_fixations"] == 16
    assert message["meaningful"] is True


def test_non_fixation_events_do_not_count_towards_meaningful(client, predictions_dir):
    session_id, lock = create_session(client, predictions_dir)
    slot_ids = live_module.slot_vocabulary(lock)
    state = live_module.open_state(session_id, mode="webcam", lock=lock)

    noise = [
        {"t_ms": i, "type": "cursor_dwell", "station_id": "B1",
         "payload": {"slot_id": slot_ids[0], "dur_ms": 100}}
        for i in range(50)
    ]
    message = state.fold(noise)
    assert message["n_fixations"] == 0
    assert message["meaningful"] is False


# ---------------------------------------------------------------------------
# The rest of the 4.7 message
# ---------------------------------------------------------------------------


def test_spearman_is_measured_against_the_locked_vector(client, predictions_dir):
    session_id, lock = create_session(client, predictions_dir)
    slot_ids = live_module.slot_vocabulary(lock)
    events = recorded_session(slot_ids)

    state = live_module.open_state(session_id, mode="webcam", lock=lock)
    message = state.fold(events)

    expected = attention_spearman(
        message["attention"], lock["population_fixation_prob"], slot_ids
    )
    assert message["spearman"] == pytest.approx(expected)

    # It is the lock that is being compared against, not a fresh simulation:
    # perturbing the locked vector must move the number.
    tampered = dict(lock)
    tampered["population_fixation_prob"] = {
        slot_id: float(i) for i, slot_id in enumerate(slot_ids)
    }
    other = live_module.open_state(session_id + "-x", mode="webcam", lock=tampered)
    assert other.fold(events)["spearman"] != message["spearman"]


def test_t_ms_stations_visited_and_latest_gaze_track_the_stream(client, predictions_dir):
    session_id, lock = create_session(client, predictions_dir)
    slot_ids = live_module.slot_vocabulary(lock)
    state = live_module.open_state(session_id, mode="webcam", lock=lock)

    assert state.snapshot()["latest_gaze"] is None
    assert state.snapshot()["stations_visited"] == 0

    message = state.fold([
        {"t_ms": 100, "type": "station_enter", "station_id": "B1", "payload": {}},
        {"t_ms": 250, "type": "gaze", "station_id": "B1",
         "payload": {"x": 812, "y": 344, "conf": 0.9, "t": 250}},
    ])
    assert message["t_ms"] == 250
    assert message["stations_visited"] == 1
    assert message["latest_gaze"] == {"x": 812, "y": 344}

    message = state.fold([
        {"t_ms": 900, "type": "station_enter", "station_id": "B2", "payload": {}},
        {"t_ms": 950, "type": "gaze", "station_id": "B2",
         "payload": {"x": 10, "y": 20, "conf": 0.4, "t": 950}},
        fixation(slot_ids[0], 1200),
    ])
    assert message["t_ms"] == 1200
    assert message["stations_visited"] == 2
    assert message["latest_gaze"] == {"x": 10, "y": 20}


# ---------------------------------------------------------------------------
# Budget: a batch folds in under 20 ms (docs/PLAN.md S14, SPEC M9)
# ---------------------------------------------------------------------------


def test_batch_fold_stays_under_20ms(client, predictions_dir, capsys):
    session_id, lock = create_session(client, predictions_dir)
    slot_ids = live_module.slot_vocabulary(lock)
    # A long session: ~3,000 events is about three minutes of dense capture,
    # well past anything the 60-second demo produces. The fold cost grows with
    # the accumulated session, so the last batch is the worst case.
    events = recorded_session(slot_ids, n_events=3000, seed=11)

    state = live_module.open_state(session_id, mode="webcam", lock=lock)
    timings = []
    for batch in batches(events, sizes=(25,)):
        started = time.perf_counter()
        state.fold(batch)
        timings.append((time.perf_counter() - started) * 1000.0)

    worst = max(timings)
    mean = sum(timings) / len(timings)
    with capsys.disabled():
        print(f"\n[S14] fold over {len(events)} events in {len(timings)} batches: "
              f"worst {worst:.2f} ms, mean {mean:.2f} ms (budget 20 ms)")
    assert worst < 20.0, f"worst batch fold {worst:.2f} ms exceeds the 20 ms budget"


# ---------------------------------------------------------------------------
# Spectator sockets
# ---------------------------------------------------------------------------


def test_two_spectators_both_receive_updates_and_one_can_leave(client, predictions_dir):
    session_id, lock = create_session(client, predictions_dir)
    slot_ids = live_module.slot_vocabulary(lock)
    batch_one = [fixation(slot_ids[0], 100)]
    batch_two = [fixation(slot_ids[1], 200)]
    batch_three = [fixation(slot_ids[2], 300)]

    with client.websocket_connect(f"/ws/spectator/{session_id}") as first:
        with client.websocket_connect(f"/ws/spectator/{session_id}") as second:
            with client.websocket_connect(f"/ws/session/{session_id}") as ingest:
                ingest.send_json({"events": batch_one})
                a = first.receive_json()
                b = second.receive_json()
                assert a == b
                assert a["n_fixations"] == 1

                second.close()

                # The remaining spectator keeps receiving...
                ingest.send_json({"events": batch_two})
                assert first.receive_json()["n_fixations"] == 2

                # ... and the ingest socket itself is unharmed.
                ingest.send_json({"events": batch_three})
                assert first.receive_json()["n_fixations"] == 3


def test_a_late_spectator_gets_the_current_snapshot_on_connect(client, predictions_dir):
    session_id, lock = create_session(client, predictions_dir)
    slot_ids = live_module.slot_vocabulary(lock)

    with client.websocket_connect(f"/ws/session/{session_id}") as ingest:
        ingest.send_json({"events": [fixation(slot_ids[0], 100)]})
        with client.websocket_connect(f"/ws/spectator/{session_id}") as spectator:
            snapshot = spectator.receive_json()
    assert set(snapshot) == LIVE_MESSAGE_FIELDS
    assert snapshot["session_id"] == session_id
    assert snapshot["prediction_id"] == lock["prediction_id"]


def test_ingest_survives_a_spectator_that_vanished(client, predictions_dir):
    """A spectator window closed mid-demo must not take the shopper down."""
    session_id, lock = create_session(client, predictions_dir)
    slot_ids = live_module.slot_vocabulary(lock)

    with client.websocket_connect(f"/ws/session/{session_id}") as ingest:
        with client.websocket_connect(f"/ws/spectator/{session_id}"):
            pass  # connects and immediately disconnects
        ingest.send_json({"events": [fixation(slot_ids[0], 100)]})
        ingest.send_json({"events": [fixation(slot_ids[1], 200)]})

    assert live_module.get_state(session_id).snapshot()["n_fixations"] == 2


# ---------------------------------------------------------------------------
# Refusals: no lock means no ingest
# ---------------------------------------------------------------------------


def test_ws_session_refused_when_the_session_has_no_lock(client, test_engine, predictions_dir):
    session_id = str(uuid.uuid4())
    body = valid_session_body()
    body["session_id"] = session_id
    with Session(test_engine) as db_session:
        db_session.add(SessionRecord(session_id=session_id, data=json.dumps(body)))
        db_session.commit()

    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect(f"/ws/session/{session_id}"):
            pass
    assert excinfo.value.code == ws_module.CLOSE_NO_PREDICTION_LOCK


def test_ws_session_refused_for_an_unknown_session(client, predictions_dir):
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect("/ws/session/does-not-exist"):
            pass
    assert excinfo.value.code == ws_module.CLOSE_UNKNOWN_SESSION


def test_ws_session_reports_an_invalid_batch_without_dropping_the_stream(
    client, predictions_dir, test_engine
):
    session_id, lock = create_session(client, predictions_dir)
    slot_ids = live_module.slot_vocabulary(lock)

    with client.websocket_connect(f"/ws/session/{session_id}") as ingest:
        ingest.send_json({"events": [{"t_ms": 1, "type": "not_a_real_type",
                                      "station_id": "B1", "payload": {}}]})
        error = ingest.receive_json()
        assert "error" in error
        # The socket is still usable afterwards.
        ingest.send_json({"events": [fixation(slot_ids[0], 100)]})

    with Session(test_engine) as db_session:
        rows = db_session.exec(
            select(EventRecord).where(EventRecord.session_id == session_id)
        ).all()
    assert len(rows) == 1, "the invalid batch must not have been persisted"


# ---------------------------------------------------------------------------
# The fake spectator stream that unblocks Track A (docs/PLAN.md S14)
# ---------------------------------------------------------------------------


def test_fake_spectator_stream_is_wellformed_and_clearly_marked(client, predictions_dir):
    with client.websocket_connect("/ws/spectator/demo?fake=1") as spectator:
        messages = [spectator.receive_json() for _ in range(3)]

    for message in messages:
        assert set(message) == LIVE_MESSAGE_FIELDS | {"fake"}
        assert message["fake"] is True
        assert message["prediction_id"] == ws_module.FAKE_PREDICTION_ID
        assert message["session_id"] == ws_module.FAKE_SESSION_ID
        assert isinstance(message["attention"], dict) and message["attention"]
        assert -1.0 <= message["spearman"] <= 1.0
        assert isinstance(message["meaningful"], bool)
        assert set(message["latest_gaze"]) == {"x", "y"}

    # It is a stream, not one repeated frame.
    assert messages[0]["t_ms"] < messages[-1]["t_ms"]


def test_real_spectator_stream_is_never_marked_fake(client, predictions_dir):
    session_id, lock = create_session(client, predictions_dir)
    slot_ids = live_module.slot_vocabulary(lock)

    with client.websocket_connect(f"/ws/spectator/{session_id}") as spectator:
        with client.websocket_connect(f"/ws/session/{session_id}") as ingest:
            ingest.send_json({"events": [fixation(slot_ids[0], 100)]})
            message = spectator.receive_json()

    assert "fake" not in message
    assert message["session_id"] == session_id
