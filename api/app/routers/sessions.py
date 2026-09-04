"""POST /sessions, POST /sessions/{id}/events, POST /sessions/{id}/finish.

The session gate itself (accept/reject) is computed in the browser (S11) -
this router only persists what it is given.

What this router *does* own is the ordering CLAUDE.md calls non-negotiable:
**the prediction lock is written on POST /sessions, before any event is
accepted.** Two things enforce it structurally rather than by convention:

  1. `create_session` writes `predictions/{session_id}.json` BEFORE it writes
     the session row, so a session that exists in the database always has a
     lock, and a failed simulation leaves neither.
  2. `post_events` refuses (409) a session with no lock, so an event can never
     be recorded ahead of the commitment it will be judged against. The same
     check guards the websocket ingest in `routers/ws.py`.

`scripts/eval.py` (S19) re-verifies this from the committed files; see the
module docstring of `api/app/prediction.py` for what it can and cannot compare
`created_at` against.
"""
import json
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from api.app import prediction
from api.app.db import (
    EventRecord,
    PlanogramRecord,
    SessionRecord,
    VariantRecord,
    get_session,
    get_validator,
)
from api.app.resolve import PatchError, resolve

router = APIRouter(tags=["sessions"])

_FINISH_FIELDS = ("ended_at", "quality", "accepted", "reject_reason")


def _validation_detail(errors) -> str:
    return "; ".join(f"{'/'.join(str(p) for p in e.path)}: {e.message}" for e in errors)


def _resolved_variant(session: Session, variant_id: str) -> Dict[str, Any]:
    """The resolved planogram a prediction for `variant_id` is computed over.

    resolve() lives only in api/app/resolve.py (CLAUDE.md); this loads the two
    documents and calls it.
    """
    variant_record = session.get(VariantRecord, variant_id)
    if variant_record is None:
        raise HTTPException(status_code=404, detail=f"unknown variant_id {variant_id!r}")
    variant = json.loads(variant_record.data)

    planogram_record = session.get(PlanogramRecord, variant["base_planogram_id"])
    if planogram_record is None:
        raise HTTPException(
            status_code=404,
            detail=f"unknown base_planogram_id {variant['base_planogram_id']!r}",
        )

    try:
        return resolve(json.loads(planogram_record.data), variant)
    except PatchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/sessions", status_code=201)
def create_session(body: Dict[str, Any], session: Session = Depends(get_session)):
    """Register a shopper and lock the synthetic prediction they will be scored against.

    The variant is resolved and simulated first: a session that cannot be given
    a lock is refused outright rather than created in a state where it could
    never accept an event. The response carries `prediction_id` (also stored on
    the session document, where schemas/session.schema.json has a field for it)
    plus the badge the spectator screen displays - the first 8 hex characters
    of the hash and `created_at` (SPEC 4.6).
    """
    validator = get_validator("session.schema.json")
    errors = sorted(validator.iter_errors(body), key=str)
    if errors:
        raise HTTPException(
            status_code=422, detail=f"invalid session: {_validation_detail(errors)}"
        )

    session_id = body["session_id"]
    variant_id = body["variant_id"]
    resolved = _resolved_variant(session, variant_id)

    # Before the session row exists, so no event can ever precede it. Returns
    # the original document unchanged if this session was registered before -
    # a lock is evidence and is never re-timestamped.
    lock = prediction.write_lock(session_id, variant_id, resolved)

    stored = {**body, "prediction_id": lock["prediction_id"]}
    record = session.get(SessionRecord, session_id)
    if record is None:
        record = SessionRecord(session_id=session_id, data=json.dumps(stored))
    else:
        record.data = json.dumps(stored)
    session.add(record)
    session.commit()

    return {
        **stored,
        "prediction": {
            "prediction_id": lock["prediction_id"],
            "sha256_prefix": lock["sha256"][:8],
            "created_at": lock["created_at"],
            "sim_run_id": lock["sim_run_id"],
        },
    }


@router.post("/sessions/{session_id}/events")
def post_events(
    session_id: str,
    body: List[Dict[str, Any]],
    session: Session = Depends(get_session),
):
    record = session.get(SessionRecord, session_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"unknown session_id {session_id!r}")

    # The non-negotiable ordering, checked on every batch rather than assumed:
    # no locked prediction, no events. A session created through POST /sessions
    # always has one, so reaching this means the lock was removed or the row
    # was created some other way - either way the data would be unusable as
    # evidence and is refused instead of silently accepted.
    if not prediction.lock_exists(session_id):
        raise HTTPException(
            status_code=409,
            detail=(
                f"session {session_id!r} has no prediction lock; events cannot be "
                "accepted before the prediction is locked"
            ),
        )

    validator = get_validator("event.schema.json")
    for i, event in enumerate(body):
        errors = sorted(validator.iter_errors(event), key=str)
        if errors:
            raise HTTPException(
                status_code=422,
                detail=f"invalid event at index {i}: {_validation_detail(errors)}",
            )

    for event in body:
        session.add(EventRecord(session_id=session_id, data=json.dumps(event)))
    session.commit()
    return {"accepted": len(body)}


@router.post("/sessions/{session_id}/finish")
def finish_session(
    session_id: str, body: Dict[str, Any], session: Session = Depends(get_session)
):
    record = session.get(SessionRecord, session_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"unknown session_id {session_id!r}")

    stored = json.loads(record.data)
    for field in _FINISH_FIELDS:
        if field in body:
            stored[field] = body[field]

    validator = get_validator("session.schema.json")
    errors = sorted(validator.iter_errors(stored), key=str)
    if errors:
        raise HTTPException(
            status_code=422,
            detail=f"invalid session after finish: {_validation_detail(errors)}",
        )

    record.data = json.dumps(stored)
    session.add(record)
    session.commit()
    return stored
