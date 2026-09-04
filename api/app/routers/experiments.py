"""POST /experiments, GET /experiments/{experiment_id}.

S5 wires this endpoint to the rest of the stack: resolve() the variant against
its base planogram (api/app/resolve.py), run the vectorised simulator for
each persona (sim/simulator.py) and combine() them into one share-weighted
population SimResult, fuse the real session's events into a comparable
per-slot attention vector (analytics/fusion.py -- the only implementation of
that formula, per CLAUDE.md), and score the two against each other
(analytics/metrics.py -- the only implementation of those metrics). Nothing
here reimplements any of that maths; this module only wires it together and
persists the result.

The response intentionally does NOT claim to satisfy schemas/metrics.schema.json.
That schema is the full cross-variant evaluation (noise ceiling, decision
agreement, holdout variants, accepted/rejected counts), which is S17-S19's
job -- filling those fields with invented numbers here would be exactly the
placeholder CLAUDE.md forbids. This endpoint returns a smaller, honest shape
instead: see the return statement of `_build_experiment` below.
"""
import json
import uuid
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from analytics.fusion import fuse_session
from analytics.metrics import attention_spearman, purchase_share_mae
from api.app.db import (
    ROOT,
    EventRecord,
    ExperimentRecord,
    PlanogramRecord,
    SessionRecord,
    VariantRecord,
    get_session,
)
from api.app.resolve import PatchError, resolve
from sim.simulator import build_store, combine, run

router = APIRouter(tags=["experiments"])

PERSONAS_DIR = ROOT / "data" / "personas"
POLICIES_DIR = ROOT / "data" / "cache" / "policies"

# Fixed simulation resolution for every experiment (S5 task brief): each of
# the four personas is run this many times, at this seed, before being
# combined into the population result. `n_synth` in the response reports
# this per-persona configuration value -- NOT combine()'s own summed
# `n_runs` (which is N_RUNS * 4 personas = 40,000) -- so it always reads
# 10,000 regardless of how many personas exist on disk.
N_RUNS = 10_000
SEED = 42


def _occupied_slot_ids(planogram: Dict[str, Any]) -> List[str]:
    """Occupied slot ids (sku_id is not null), in planogram order.

    This is the shared slot vocabulary that real and synthetic attention are
    both built over, so the two vectors stay aligned and comparable.
    """
    return [
        slot["slot_id"]
        for bay in planogram["bays"]
        for shelf in bay["shelves"]
        for slot in shelf["slots"]
        if slot["sku_id"] is not None
    ]


def _load_personas() -> List[Dict[str, Any]]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(PERSONAS_DIR.glob("*.json"))
    ]


def _load_policy(persona_id: str, planogram_id: str) -> Dict[str, Any]:
    path = POLICIES_DIR / f"{persona_id}_{planogram_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _real_purchase_share(events: List[Dict[str, Any]]) -> Dict[str, float]:
    """Real purchase share from this session's `add_to_cart` events: a count
    per sku_id divided by the total count, so it sums to 1. Empty (reads as
    all zero downstream, via the same missing-key-means-0 convention every
    comparison in analytics/metrics.py already uses) when there were none --
    no division by zero, no invented share.
    """
    counts: Dict[str, int] = {}
    for event in events:
        if event.get("type") != "add_to_cart":
            continue
        sku_id = (event.get("payload") or {}).get("sku_id")
        if sku_id is not None:
            counts[sku_id] = counts.get(sku_id, 0) + 1

    total = sum(counts.values())
    if total == 0:
        return {}
    return {sku_id: count / total for sku_id, count in counts.items()}


def _build_experiment(variant_id: str, session_id: str, db_session: Session) -> Dict[str, Any]:
    """Everything POST /experiments does before it has a document to persist."""
    variant_record = db_session.get(VariantRecord, variant_id)
    if variant_record is None:
        raise HTTPException(status_code=404, detail=f"unknown variant_id {variant_id!r}")

    session_record = db_session.get(SessionRecord, session_id)
    if session_record is None:
        raise HTTPException(status_code=404, detail=f"unknown session_id {session_id!r}")

    variant = json.loads(variant_record.data)
    planogram_record = db_session.get(PlanogramRecord, variant["base_planogram_id"])
    if planogram_record is None:
        raise HTTPException(
            status_code=404,
            detail=f"unknown base_planogram_id {variant['base_planogram_id']!r}",
        )
    base = json.loads(planogram_record.data)

    try:
        resolved = resolve(base, variant)
    except PatchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    slot_ids = _occupied_slot_ids(resolved)
    store = build_store(resolved)

    results = []
    shares = []
    for persona in _load_personas():
        policy = _load_policy(persona["persona_id"], resolved["planogram_id"])
        results.append(run(store, policy, n_runs=N_RUNS, seed=SEED, variant_id=variant_id))
        shares.append(persona["share_of_population"])
    population = combine(results, shares)

    event_rows = db_session.exec(
        select(EventRecord).where(EventRecord.session_id == session_id)
    ).all()
    events = [json.loads(row.data) for row in event_rows]

    real_attention = fuse_session(events, slot_ids)
    synth_attention = {
        slot_id: population["fixation_prob"].get(slot_id, 0.0) for slot_id in slot_ids
    }

    real_purchase_share = _real_purchase_share(events)
    synth_purchase_share = population["purchase_share"]

    return {
        "variant_id": variant_id,
        "session_id": session_id,
        "n_synth": N_RUNS,
        "seed": SEED,
        "slot_ids": slot_ids,
        "real_attention": real_attention,
        "synth_attention": synth_attention,
        "attention_spearman": attention_spearman(real_attention, synth_attention, slot_ids),
        "purchase_share_mae": purchase_share_mae(real_purchase_share, synth_purchase_share),
        "real_purchase_share": real_purchase_share,
        "synth_purchase_share": synth_purchase_share,
    }


@router.post("/experiments", status_code=201)
def create_experiment(body: Dict[str, Any], session: Session = Depends(get_session)):
    variant_id = body.get("variant_id")
    session_id = body.get("session_id")
    if not isinstance(variant_id, str) or not isinstance(session_id, str):
        raise HTTPException(
            status_code=422,
            detail="experiment body requires string fields 'variant_id' and 'session_id'",
        )

    result = _build_experiment(variant_id, session_id, session)

    experiment_id = f"exp_{uuid.uuid4().hex[:12]}"
    record_data = {"experiment_id": experiment_id, **result}
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
