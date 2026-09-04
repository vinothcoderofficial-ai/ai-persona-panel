"""The real-time layer: `ws/session/{id}` in, `ws/spectator/{id}` out (SPEC M9).

    browser ──batches──▶ ws/session/{id} ──▶ live.LiveState.fold()
                                │                     │
                                │                     ▼
                                │            SPEC 4.7 message
                                ▼                     │
                       EventRecord (every 2 s)        ▼
                                            ws/spectator/{id} × N

**No acks.** docs/PLAN.md 13 overrides the SPEC here: "WebSocket acks,
zero-loss test -> Cut. Plain WS + local buffer + REST fallback." The browser
keeps the authoritative buffer and falls back to `POST /sessions/{id}/events`,
so nothing on this socket is confirmed, numbered or retransmitted. The only
frame this endpoint ever sends back is an `{"error": ...}` diagnostic for a
batch it could not accept - that is a rejection notice, not an
acknowledgement: no batch ids, nothing waits on it, and a client that ignores
it entirely still works.

**The lock comes first.** A session with no `predictions/{id}.json` is refused
at connect, before the socket is even accepted - the same rule
`POST /sessions/{id}/events` enforces (CLAUDE.md: locks are written on
`POST /sessions`, before any event is accepted).

**No database reads on the hot path.** The session document and the lock are
read once, at connect, to build the `LiveState`. After that a batch costs a
fold and a broadcast; the only database work is an append, and even that is
batched to at most once every 2 s.
"""
from __future__ import annotations

import json
import logging
import random
import time
from typing import Any, Dict, List, Optional, Sequence, Set

import anyio
from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlmodel import Session

from api.app import live, prediction
from api.app.db import PLANOGRAMS_DIR, EventRecord, SessionRecord, get_session, get_validator

router = APIRouter(tags=["ws"])
log = logging.getLogger(__name__)

# Application close codes (the 4000-4999 range is reserved for exactly this).
CLOSE_UNKNOWN_SESSION = 4404
CLOSE_NO_PREDICTION_LOCK = 4409

# SPEC M9: append to the database at most this often.
FLUSH_INTERVAL_S = 2.0

# Fake-stream constants. They are constants, and not derived from the request,
# precisely so a fake frame can never be mistaken for a real one.
FAKE_SESSION_ID = "fake-session"
FAKE_PREDICTION_ID = "fake-prediction"
FAKE_INTERVAL_S = 0.05

_spectators: Dict[str, Set[WebSocket]] = {}


# ---------------------------------------------------------------------------
# Spectator fan-out
# ---------------------------------------------------------------------------


def _subscribe(session_id: str, websocket: WebSocket) -> None:
    _spectators.setdefault(session_id, set()).add(websocket)


def _unsubscribe(session_id: str, websocket: WebSocket) -> None:
    watchers = _spectators.get(session_id)
    if watchers is None:
        return
    watchers.discard(websocket)
    if not watchers:
        _spectators.pop(session_id, None)


async def broadcast(session_id: str, message: Dict[str, Any]) -> None:
    """Push one SPEC 4.7 message to every spectator of this session.

    A spectator whose window was closed mid-demo is dropped and the send moves
    on. Nothing here may raise: the shopper's ingest socket calls this on every
    batch, and a dead second monitor must never be able to interrupt the person
    actually shopping.
    """
    for websocket in list(_spectators.get(session_id, ())):
        try:
            await websocket.send_json(message)
        except Exception:  # noqa: BLE001 - a dead spectator is not an ingest failure
            _unsubscribe(session_id, websocket)


# ---------------------------------------------------------------------------
# ws/session/{id} - event ingest
# ---------------------------------------------------------------------------


@router.websocket("/ws/session/{session_id}")
async def session_socket(
    websocket: WebSocket,
    session_id: str,
    db: Session = Depends(get_session),
) -> None:
    """Receive event batches, fuse them live, persist them every 2 s."""
    record = db.get(SessionRecord, session_id)
    if record is None:
        await websocket.close(code=CLOSE_UNKNOWN_SESSION,
                              reason=f"unknown session_id {session_id!r}")
        return

    lock = prediction.read_lock(session_id)
    if lock is None:
        # The prediction must exist before a single event is accepted.
        await websocket.close(
            code=CLOSE_NO_PREDICTION_LOCK,
            reason=f"session {session_id!r} has no prediction lock",
        )
        return

    session_document = json.loads(record.data)
    state = live.open_state(session_id, mode=session_document["mode"], lock=lock)
    validator = get_validator("event.schema.json")

    await websocket.accept()

    pending: List[Dict[str, Any]] = []
    last_flush = time.monotonic()
    try:
        while True:
            raw = await websocket.receive_text()
            events, error = _parse_batch(raw, validator)
            if error is not None:
                await websocket.send_json({"error": error})
                continue

            message = state.fold(events)
            pending.extend(events)
            await broadcast(session_id, message)

            now = time.monotonic()
            if now - last_flush >= FLUSH_INTERVAL_S:
                _flush(db, session_id, pending)
                last_flush = now
    except WebSocketDisconnect:
        pass
    finally:
        # Whatever is still buffered belongs in the database, disconnect or not.
        _flush(db, session_id, pending)


