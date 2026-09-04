"""POST /variants, GET /variants/{variant_id}/resolved.

resolve() itself lives only in api/app/resolve.py; these routes just load the
stored documents and call it.
"""
import json
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from api.app.db import PlanogramRecord, VariantRecord, get_session, get_validator
from api.app.resolve import PatchError, resolve

router = APIRouter(tags=["variants"])


def _validation_detail(errors) -> str:
    return "; ".join(f"{'/'.join(str(p) for p in e.path)}: {e.message}" for e in errors)


def _load_planogram(session: Session, planogram_id: str) -> Dict[str, Any]:
    record = session.get(PlanogramRecord, planogram_id)
    if record is None:
        raise HTTPException(
            status_code=404, detail=f"unknown base_planogram_id {planogram_id!r}"
        )
    return json.loads(record.data)


@router.post("/variants", status_code=201)
def create_variant(body: Dict[str, Any], session: Session = Depends(get_session)):
    validator = get_validator("variant.schema.json")
    errors = sorted(validator.iter_errors(body), key=str)
    if errors:
        raise HTTPException(
            status_code=422, detail=f"invalid variant: {_validation_detail(errors)}"
        )

    base = _load_planogram(session, body["base_planogram_id"])

    try:
        resolved = resolve(base, body)
    except PatchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    variant_id = body["variant_id"]
    record = session.get(VariantRecord, variant_id)
    if record is None:
        record = VariantRecord(variant_id=variant_id, data=json.dumps(body))
    else:
        record.data = json.dumps(body)
    session.add(record)
    session.commit()
    return resolved


@router.get("/variants/{variant_id}/resolved")
def get_resolved_variant(variant_id: str, session: Session = Depends(get_session)):
    record = session.get(VariantRecord, variant_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"unknown variant_id {variant_id!r}")

    variant = json.loads(record.data)
    base = _load_planogram(session, variant["base_planogram_id"])

    try:
        return resolve(base, variant)
    except PatchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
