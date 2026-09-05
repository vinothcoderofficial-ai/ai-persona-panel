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

from analytics.fusion import fuse_session, fuse_synthetic, trimmed_mean
from analytics.metrics import attention_spearman
from api.app import live as live_module
from api.app import prediction as prediction_module
from api.app import simcache
from api.app.db import EventRecord, SessionRecord
from api.app.routers import ws as ws_module

ROOT = Path(__file__).resolve().parents[2]

LIVE_MESSAGE_FIELDS = {
    "session_id",
    "t_ms",
    "n_fixations",
    "n_cursor_dwells",
    "evidence_count",
    "evidence_kind",
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

    state = open_live_state(client, session_id, lock, mode=mode)
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


def stored_events(engine, session_id, expected: int, timeout_s: float = 5.0):
    """The session's persisted events, once at least `expected` have landed.

    TestClient runs the websocket handler on a portal thread, so leaving the
    `with` block closes the socket but does not guarantee ws.py's `finally`
    flush has finished. Asserting straight after the block is a race -- it is
    what made this module flake. Polling a bounded predicate turns a genuine
    async boundary into a deterministic wait, and still fails (on the caller's
    assertion) if the rows never arrive.
    """
    deadline = time.monotonic() + timeout_s
    rows = []
    while True:
        with Session(engine) as db_session:
            rows = db_session.exec(
                select(EventRecord).where(EventRecord.session_id == session_id)
            ).all()
        if len(rows) >= expected or time.monotonic() >= deadline:
            return rows
        time.sleep(0.005)


def test_replayed_events_reach_the_database(client, predictions_dir, test_engine):
    session_id, lock = create_session(client, predictions_dir)
    events = recorded_session(live_module.slot_vocabulary(lock), n_events=30)

    with client.websocket_connect(f"/ws/session/{session_id}") as ingest:
        for batch in batches(events, sizes=(10,)):
            ingest.send_json({"events": batch})

    rows = stored_events(test_engine, session_id, len(events))
    assert len(rows) == len(events)
    assert json.loads(rows[0].data) == events[0]


# ---------------------------------------------------------------------------
# meaningful: false below 15 fixations, true at 15, for a WEBCAM session
# (SPEC 4.7 verbatim). The cursor_only counterpart is in the defect 2 section.
# ---------------------------------------------------------------------------


def fixation(slot_id: str, t_ms: int) -> dict:
    return {"t_ms": t_ms, "type": "fixation", "station_id": "B1",
            "payload": {"x": 10, "y": 20, "dur_ms": 120, "slot_id": slot_id,
                        "shelf_id": None}}


def test_meaningful_flips_exactly_at_fifteen_fixations(client, predictions_dir, capsys):
    session_id, lock = create_session(client, predictions_dir)
    slot_ids = live_module.slot_vocabulary(lock)
    state = open_live_state(client, session_id, lock, mode="webcam")

    assert live_module.MEANINGFUL_MIN_EVIDENCE == 15

    for i in range(14):
        message = state.fold([fixation(slot_ids[i % len(slot_ids)], 100 * i)])
        assert message["n_fixations"] == i + 1
        assert message["evidence_count"] == i + 1
        assert message["evidence_kind"] == "fixations"
        assert message["meaningful"] is False, f"meaningful at {i + 1} fixations"

    message = state.fold([fixation(slot_ids[0], 9999)])
    with capsys.disabled():
        print(f"\n[defect 2/webcam] meaningful flips at "
              f"{message['evidence_count']} {message['evidence_kind']}")
    assert message["n_fixations"] == 15
    assert message["meaningful"] is True

    message = state.fold([fixation(slot_ids[1], 10_000)])
    assert message["n_fixations"] == 16
    assert message["meaningful"] is True


def test_events_that_carry_no_evidence_do_not_count_towards_meaningful(
    client, predictions_dir
):
    """Neither mode counts an event that is not its own evidence channel."""
    session_id, lock = create_session(client, predictions_dir)
    slot_ids = live_module.slot_vocabulary(lock)
    state = open_live_state(client, session_id, lock, mode="webcam")

    noise = [
        {"t_ms": i, "type": "hover", "station_id": "B1",
         "payload": {"sku_id": "SKU_001", "slot_id": slot_ids[0]}}
        for i in range(50)
    ]
    message = state.fold(noise)
    assert message["n_fixations"] == 0
    assert message["n_cursor_dwells"] == 0
    assert message["evidence_count"] == 0
    assert message["meaningful"] is False


# ---------------------------------------------------------------------------
# The rest of the 4.7 message
# ---------------------------------------------------------------------------


def test_t_ms_stations_visited_and_latest_gaze_track_the_stream(client, predictions_dir):
    session_id, lock = create_session(client, predictions_dir)
    slot_ids = live_module.slot_vocabulary(lock)
    state = open_live_state(client, session_id, lock, mode="webcam")

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

    state = open_live_state(client, session_id, lock, mode="webcam")
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

    # A spectator gives this test a synchronisation point. Acks were cut
    # (docs/PLAN.md 13), so the ingest socket answers a *valid* batch with
    # nothing at all -- closing straight after sending one races the server and
    # can drop it. The broadcast is the observable effect of the fold, so
    # waiting for it proves the batch was processed before we close.
    with client.websocket_connect(f"/ws/session/{session_id}") as ingest:
        ingest.send_json({"events": [{"t_ms": 1, "type": "not_a_real_type",
                                      "station_id": "B1", "payload": {}}]})
        error = ingest.receive_json()
        assert "error" in error

        # The spectator connects second, because the snapshot is only sent once
        # a live state exists and the ingest socket is what creates it.
        with client.websocket_connect(f"/ws/spectator/{session_id}") as spectator:
            spectator.receive_json()  # the join snapshot
            # The socket is still usable afterwards.
            ingest.send_json({"events": [fixation(slot_ids[0], 100)]})
            assert spectator.receive_json()["n_fixations"] == 1

    # The invalid batch was rejected before the valid one was sent, so anything
    # wrongly persisted from it is already present by the time the valid event
    # lands -- waiting for 1 row cannot mask a second.
    rows = stored_events(test_engine, session_id, 1)
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


# ===========================================================================
# DEFECT 1 - the live meter and scripts/eval.py compare against the SAME
# synthetic vector
# ===========================================================================


def resolved_planogram(client, variant_id: str) -> dict:
    """The resolved planogram for a variant, through the public route.

    CLAUDE.md: resolve() lives only in api/app/resolve.py, so the test asks the
    server for the resolved document rather than assembling one.
    """
    resp = client.get(f"/variants/{variant_id}/resolved")
    assert resp.status_code == 200, resp.text
    return resp.json()


def open_live_state(client, session_id: str, lock: dict, mode: str = "webcam"):
    """`live.open_state` with the resolved planogram its comparison needs."""
    return live_module.open_state(
        session_id,
        mode=mode,
        lock=lock,
        resolved_planogram=resolved_planogram(client, lock["variant_id"]),
    )


def offline_spearman(client, lock: dict, events: list, mode: str) -> float:
    """The comparison `scripts/eval.py` performs, rebuilt from its own parts.

    `_analyse()` in eval.py, for one variant:

        planogram = resolve(base, variant)
        slot_ids  = prediction.occupied_slot_ids(planogram)
        bundle    = simcache.population(planogram, variant_id, n_synth, seed)
        synth     = fuse_synthetic(bundle.population, planogram, slot_ids, mode)
        attention = trimmed_mean([fuse_session(e, slot_ids, mode) for e in panel])
        rho       = attention_spearman(attention, synth, slot_ids)

    The panel here is one session, which is what makes a per-session live
    number and eval.py's per-panel number comparable at all: `trimmed_mean`
    over one session drops int(1 * 0.10) == 0 values per tail and is the
    identity, and `dominant_mode` of a one-session panel is that session's own
    mode.
    """
    planogram = resolved_planogram(client, lock["variant_id"])
    slot_ids = prediction_module.occupied_slot_ids(planogram)
    bundle = simcache.population(
        planogram,
        lock["variant_id"],
        n_synth=prediction_module.N_SYNTH,
        seed=prediction_module.SEED,
    )
    synth = fuse_synthetic(bundle.population, planogram, slot_ids, mode=mode)
    attention = trimmed_mean([fuse_session(events, slot_ids, mode=mode)], slot_ids)
    return attention_spearman(attention, synth, slot_ids)


@pytest.mark.parametrize("mode", ["cursor_only", "webcam"])
def test_live_spearman_equals_the_offline_evaluation_spearman(
    client, predictions_dir, mode, capsys
):
    """The headline of defect 1: one session, two code paths, one number.

    The spectator screen and RESULTS.md must not be able to show different
    agreement figures for the same session.
    """
    session_id, lock = create_session(client, predictions_dir, mode=mode)
    events = recorded_session(live_module.slot_vocabulary(lock))

    state = open_live_state(client, session_id, lock, mode=mode)
    live_rho = state.fold(events)["spearman"]
    offline_rho = offline_spearman(client, lock, events, mode)

    with capsys.disabled():
        print(f"\n[defect 1/{mode}] live rho {live_rho!r} vs offline rho "
              f"{offline_rho!r} (delta {abs(live_rho - offline_rho):.3e})")

    # The two paths index their vectors in different orders -- live.py sorts the
    # lock's keys, eval.py takes the planogram's order -- so the tolerance is
    # for float summation order, not for a difference in what is computed.
    assert live_rho == pytest.approx(offline_rho, abs=1e-12)


def test_spearman_is_measured_against_the_fused_locked_prediction(client, predictions_dir):
    """The live synthetic side is `fuse_synthetic` of the LOCKED run.

    Not the raw `population_fixation_prob` (that was the defect) and not a
    fresh, unverified simulation (that would drop the pre-registration).
    """
    session_id, lock = create_session(client, predictions_dir)
    slot_ids = live_module.slot_vocabulary(lock)
    events = recorded_session(slot_ids)
    planogram = resolved_planogram(client, lock["variant_id"])

    state = open_live_state(client, session_id, lock, mode="webcam")
    message = state.fold(events)

    bundle = simcache.population(
        planogram, lock["variant_id"],
        n_synth=prediction_module.N_SYNTH, seed=prediction_module.SEED,
    )
    expected = attention_spearman(
        message["attention"],
        fuse_synthetic(bundle.population, planogram, slot_ids, mode="webcam"),
        slot_ids,
    )
    assert message["spearman"] == pytest.approx(expected, abs=1e-12)

    # And it is measurably NOT the old raw-vector comparison, which is the
    # whole reason this changed.
    raw = attention_spearman(
        message["attention"], lock["population_fixation_prob"], slot_ids
    )
    assert message["spearman"] != pytest.approx(raw, abs=1e-9)


def test_the_locked_vector_still_drives_the_comparison(client, predictions_dir):
    """Fusing is a deterministic transform OF the locked run, not a replacement."""
    session_id, lock = create_session(client, predictions_dir)
    slot_ids = live_module.slot_vocabulary(lock)
    planogram = resolved_planogram(client, lock["variant_id"])
    bundle = simcache.population(
        planogram, lock["variant_id"],
        n_synth=prediction_module.N_SYNTH, seed=prediction_module.SEED,
    )
    synthetic = live_module.synthetic_vector(
        lock, planogram, slot_ids, mode="cursor_only"
    )

    assert synthetic == pytest.approx(
        fuse_synthetic(bundle.population, planogram, slot_ids, mode="cursor_only")
    )
    assert set(synthetic) == set(lock["population_fixation_prob"])


def test_a_lock_whose_vector_no_longer_matches_the_simulator_is_refused(
    client, predictions_dir
):
    """A stale lock fails loudly rather than being silently compared against."""
    create_session(client, predictions_dir)
    _, lock = create_session(client, predictions_dir)
    planogram = resolved_planogram(client, lock["variant_id"])
    slot_ids = live_module.slot_vocabulary(lock)

    tampered = dict(lock)
    tampered["population_fixation_prob"] = {
        slot_id: float(i) for i, slot_id in enumerate(slot_ids)
    }

    with pytest.raises(live_module.StalePredictionLock) as excinfo:
        live_module.synthetic_vector(tampered, planogram, slot_ids, mode="webcam")
    assert "population_fixation_prob" in str(excinfo.value)


def test_a_lock_with_a_foreign_sim_run_id_is_refused(client, predictions_dir):
    _, lock = create_session(client, predictions_dir)
    planogram = resolved_planogram(client, lock["variant_id"])
    slot_ids = live_module.slot_vocabulary(lock)

    tampered = dict(lock)
    tampered["sim_run_id"] = "0123456789ab"

    with pytest.raises(live_module.StalePredictionLock) as excinfo:
        live_module.synthetic_vector(tampered, planogram, slot_ids, mode="webcam")
    assert "sim_run_id" in str(excinfo.value)


def test_open_state_refuses_a_stale_lock(client, predictions_dir):
    session_id, lock = create_session(client, predictions_dir)
    tampered = dict(lock)
    tampered["sim_run_id"] = "0123456789ab"

    with pytest.raises(live_module.StalePredictionLock):
        open_live_state(client, session_id, tampered, mode="webcam")


def test_ws_session_refused_when_the_lock_is_stale(client, predictions_dir):
    """End to end: a stale lock closes the ingest socket instead of measuring."""
    session_id, lock = create_session(client, predictions_dir)
    stale = dict(lock)
    stale["sim_run_id"] = "0123456789ab"
    (predictions_dir / f"{session_id}.json").write_text(
        json.dumps(stale, indent=2, sort_keys=True), encoding="utf-8"
    )

    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect(f"/ws/session/{session_id}"):
            pass
    assert excinfo.value.code == ws_module.CLOSE_PREDICTION_LOCK_STALE


def test_the_lock_hash_still_verifies_and_gained_no_hashed_fields(client, predictions_dir):
    """The pre-registration is untouched: same three fields, same digest."""
    _, lock = create_session(client, predictions_dir)

    assert lock["sha256"] == prediction_module.compute_sha256(
        lock["population_fixation_prob"], lock["sim_run_id"], lock["created_at"]
    )
    # The document on disk carries exactly the SPEC 4.6 fields -- nothing this
    # change needed was added to it, hashed or otherwise.
    assert set(lock) == {
        "prediction_id", "session_id", "variant_id", "sim_run_id", "created_at",
        "population_fixation_prob", "sha256", "git_commit",
    }


def test_the_synthetic_vector_is_computed_once_not_per_batch(client, predictions_dir,
                                                             monkeypatch):
    """No simulation, and no database read, on the hot path.

    `open_state` pays for the comparison vector once; every `fold` after that
    must be pure in-memory work. Making the simulator explode proves it.
    """
    session_id, lock = create_session(client, predictions_dir)
    slot_ids = live_module.slot_vocabulary(lock)
    state = open_live_state(client, session_id, lock, mode="webcam")

    def explode(*args, **kwargs):
        raise AssertionError("simcache.population called on the hot path")

    monkeypatch.setattr(simcache, "population", explode)
    message = state.fold([fixation(slot_ids[0], 100)])
    assert message["spearman"] == state.snapshot()["spearman"]


# ===========================================================================
# DEFECT 2 - `meaningful` reflects the evidence the session actually produces
# ===========================================================================


def cursor_dwell(slot_id: str, t_ms: int) -> dict:
    return {"t_ms": t_ms, "type": "cursor_dwell", "station_id": "B1",
            "payload": {"slot_id": slot_id, "dur_ms": 320}}


def test_a_cursor_only_session_becomes_meaningful_at_exactly_fifteen_dwells(
    client, predictions_dir, capsys
):
    """The demo's only session type must be able to turn the meter on.

    SPEC 4.7 counts fixations; a cursor_only session has none by construction,
    so it counts the channel that carries 70 % of its attention formula.
    """
    session_id, lock = create_session(client, predictions_dir, mode="cursor_only")
    slot_ids = live_module.slot_vocabulary(lock)
    state = open_live_state(client, session_id, lock, mode="cursor_only")

    assert live_module.MEANINGFUL_MIN_EVIDENCE == 15

    for i in range(14):
        message = state.fold([cursor_dwell(slot_ids[i % len(slot_ids)], 100 * i)])
        assert message["evidence_count"] == i + 1
        assert message["evidence_kind"] == "cursor_dwells"
        assert message["meaningful"] is False, f"meaningful at {i + 1} dwells"

    message = state.fold([cursor_dwell(slot_ids[0], 9999)])
    with capsys.disabled():
        print(f"\n[defect 2/cursor_only] meaningful flips at "
              f"{message['evidence_count']} {message['evidence_kind']}")
    assert message["evidence_count"] == 15
    assert message["meaningful"] is True

    message = state.fold([cursor_dwell(slot_ids[1], 10_000)])
    assert message["evidence_count"] == 16
    assert message["meaningful"] is True


def test_fixations_do_not_make_a_cursor_only_session_meaningful(client, predictions_dir):
    """A cursor_only session's gaze trail is empty; a stray fixation is not
    evidence its meter may count."""
    session_id, lock = create_session(client, predictions_dir, mode="cursor_only")
    slot_ids = live_module.slot_vocabulary(lock)
    state = open_live_state(client, session_id, lock, mode="cursor_only")

    message = state.fold([fixation(slot_ids[0], 100 * i) for i in range(50)])
    assert message["n_fixations"] == 50
    assert message["evidence_count"] == 0
    assert message["meaningful"] is False


def test_cursor_dwells_do_not_make_a_webcam_session_meaningful(client, predictions_dir):
    """SPEC 4.7, unchanged, for the mode it was written for."""
    session_id, lock = create_session(client, predictions_dir, mode="webcam")
    slot_ids = live_module.slot_vocabulary(lock)
    state = open_live_state(client, session_id, lock, mode="webcam")

    message = state.fold([cursor_dwell(slot_ids[0], 100 * i) for i in range(50)])
    assert message["n_cursor_dwells"] == 50
    assert message["evidence_count"] == 0
    assert message["evidence_kind"] == "fixations"
    assert message["meaningful"] is False


def test_every_count_in_the_message_describes_what_it_holds(client, predictions_dir):
    """No mislabelled counts: each field is checkable against the stream."""
    for mode, kind in (("webcam", "fixations"), ("cursor_only", "cursor_dwells")):
        session_id, lock = create_session(client, predictions_dir, mode=mode)
        slot_ids = live_module.slot_vocabulary(lock)
        state = open_live_state(client, session_id, lock, mode=mode)

        events = ([fixation(slot_ids[0], 10 * i) for i in range(3)]
                  + [cursor_dwell(slot_ids[1], 1000 + 10 * i) for i in range(7)])
        message = state.fold(events)

        assert message["n_fixations"] == 3
        assert message["n_cursor_dwells"] == 7
        assert message["evidence_kind"] == kind
        assert message["evidence_count"] == (
            message["n_fixations"] if kind == "fixations" else message["n_cursor_dwells"]
        )