def _parse_batch(raw: str, validator) -> tuple[List[Dict[str, Any]], Optional[str]]:
    """Decode one `{"events": [...]}` frame, or say why it was not one.

    The batch is all-or-nothing, matching `POST /sessions/{id}/events`: a frame
    with one bad event is rejected whole rather than half-recorded, so the
    stored session is always exactly what the browser believes it sent.
    """
    try:
        body = json.loads(raw)
    except (TypeError, ValueError):
        return [], "batch is not valid JSON"
    if not isinstance(body, dict) or not isinstance(body.get("events"), list):
        return [], "batch must be an object of the form {\"events\": [...]}"

    events = body["events"]
    for i, event in enumerate(events):
        if not isinstance(event, dict):
            return [], f"event at index {i} is not an object"
        errors = sorted(validator.iter_errors(event), key=str)
        if errors:
            detail = "; ".join(
                f"{'/'.join(str(p) for p in e.path)}: {e.message}" for e in errors
            )
            return [], f"invalid event at index {i}: {detail}"
    return events, None


def _flush(db: Session, session_id: str, pending: List[Dict[str, Any]]) -> None:
    """Append the buffered events and clear the buffer."""
    if not pending:
        return
    for event in pending:
        db.add(EventRecord(session_id=session_id, data=json.dumps(event)))
    db.commit()
    pending.clear()


# ---------------------------------------------------------------------------
# ws/spectator/{id} - live fan-out to the second screen
# ---------------------------------------------------------------------------


@router.websocket("/ws/spectator/{session_id}")
async def spectator_socket(
    websocket: WebSocket,
    session_id: str,
    fake: int = Query(0, description="1 streams synthetic demo data, see fake_stream()"),
) -> None:
    """Subscribe to a session's live updates, or to the fake demo stream.

    A spectator that joins mid-session is sent the current snapshot
    immediately, so the heatmap and the prediction badge are populated on the
    first frame rather than staying blank until the shopper's next batch.
    """
    await websocket.accept()

    if fake:
        await fake_stream(websocket)
        return

    _subscribe(session_id, websocket)
    try:
        state = live.get_state(session_id)
        if state is not None:
            await websocket.send_json(state.snapshot())
        while True:
            # Spectators never send anything; this is how the endpoint parks
            # until the client goes away.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _unsubscribe(session_id, websocket)


# ---------------------------------------------------------------------------
# The fake stream (docs/PLAN.md S14: ship ws/spectator with fake data on Day 3
# so Track A is not blocked waiting for a live shopper)
# ---------------------------------------------------------------------------


async def fake_stream(websocket: WebSocket) -> None:
    """Stream synthetic SPEC 4.7 messages so the spectator UI can be built.

    Every frame is marked three ways, so no screenshot, log line or recorded
    demo can pass this off as a measurement:

      * `"fake": true` - a field the real path never sets;
      * `session_id` is the constant "fake-session", never the requested id;
      * `prediction_id` is the constant "fake-prediction", which matches no
        lock file that exists.

    The numbers are a seeded random walk over the demo aisle's real slot ids,
    so the heatmap has something plausibly shaped to render, and `meaningful`
    flips at the same 15-fixation boundary the real engine uses.
    """
    log.warning("ws/spectator opened in FAKE mode - these frames are not measurements")
    rng = random.Random(20260903)
    slot_ids = _fake_slot_ids()
    attention = {slot_id: rng.random() for slot_id in slot_ids}
    t_ms = 0
    n_fixations = 0

    try:
        while True:
            t_ms += rng.randint(200, 600)
            n_fixations += rng.randint(0, 2)
            for slot_id in slot_ids:
                attention[slot_id] = max(0.0, attention[slot_id] + rng.uniform(-0.05, 0.05))
            total = sum(attention.values()) or 1.0

            await websocket.send_json({
                "session_id": FAKE_SESSION_ID,
                "t_ms": t_ms,
                "n_fixations": n_fixations,
                "stations_visited": min(3, 1 + t_ms // 20_000),
                "attention": {k: v / total for k, v in attention.items()},
                "latest_gaze": {"x": rng.randint(0, 1439), "y": rng.randint(0, 899)},
                "spearman": round(rng.uniform(-0.2, 0.9), 3),
                "meaningful": n_fixations >= live.MEANINGFUL_MIN_FIXATIONS,
                "prediction_id": FAKE_PREDICTION_ID,
                "fake": True,
            })
            await anyio.sleep(FAKE_INTERVAL_S)
    except WebSocketDisconnect:
        pass


def _fake_slot_ids() -> Sequence[str]:
    """The demo aisle's occupied slot ids, read straight off disk.

    Off disk and not out of the database, so the fake stream works on a machine
    that has never seeded or started anything - which is the whole point of it
    on Day 3. Falls back to the planogram's naming convention if the seed file
    is missing, because a stream that cannot start is worse than one with
    approximate ids while it is admittedly, loudly fake.
    """
    try:
        document = json.loads(
            (PLANOGRAMS_DIR / "demo_aisle.json").read_text(encoding="utf-8")
        )
        slot_ids = prediction.occupied_slot_ids(document)
        if slot_ids:
            return slot_ids
    except (OSError, ValueError, KeyError, TypeError):
        log.warning("fake spectator stream: demo_aisle.json unreadable, using generated ids")
    return [f"B{bay}S{shelf}P{pos}"
            for bay in (1, 2, 3) for shelf in (1, 2, 3, 4) for pos in (1, 2)]
