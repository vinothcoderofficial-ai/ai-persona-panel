"""POST /experiments, GET /experiments/{experiment_id}.

Running the simulator and computing metrics is S5 and is out of scope here;
this router's whole job is to persist {variant_id, session_id} under a
generated experiment_id and hand it back. S5 extends this endpoint.
"""
import json
import uuid
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from api.app.db import ExperimentRecord, get_session

router = APIRouter(tags=["experiments"])


@router.post("/experiments", status_code=201)
def create_experiment(body: Dict[str, Any], session: Session = Depends(get_session)):
    variant_id = body.get("variant_id")
    session_id = body.get("session_id")
    if not isinstance(variant_id, str) or not isinstance(session_id, str):
        raise HTTPException(
            status_code=422,
            detail="experiment body requires string fields 'variant_id' and 'session_id'",
        )

    experiment_id = f"exp_{uuid.uuid4().hex[:12]}"
    record_data = {
        "experiment_id": experiment_id,
        "variant_id": variant_id,
        "session_id": session_id,
    }
    session.add(ExperimentRecord(experiment_id=experiment_id, data=json.dumps(record_data)))
    session.commit()
    return record_data


@router.get("/experiments/{experiment_id}")
def get_experiment(experiment_id: str, session: Session = Depends(get_session)):
    record = session.get(ExperimentRecord, experiment_id)
    if record is None:
        raise HTTPException(
            status_code=404, detail=f"unknown experiment_id {experiment_id!r}"
        )
    return json.loads(record.data)
