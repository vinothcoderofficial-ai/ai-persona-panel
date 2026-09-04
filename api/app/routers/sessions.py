"""POST /sessions, POST /sessions/{id}/events, POST /sessions/{id}/finish.

The session gate itself (accept/reject) is computed in the browser (S11) -
this router only persists what it is given. The prediction lock is S14 and
is out of scope here.
"""
import json
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from api.app.db import EventRecord, SessionRecord, get_session, get_validator

router = APIRouter(tags=["sessions"])

_FINISH_FIELDS = ("ended_at", "quality", "accepted", "reject_reason")


def _validation_detail(errors) -> str:
    return "; ".join(f"{'/'.join(str(p) for p in e.path)}: {e.message}" for e in errors)


@router.post("/sessions", status_code=201)
def create_session(body: Dict[str, Any], session: Session = Depends(get_session)):
    validator = get_validator("session.schema.json")
    errors = sorted(validator.iter_errors(body), key=str)
    if errors:
        raise HTTPException(
            status_code=422, detail=f"invalid session: {_validation_detail(errors)}"
        )

    session_id = body["session_id"]
    record = session.get(SessionRecord, session_id)
    if record is None:
        record = SessionRecord(session_id=session_id, data=json.dumps(body))
    else:
        record.data = json.dumps(body)
    session.add(record)
    session.commit()
    return body


@router.post("/sessions/{session_id}/events")
def post_events(
    session_id: str,
    body: List[Dict[str, Any]],
    session: Session = Depends(get_session),
):
    record = session.get(SessionRecord, session_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"unknown session_id {session_id!r}")

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
