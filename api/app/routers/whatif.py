"""POST /whatif - resolve a set of patches, simulate it, report the lift (SPEC M9, 4.8).

The pipeline is exactly the one in the SPEC: `resolve()` (api/app/resolve.py, the only resolver)
-> saliency -> simulator. Saliency is not called here directly: `sim.simulator.build_store()`
computes it internally and this module only needs the arrays build_store returns, so calling
`compute_saliency` again would be duplicated work on the hot path.

Nothing fixed is rebuilt per request. `warm_up()` runs from the app lifespan and loads the four
persona documents, their cached policies, and the *baseline* - the unpatched run of the planogram
that `lift_vs_baseline` is measured against. The baseline is deterministic for a given
(planogram content, n_synth, seed), so caching it means a what-if request pays for one simulation
instead of two. Every cache is also populated lazily, so a caller that never went through startup
still gets correct answers, just slower on its first call.

Two notes on honesty of the numbers:

* `sim_run_id` is the population run's deterministic id from `combine()`, not a fresh uuid. The
  same base planogram, patches, n_synth and seed must produce the same id, otherwise a what-if
  result cannot be reproduced or compared against a prediction lock.
* `lift_vs_baseline` only carries a key it actually computed. A patch set with no SKU in it (an
  ad-creative swap, say) has no focal SKU, so both focal keys are absent rather than 0 or null,
  and a baseline value of exactly 0 makes the relative change undefined, so that key is null
  rather than a fabricated number.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

from api.app import db as db_module
from api.app.db import ROOT, PlanogramRecord, get_session, get_validator
from api.app.resolve import PatchError, resolve
from sim.simulator import build_store, combine, run

router = APIRouter(tags=["whatif"])
log = logging.getLogger(__name__)

PERSONAS_DIR = ROOT / "data" / "personas"
POLICIES_DIR = ROOT / "data" / "cache" / "policies"

# SPEC 4.8 request defaults. MAX_N_SYNTH keeps a single request from being asked for an unbounded
# simulation - the p95 < 1,000 ms budget is only meaningful with a ceiling on the work.
DEFAULT_N_SYNTH = 10_000
DEFAULT_SEED = 42
MAX_N_SYNTH = 50_000

# The planogram warmed at startup: the demo aisle the what-if UI opens on.
WARM_PLANOGRAM_ID = "demo_aisle"

# One lock guards all three module caches. It is only ever held around the dict access itself,
# never across a simulation - load_personas() and load_policy() take it too, and a plain Lock is
# not reentrant. Two concurrent cold requests can therefore both compute the same baseline; the
# setdefault in get_baseline() keeps whichever landed first, so the cached object stays stable.
_cache_lock = threading.Lock()
_personas: Optional[List[Dict[str, Any]]] = None
_policies: Dict[Tuple[str, str], Dict[str, Any]] = {}
_baselines: Dict[Tuple[str, int, int], "Baseline"] = {}


class WhatIfRequest(BaseModel):
    """SPEC 4.8 request body. `patches` are validated against schemas/variant.schema.json
    separately - they are the cross-track contract, and jsonschema is what owns it."""

    model_config = ConfigDict(extra="forbid")

    base_planogram_id: str
    patches: List[Dict[str, Any]]
    n_synth: int = Field(default=DEFAULT_N_SYNTH, ge=1, le=MAX_N_SYNTH)
    seed: int = DEFAULT_SEED
    focal_sku_id: Optional[str] = None


@dataclass(frozen=True)
class Baseline:
    """The unpatched run of one planogram: what `lift_vs_baseline` is measured against.

    The id it was run under is not a separate field - it is already on every SimResult in here,
    as `population["variant_id"]`.
    """

    per_persona: Dict[str, Dict[str, Any]]
    population: Dict[str, Any]
    slot_of_sku: Dict[str, str]


# ---------------------------------------------------------------------------
# Warm caches
# ---------------------------------------------------------------------------


def load_personas() -> List[Dict[str, Any]]:
    """The persona documents, in filename order, carrying `share_of_population`."""
    global _personas
    with _cache_lock:
        if _personas is None:
            _personas = [
                json.loads(path.read_text(encoding="utf-8"))
                for path in sorted(PERSONAS_DIR.glob("*.json"))
            ]
        return _personas


def load_policy(persona_id: str, planogram_id: str) -> Dict[str, Any]:
    """One cached persona policy. Raises FileNotFoundError if this planogram has no policy for
    this persona - the endpoint turns that into a 404 and warm_up() into a warning."""
    key = (persona_id, planogram_id)
    with _cache_lock:
        policy = _policies.get(key)
        if policy is None:
            path = POLICIES_DIR / f"{persona_id}_{planogram_id}.json"
            if not path.exists():
                raise FileNotFoundError(
                    f"no cached policy for persona {persona_id!r} on planogram {planogram_id!r}"
                )
            policy = json.loads(path.read_text(encoding="utf-8"))
            _policies[key] = policy
        return policy


def get_baseline(base: Dict[str, Any], n_synth: int, seed: int) -> Baseline:
    """The unpatched run of `base` at this resolution, computed once and cached.

    Keyed on the planogram's *content*, not just its id: POST /planograms can replace a planogram
    under the same id, and a stale baseline would silently mis-state every lift afterwards.
    """
    key = (_document_hash(base), int(n_synth), int(seed))
    with _cache_lock:
        cached = _baselines.get(key)
    if cached is not None:
        return cached

    variant_id = _variant_id(key[0], [])
    # Run it through resolve() with no patches rather than using `base` directly, so the baseline
    # comes off exactly the same code path a patched request does.
    resolved = resolve(base, _variant_document(base["planogram_id"], variant_id, []))
    per_persona, population = _simulate(resolved, variant_id, n_synth, seed)
    baseline = Baseline(
        per_persona=per_persona,
        population=population,
        slot_of_sku=_slot_of_sku(resolved),
    )

    with _cache_lock:
        # Another thread may have finished the same baseline while this one ran; keep whichever
        # landed first so callers comparing identity see one object per key.
        return _baselines.setdefault(key, baseline)


def warm_up() -> None:
    """Load the personas, their policies and the demo aisle's baseline at application startup.

    Best effort by design: a deployment whose seed data does not include the demo aisle, or whose
    policy cache has not been generated yet, still starts - the other routers do not depend on
    any of this, and POST /whatif reports the missing piece per request.
    """
    load_personas()

    with Session(db_module.engine) as session:
        record = session.get(PlanogramRecord, WARM_PLANOGRAM_ID)
    if record is None:
        log.warning("whatif warm-up skipped: planogram %r is not in the database",
                    WARM_PLANOGRAM_ID)
        return

    try:
        get_baseline(json.loads(record.data), DEFAULT_N_SYNTH, DEFAULT_SEED)
    except FileNotFoundError as exc:
        log.warning("whatif warm-up skipped: %s", exc)


# ---------------------------------------------------------------------------
# The endpoint
# ---------------------------------------------------------------------------


@router.post("/whatif")
def post_whatif(body: WhatIfRequest, session: Session = Depends(get_session)):
    # Cheapest rejection first: the patches are the cross-track contract, so they are checked
    # against variant.schema.json before anything touches the database. The id here is a
    # stand-in that only exists to make the document schema-complete.
    validator = get_validator("variant.schema.json")
    candidate = _variant_document(body.base_planogram_id, "whatif_candidate", body.patches)
    errors = sorted(validator.iter_errors(candidate), key=str)
    if errors:
        raise HTTPException(
            status_code=422, detail=f"invalid patches: {_validation_detail(errors)}"
        )

    record = session.get(PlanogramRecord, body.base_planogram_id)
    if record is None:
        raise HTTPException(
            status_code=404, detail=f"unknown base_planogram_id {body.base_planogram_id!r}"
        )
    base = json.loads(record.data)

    focal_sku_id = body.focal_sku_id or _infer_focal_sku(body.patches)
    if focal_sku_id is not None and not any(s["sku_id"] == focal_sku_id for s in base["skus"]):
        raise HTTPException(status_code=400, detail=f"unknown focal_sku_id {focal_sku_id!r}")

    variant_id = _variant_id(_document_hash(base), body.patches)

    # elapsed_ms covers everything the answer costs, a cold baseline included - which is exactly
    # what warm_up() is for. Requests at a non-default n_synth or seed pay for their own baseline
    # once, because a lift measured against a baseline at a different seed would be seed noise.
    started = time.perf_counter()
    try:
        # Resolve before simulating anything: a patch naming something that does not exist is a
        # 400, and it should not first pay for a baseline run.
        resolved = resolve(base, _variant_document(base["planogram_id"], variant_id, body.patches))
        baseline = get_baseline(base, body.n_synth, body.seed)
        if body.patches:
            per_persona, population = _simulate(resolved, variant_id, body.n_synth, body.seed)
            slot_of_sku = _slot_of_sku(resolved)
        else:
            # No patches means the request *is* the baseline: same resolved planogram, same
            # variant_id, same seed, so re-running it could only reproduce the cached numbers.
            per_persona = baseline.per_persona
            population = baseline.population
            slot_of_sku = baseline.slot_of_sku
    except PatchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    lift = _lift_vs_baseline(focal_sku_id, baseline, population, slot_of_sku)
    elapsed_ms = int(round((time.perf_counter() - started) * 1000.0))

    return {
        "sim_run_id": population["sim_run_id"],
        "elapsed_ms": elapsed_ms,
        "per_persona": per_persona,
        "population_fixation_prob": population["fixation_prob"],
        "lift_vs_baseline": lift,
        "ad_slot_attention": population["ad_slot_attention"],
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _simulate(resolved: Dict[str, Any], variant_id: str, n_synth: int,
              seed: int) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    """Run every persona over `resolved` and combine them into the population result.

    build_store() computes the saliency layer; the policy reweighting and the Monte Carlo live in
    sim/simulator.py. No maths is duplicated here.
    """
    store = build_store(resolved)
    per_persona: Dict[str, Dict[str, Any]] = {}
    results: List[Dict[str, Any]] = []
    shares: List[float] = []

    for persona in load_personas():
        persona_id = persona["persona_id"]
        policy = load_policy(persona_id, resolved["planogram_id"])
        result = run(store, policy, n_runs=n_synth, seed=seed, variant_id=variant_id,
                     archetype=persona["archetype"])
        per_persona[persona_id] = result
        results.append(result)
        shares.append(float(persona["share_of_population"]))

    return per_persona, combine(results, shares)


def _lift_vs_baseline(focal_sku_id: Optional[str], baseline: Baseline,
                      population: Dict[str, Any],
                      slot_of_sku: Dict[str, str]) -> Dict[str, Optional[float]]:
    """Relative change in the focal SKU's population attention and purchase share.

    The SKU's *slot* is looked up in each resolved planogram separately, because a move_sku patch
    is exactly the case where it differs - comparing a fixed slot id would measure the old shelf
    position against the new occupant of it. Returns {} when there is no focal SKU to report on.
    """
    if focal_sku_id is None:
        return {}

    baseline_slot = baseline.slot_of_sku.get(focal_sku_id)
    patched_slot = slot_of_sku.get(focal_sku_id)
    baseline_attention = _attention(baseline.population, baseline_slot)
    patched_attention = _attention(population, patched_slot)

    return {
        "focal_sku_attention": _relative_change(baseline_attention, patched_attention),
        "focal_sku_purchase_share": _relative_change(
            float(baseline.population["purchase_share"].get(focal_sku_id, 0.0)),
            float(population["purchase_share"].get(focal_sku_id, 0.0)),
        ),
    }


def _attention(result: Dict[str, Any], slot_id: Optional[str]) -> float:
    """Population attention on one slot. An unplaced SKU has no slot, and an empty slot is never
    a fixation target, so either way the attention on it is exactly 0."""
    if slot_id is None:
        return 0.0
    return float(result["fixation_prob"].get(slot_id, 0.0))


def _relative_change(before: float, after: float) -> Optional[float]:
    """(after - before) / before, or None when `before` is 0 and the ratio is undefined."""
    if before == 0.0:
        return None
    return (after - before) / before


def _infer_focal_sku(patches: List[Dict[str, Any]]) -> Optional[str]:
    """The first SKU named by a patch. move_sku, swap_texture and set_price carry a `sku_id`;
    set_ad_creative does not, so an ad-only patch set yields None and no lift is reported."""
    for patch in patches:
        sku_id = patch.get("sku_id")
        if isinstance(sku_id, str):
            return sku_id
    return None


def _slot_of_sku(planogram: Dict[str, Any]) -> Dict[str, str]:
    """sku_id -> the slot it occupies in this resolved planogram."""
    return {
        slot["sku_id"]: slot["slot_id"]
        for bay in planogram["bays"]
        for shelf in bay["shelves"]
        for slot in shelf["slots"]
        if slot["sku_id"] is not None
    }


def _variant_document(planogram_id: str, variant_id: str,
                      patches: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The variant shape resolve() and variant.schema.json both expect. A what-if is an unsaved
    variant, so it is never written to the variants table."""
    return {
        "variant_id": variant_id,
        "base_planogram_id": planogram_id,
        "name": "what-if",
        "patches": patches,
    }


def _variant_id(base_hash: str, patches: List[Dict[str, Any]]) -> str:
    """A stable id for this base + patches, so sim_run_id is reproducible across calls and
    differs whenever the patches do."""
    payload = f"{base_hash}|{_canonical(patches)}"
    return f"wi_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:12]}"


def _document_hash(document: Any) -> str:
    return hashlib.sha256(_canonical(document).encode("utf-8")).hexdigest()


def _canonical(document: Any) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"))


def _validation_detail(errors) -> str:
    return "; ".join(f"{'/'.join(str(p) for p in e.path)}: {e.message}" for e in errors)
