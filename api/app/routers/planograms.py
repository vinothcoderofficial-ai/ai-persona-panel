"""POST/GET /planograms, GET /planograms/{planogram_id}."""
import json
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from api.app.db import PlanogramRecord, get_session, get_validator

router = APIRouter(tags=["planograms"])


def _validation_detail(errors) -> str:
    return "; ".join(f"{'/'.join(str(p) for p in e.path)}: {e.message}" for e in errors)


@router.post("/planograms", status_code=201)
def create_planogram(body: Dict[str, Any], session: Session = Depends(get_session)):
    validator = get_validator("planogram.schema.json")
    errors = sorted(validator.iter_errors(body), key=str)
    if errors:
        raise HTTPException(
            status_code=422, detail=f"invalid planogram: {_validation_detail(errors)}"
        )

    planogram_id = body["planogram_id"]
    record = session.get(PlanogramRecord, planogram_id)
    if record is None:
        record = PlanogramRecord(planogram_id=planogram_id, data=json.dumps(body))
    else:
        record.data = json.dumps(body)
    session.add(record)
    session.commit()
    return body


@router.get("/planograms")
def list_planograms(session: Session = Depends(get_session)):
    records = session.exec(select(PlanogramRecord)).all()
    return sorted(r.planogram_id for r in records)


@router.get("/planograms/{planogram_id}")
def get_planogram(planogram_id: str, session: Session = Depends(get_session)):
    record = session.get(PlanogramRecord, planogram_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"unknown planogram_id {planogram_id!r}")
    return json.loads(record.data)
