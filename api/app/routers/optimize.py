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
* **A question that was not asked comes back as `null`.** `beats_current` is
  None when the comparison could not be made -- nothing in the space reproduces
  today's planogram, or the current placement fell outside `spread_top_n` and
  so has no range for anything to clear. This route wrote `list(... or ())`,
  which turned that None into `[]`, and the screen renders `[]` as "No
  placement clears the current one's spread either." On the committed aisle at
  this route's own defaults the current placement ranks 5th to 8th, outside the
  top five, for 23 of the 24 focal SKUs: an unanswered question printed as a
  definite negative nearly every time.

Which estimator the caller gets
-------------------------------
`analytics/lift.py` carries two Brand Lifts. The WITHIN-run one splits a single
run by whether each shopper fixated an ad slot; the BETWEEN-arm one compares a
treated run against a control run carrying no creative. docs/PHASE3.md P3.1
measured them on this aisle at +4.5 % and +0.9 %, because within-run "ad
exposed" is a selection -- those shoppers had already walked to the endcap --
and not a randomisation.

This route hardcoded the within-run one, so every screen in the product showed
a number roughly five times the one a client's own study would produce.
`objective` now names the estimator and **defaults to the between-arm
comparison**: not for compatibility, but because a default is what almost
everyone reads, and the estimator whose two arms are identical by construction
is the one that should be under an unqualified percentage. The within-run split
stays reachable by name -- it is the only estimator a real panel can produce -
and `objective_caveat` travels with either, so the screen prints what the
number is rather than inferring it.

`sku_purchase_share` is the third option and needs no creative at all, which
makes it the one that can rank a planogram whose advertising is unknown.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Literal, Optional

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

# The estimator names a caller can ask for, and the default. `between_arm_lift`
# leads because it is the comparison a client's own study makes; see the module
# docstring for why an unqualified percentage has to be that one.
ObjectiveName = Literal["between_arm_lift", "within_run_lift", "sku_purchase_share"]
DEFAULT_OBJECTIVE: ObjectiveName = "between_arm_lift"
LIFT_OBJECTIVES = ("between_arm_lift", "within_run_lift")


class OptimizeRequest(BaseModel):
    """`extra="forbid"`, like `WhatIfRequest`.

    A misspelled `n_synths` that was quietly ignored would hand back a ranking
    at the default run size while the caller believed they had asked for
    another - and two rankings at different run sizes are not comparable. The
    same argument is why `objective` is a `Literal` and not a free string: a
    misspelled estimator that fell back to the default would answer a question
    nobody asked, in a field where the two answers differ five-fold.

    `creative_id` is optional because `sku_purchase_share` names no creative -
    which is what makes it usable on a planogram whose ad furniture is unknown.
    It is required for either lift, and `post_optimize` refuses rather than
    guessing one.
    """

    model_config = ConfigDict(extra="forbid")

    creative_id: Optional[str] = None
    objective: ObjectiveName = DEFAULT_OBJECTIVE
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


def _build_objective(body: OptimizeRequest) -> Any:
    """The `Objective` the caller asked for, or a 422 naming what is missing.

    No fallbacks. A lift with no creative and a share with no SKU are both
    questions with no subject, and inventing one - "the first creative in the
    planogram", say - would produce a confident ranking of something the caller
    did not ask about.
    """
    if body.objective in LIFT_OBJECTIVES and body.creative_id is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"objective {body.objective!r} is a brand lift and needs a creative_id; "
                "there is no creative this endpoint could pick for you"
            ),
        )

    if body.objective == "between_arm_lift":
        return optimizer.between_arm_lift_objective(body.creative_id)
    if body.objective == "within_run_lift":
        return optimizer.ad_purchase_lift_objective(body.creative_id)

    if body.focal_sku_id is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "objective 'sku_purchase_share' needs a focal_sku_id: it ranks one "
                "product's share of purchases, and there is no default product"
            ),
        )
    return optimizer.sku_purchase_share_objective(body.focal_sku_id)


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
        # Three answers: True (the whole range is clear of no effect), False
        # (the range contains it, so this placement has not been shown to do
        # anything at all whatever it outranked), null (no range, or an
        # objective with no meaningful null). False and null are not the same
        # claim and neither is a bad score.
        "spread_clears_no_effect": scored.spread_clears_no_effect,
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
    if body.creative_id is not None:
        _check_creative(planogram, body.creative_id)
    if body.focal_sku_id is not None:
        _check_sku(planogram, body.focal_sku_id)

    space = optimizer.ad_placement_candidates(planogram)
    if body.focal_sku_id is not None:
        space = space + optimizer.sku_level_candidates(planogram, body.focal_sku_id)

    objective = _build_objective(body)

    spread_seeds = (
        optimizer.DEFAULT_SPREAD_SEEDS
        if body.spread_seeds is None
        else tuple(body.spread_seeds)
    )

    started = time.perf_counter()
    try:
        ranking = optimizer.rank_candidates(
            planogram,
            space,
            objective,
            n_synth=body.n_synth,
            seed=body.seed,
            spread_seeds=spread_seeds,
            spread_top_n=body.spread_top_n,
        )
    except FileNotFoundError as exc:
        # `api.app.simcache.load_policy` raises this for a planogram nobody has
        # generated persona policies for. POST /whatif has turned it into a 404
        # since S15; this route did not, so the same input gave one endpoint a
        # 404 and this one a 500 and a traceback. A 500 says "this server is
        # broken"; the truth is that the caller named a planogram the simulator
        # has no policies for, which is theirs to fix, so the detail names the
        # file that is missing.
        raise HTTPException(
            status_code=404,
            detail=(
                f"{exc}. Ranking placements simulates every candidate on planogram "
                f"{planogram['planogram_id']!r}, and each persona needs its cached policy "
                f"at data/cache/policies/<persona_id>_{planogram['planogram_id']}.json. "
                "Generate them (make seed) before optimising against this planogram."
            ),
        ) from exc
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    return {
        "variant_id": body.variant_id,
        "planogram_id": planogram["planogram_id"],
        "creative_id": body.creative_id,
        "focal_sku_id": body.focal_sku_id,
        "objective": body.objective,
        "objective_name": ranking.objective_name,
        # The sentence that says what the number is. Two estimators, five-fold
        # apart, so a percentage without this is an unlabelled percentage.
        "objective_caveat": ranking.objective_caveat,
        "no_effect_value": ranking.no_effect_value,
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
        # JSON null when the comparison was never made, and a list - possibly
        # empty - when it was. `list(... or ())` collapsed the first into the
        # second, and the screen renders an empty list as "No placement clears
        # the current one's spread either", so an unanswered question printed
        # as a definite negative. See the module docstring for how often.
        "beats_current": (None if ranking.beats_current is None
                          else list(ranking.beats_current)),
        # The recommendation in words, from the library that produced it, so the
        # screen prints the same sentence RESULTS.md and the CLI do rather than
        # composing a third version that could disagree with both.
        "summary_lines": optimizer.summary(ranking).splitlines(),
        "spread_caveat": SPREAD_CAVEAT,
    }
