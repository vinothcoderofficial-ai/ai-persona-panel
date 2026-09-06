"""POST /optimize - the placement ranking, over HTTP (S29).

`analytics/optimizer.py` (S24) scores every placement in a space, ranks them,
names the best and says where the current one sits; `analytics/slot_value.py`
(S25) can put a price on the difference. Both have existed since Day 8 with no
route and no screen, so the only way to see a recommendation was to run
`python scripts/optimize.py` at a terminal - which means the claim this project
actually makes, that it is a recommendation engine rather than an A/B testing
tool because it models purchase and not only attention, was not something a
viewer of the running product could check.

This module is the seam and nothing else. It builds the candidate space, calls
`rank_candidates`, and serialises what comes back. **No ranking maths lives
here**, for the same reason `resolve()` lives in one module and the attention
formula in another: a second implementation would be a second answer.

Serialising a ranking is where honesty is easiest to lose, so three properties
are deliberate rather than incidental:

* **An undefined objective serialises as `null`, never `0.0`.** A creative
  taken down has no ad-to-purchase lift; `Scored.objective` is None for it. A
  zero there would rank "no advertising at all" as a measured, mediocre result
  instead of an unanswerable question, and it would sort among the real values.
* **The seed spread is labelled as what it is.** It is Monte Carlo run-to-run
  variability, not a confidence interval - `SeedSpread`'s docstring and
  docs/METHODOLOGY.md 12.7 both say why there is no honest CI to put there
  instead - so the payload carries `spread_caveat` for the screen to print, and
  the seeds themselves, because a range quoted without its `n_seeds` says
  nothing.
* **Skipped candidates come back.** A shelf level a bay does not have is not a
  placement that scored badly. Dropping it silently turns "there was no move to
  try" into the much stronger-sounding "we tried it and it lost".
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

from analytics import optimizer
from api.app.db import PlanogramRecord, VariantRecord, get_session
from api.app.resolve import PatchError, resolve

import json

router = APIRouter(tags=["optimize"])

DEFAULT_VARIANT_ID = "A"
DEFAULT_N_SYNTH = optimizer.DEFAULT_N_SYNTH
DEFAULT_SEED = optimizer.DEFAULT_SEED
# The same ceiling POST /whatif applies, and for the same reason: a runtime
# budget is only meaningful with a bound on the work. The optimizer simulates
# once per candidate, so this is a larger ask than one what-if.
MAX_N_SYNTH = 50_000

SPREAD_CAVEAT = (
    "This range is Monte Carlo run-to-run variability across seeds — the same "
    "simulation re-rolled — and is not a confidence interval. Adding seeds "
    "widens it rather than narrowing it, because it is a min and a max; only a "
    "larger run size narrows it. See docs/METHODOLOGY.md §12.7."
)


class OptimizeRequest(BaseModel):
    """`extra="forbid"`, like `WhatIfRequest`.

    A misspelled `n_synths` that was quietly ignored would hand back a ranking
    at the default run size while the caller believed they had asked for
    another - and two rankings at different run sizes are not comparable.
    """

    model_config = ConfigDict(extra="forbid")

    creative_id: str
    variant_id: str = DEFAULT_VARIANT_ID
    focal_sku_id: Optional[str] = None
    n_synth: int = Field(default=DEFAULT_N_SYNTH, ge=1, le=MAX_N_SYNTH)
    seed: int = DEFAULT_SEED
    spread_seeds: Optional[List[int]] = None
    spread_top_n: int = Field(default=optimizer.DEFAULT_SPREAD_TOP_N, ge=0, le=50)


def _resolved_planogram(db: Session, variant_id: str) -> Dict[str, Any]:
    """The arm this ranking starts from, resolved through the one resolver."""
    variant_record = db.get(VariantRecord, variant_id)
    if variant_record is None:
        raise HTTPException(status_code=404, detail=f"no variant {variant_id!r}")
    variant = json.loads(variant_record.data)

    planogram_record = db.get(PlanogramRecord, variant["base_planogram_id"])
    if planogram_record is None:
        raise HTTPException(
            status_code=404,
            detail=f"no planogram {variant['base_planogram_id']!r} for variant {variant_id!r}",
        )

    try:
        return resolve(json.loads(planogram_record.data), variant)
    except PatchError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _check_creative(planogram: Dict[str, Any], creative_id: str) -> None:
    known = {c["creative_id"] for c in planogram.get("creatives", [])}
    if creative_id not in known:
        raise HTTPException(
            status_code=404,
            detail=(
                f"no creative {creative_id!r} in planogram "
                f"{planogram['planogram_id']!r} (have: {', '.join(sorted(known)) or 'none'})"
            ),
        )


def _check_sku(planogram: Dict[str, Any], sku_id: str) -> None:
    known = {sku["sku_id"] for sku in planogram.get("skus", [])}
    if sku_id not in known:
        raise HTTPException(
            status_code=404,
            detail=f"no sku {sku_id!r} in planogram {planogram['planogram_id']!r}",
        )


def _spread(spread: Any) -> Optional[Dict[str, Any]]:
    """A `SeedSpread` as JSON, or null when none was computed.

    `seeds` and `values` come across in run order, primary seed first, so a
    reader can see which draws produced the range rather than being handed a
    bare interval to misread as a CI.
    """
    if spread is None:
        return None
    return {
        "seeds": list(spread.seeds),
        "values": [float(value) for value in spread.values],
        "low": float(spread.low),
        "high": float(spread.high),
        "n_seeds": spread.n_seeds,
        "width": float(spread.width),
    }


def _entry(scored: Any, format_value) -> Dict[str, Any]:
    return {
        "rank": scored.rank,
        "candidate_id": scored.candidate.candidate_id,
        "kind": scored.candidate.kind,
        "label": scored.candidate.label,
        "detail": scored.candidate.detail,
        # The patches a caller can actually run: the shape POST /whatif and
        # schemas/variant.schema.json both take. A recommendation nobody can
        # act on is a slogan.
        "patches": [dict(patch) for patch in scored.candidate.patches],
        # None, never 0.0. See the module docstring.
        "objective": None if scored.objective is None else float(scored.objective),
        "objective_text": None if scored.objective is None else format_value(scored.objective),
        "is_current": bool(scored.is_current),
        "variant_id": scored.variant_id,
        "sim_run_id": scored.sim_run_id,
        "seed_spread": _spread(scored.seed_spread),
        "unresolved_against": list(scored.unresolved_against),
    }


@router.post("/optimize")
def post_optimize(
    body: OptimizeRequest, db: Session = Depends(get_session)
) -> Dict[str, Any]:
    """Rank every placement of `creative_id`, and optionally every shelf level
    for `focal_sku_id`, against the arm `variant_id` names.

    Exhaustive rather than heuristic - the space is small enough to score whole,
    which is what lets the response say "ranks 4th of 8" rather than "we found a
    better one somewhere".
    """
    planogram = _resolved_planogram(db, body.variant_id)
    _check_creative(planogram, body.creative_id)
    if body.focal_sku_id is not None:
        _check_sku(planogram, body.focal_sku_id)

    space = optimizer.ad_placement_candidates(planogram)
    if body.focal_sku_id is not None:
        space = space + optimizer.sku_level_candidates(planogram, body.focal_sku_id)

    objective = optimizer.ad_purchase_lift_objective(body.creative_id)

    spread_seeds = (
        optimizer.DEFAULT_SPREAD_SEEDS
        if body.spread_seeds is None
        else tuple(body.spread_seeds)
    )

    started = time.perf_counter()
    ranking = optimizer.rank_candidates(
        planogram,
        space,
        objective,
        n_synth=body.n_synth,
        seed=body.seed,
        spread_seeds=spread_seeds,
        spread_top_n=body.spread_top_n,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    return {
        "variant_id": body.variant_id,
        "planogram_id": planogram["planogram_id"],
        "creative_id": body.creative_id,
        "focal_sku_id": body.focal_sku_id,
        "objective_name": ranking.objective_name,
        "n_synth": ranking.n_synth,
        "seed": ranking.seed,
        "spread_seeds": list(ranking.spread_seeds),
        "elapsed_ms": elapsed_ms,
        "n_candidates": ranking.n_candidates,
        "entries": [_entry(entry, ranking.format_value) for entry in ranking.entries],
        "skipped": [
            {
                "candidate_id": skip.candidate_id,
                "kind": skip.kind,
                "reason": skip.reason,
                "detail": skip.detail,
            }
            for skip in ranking.skipped
        ],
        "current_rank": ranking.current_rank,
        "top_pick_is_resolved": ranking.top_pick_is_resolved,
        "beats_current": list(ranking.beats_current or ()),
        # The recommendation in words, from the library that produced it, so the
        # screen prints the same sentence RESULTS.md and the CLI do rather than
        # composing a third version that could disagree with both.
        "summary_lines": optimizer.summary(ranking).splitlines(),
        "spread_caveat": SPREAD_CAVEAT,
    }
