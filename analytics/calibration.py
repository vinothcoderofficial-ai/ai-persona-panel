"""Persona-share calibration: grid search on ONE variant, then freeze.

PLAN §13 overrides SPEC's "Nelder-Mead over 6 params" with: **grid search over
the 4 persona shares, step 0.05; the two global parameters stay fixed.** So
the only thing fitted here is how the population divides between the personas
-- never a policy parameter, never a saliency weight. That is deliberate:
PLAN §11 lists "calibration overfits" as a medium risk and answers it with
"persona shares only; report fit and holdout side by side".

The objective (PLAN S17), minimised over the grid:

    (1 - attention_spearman) + 5 x purchase_share_mae

Both terms come from `analytics/metrics.py`; neither is reimplemented here.

What the Spearman is taken against
----------------------------------
The real panel's attention is `fusion.fuse_session` output: looking AND
interaction. The synthetic side used to be the population's raw
`fixation_prob`, which models looking only, so the search compared two
different quantities and had nowhere to put the difference except into the
shares -- measured, ~0.15 of displaced share that did not shrink with panel
size. Both sides are now fused the same way: the candidate mixture goes
through `fusion.fuse_synthetic`, which gives the synthetic vector a matching
interaction channel out of `purchase_share`. That is why `calibrate` and
`evaluate` need the resolved `planogram` (it carries the sku -> slot map) and
the `mode` the real panel was fused with.

The purchase term is untouched: purchase shares were always like-for-like.

Fit on ONE variant, evaluate the others
---------------------------------------
`calibrate()` takes a single variant's real panel and a single variant's
per-persona simulation. The caller passes **variant A**. Fitting on B or C
would consume the holdout and leave nothing to test the frozen shares
against, which is the whole point of the exercise -- so the fitted variant id
is echoed in the result and `evaluate()` exists to score the other variants
under the frozen shares without re-fitting them.

Why the search is fast
----------------------
Step 0.05 over 4 shares summing to 1 is every composition of 20 units into 4
parts = 1,771 candidates. Re-simulating each would be ~7 minutes. It is not
necessary: `sim.simulator.combine()` blends every field as
`sum(share x persona value)`, so the population vectors are a *linear* mix of
the per-persona vectors. Simulate each persona once, then every candidate is
one matrix row plus a Spearman and an MAE, and the whole grid runs in well
under a second. `analytics/tests/test_calibration.py::
test_mixture_reproduces_combine_exactly` asserts that equivalence against the
real `combine()` rather than trusting this paragraph, and the guards below
mirror `combine()`'s own preconditions (one variant, one seed) so the
shortcut is never applied to inputs `combine()` would have refused.

Fusing the synthetic side does not spend that speed. Fusion normalises, and
normalisation is NOT linear -- each persona spends a different fraction of its
fixations on ad slots, so mixing pre-fused per-persona vectors would give a
different answer from fusing the mixture (pinned down by
`test_mixing_then_fusing_is_not_the_same_as_fusing_then_mixing`). So the grid
mixes first and fuses after, which is what `fuse_synthetic(combine(...))`
computes -- and `fusion.fuse_synthetic_rows` fuses all 1,771 candidate rows in
two array operations, so the exact order is also the fast one.

Only four fields of each SimResult are read: `variant_id`, `seed`,
`fixation_prob` and `purchase_share`. Pure: no I/O, no globals, no RNG. The
same inputs always give the same shares, which is what lets a calibration be
frozen and re-verified from committed data.
"""

from typing import Any, Dict, List, Mapping, Sequence, Tuple

import numpy as np

from analytics.fusion import DEFAULT_MODE, fuse_synthetic_rows, purchase_slot_matrix
from analytics.metrics import attention_spearman, purchase_share_mae

# PLAN S17 / §13: step 0.05 over the persona shares, objective weight 5 on MAE.
DEFAULT_STEP = 0.05
DEFAULT_MAE_WEIGHT = 5.0

# Shares must sum to 1 this closely -- the same tolerance `combine()` uses.
SUM_TOLERANCE = 1e-9

# Two candidates whose objectives differ by less than this are treated as tied
# and resolved by grid order (see `calibrate`). Float summation makes bitwise
# ties essentially impossible -- two candidates can land 1e-16 apart on an
# objective neither of them meaningfully wins -- and without this the reported
# shares could flip between machines. 1e-12 is far below any difference the
# metrics can resolve.
TIE_TOLERANCE = 1e-12


def share_grid(step: float = DEFAULT_STEP, n: int = 4) -> List[Tuple[float, ...]]:
    """Every `n`-way split of 1.0 in units of `step`, in ascending order.

    Built as integer compositions of `1 / step` units and divided only at the
    end, so no float drift can make a candidate miss 1.0 or drop below 0.
    With the defaults this is C(23, 3) = 1,771 candidates.

    The order is ascending lexicographic -- `(0, 0, 0, 1)` first,
    `(1, 0, 0, 0)` last -- and `calibrate` relies on it to break ties.

    `step` must divide 1 into a whole number of units; 0.03 would give 33.33
    and silently round to a grid that is not the one asked for, so it raises.
    """
    if not 0.0 < step <= 1.0:
        raise ValueError(f"step must be in (0, 1], got {step!r}")
    units_exact = 1.0 / step
    units = int(round(units_exact))
    if abs(units_exact - units) > 1e-9:
        raise ValueError(f"step {step!r} does not divide 1 into whole units")
    if n < 1:
        raise ValueError(f"need at least one share to grid over, got n={n!r}")

    integer_grid: List[Tuple[int, ...]] = []

    def extend(prefix: Tuple[int, ...], remaining: int, parts_left: int) -> None:
        if parts_left == 1:
            integer_grid.append(prefix + (remaining,))
            return
        for taken in range(remaining + 1):
            extend(prefix + (taken,), remaining - taken, parts_left - 1)

    extend((), units, n)
    return [tuple(taken / units for taken in candidate) for candidate in integer_grid]


def mixture(
    per_persona: Mapping[str, Mapping[str, Any]],
    shares: Mapping[str, float],
    *,
    slot_ids: Sequence[str],
    sku_ids: Sequence[str] | None = None,
) -> Dict[str, Dict[str, float]]:
    """The share-weighted population vectors, keyed as `combine()` keys them.

    Returns `{"fixation_prob": {...}, "purchase_share": {...}}`: exactly those
    two fields of `sim.simulator.combine(results, shares)`, restricted to
    `slot_ids` and `sku_ids`. Slots or SKUs a persona result does not mention
    count as 0.0, the same convention `analytics/metrics.py` uses.

    `sku_ids` defaults to the sorted union of the personas' `purchase_share`
    keys.
    """
    persona_ids, _variant_id = _persona_order(per_persona)
    weights = _checked_shares(shares, persona_ids)
    sku_vocabulary = _sku_vocabulary(per_persona, sku_ids)

    attention = np.asarray(weights) @ _stack(per_persona, persona_ids, "fixation_prob", slot_ids)
    purchase = np.asarray(weights) @ _stack(per_persona, persona_ids, "purchase_share",
                                            sku_vocabulary)

    return {
        "fixation_prob": dict(zip(slot_ids, attention.tolist())),
        "purchase_share": dict(zip(sku_vocabulary, purchase.tolist())),
    }


def evaluate(
    shares: Mapping[str, float],
    real_attention: Mapping[str, float],
    real_purchase_share: Mapping[str, float],
    per_persona: Mapping[str, Mapping[str, Any]],
    *,
    planogram: Mapping[str, Any],
    slot_ids: Sequence[str],
    sku_ids: Sequence[str] | None = None,
    mode: str = DEFAULT_MODE,
    mae_weight: float = DEFAULT_MAE_WEIGHT,
) -> Dict[str, Any]:
    """Score one variant under shares that are already frozen.

    This is how the holdout numbers are produced: `calibrate` on variant A,
    then `evaluate` the returned shares on B and on C. Nothing is fitted here
    -- the shares that come out are the shares that went in, and the caller's
    mapping is never mutated.

    `planogram` is the RESOLVED planogram for the variant `per_persona` was
    simulated over, and `mode` is the capture mode the real panel was fused
    with; both feed `fusion.fuse_synthetic`, so a holdout is scored by exactly
    the comparison the fit used.

    Returns `{variant_id, shares, objective, attention_spearman,
    purchase_share_mae}`, so a fit result and a holdout result are directly
    comparable field by field.
    """
    persona_ids, variant_id = _persona_order(per_persona)
    weights = _checked_shares(shares, persona_ids)
    sku_vocabulary = _sku_vocabulary(per_persona, sku_ids)

    attention = np.asarray(weights) @ _stack(per_persona, persona_ids, "fixation_prob", slot_ids)
    purchase = np.asarray(weights) @ _stack(per_persona, persona_ids, "purchase_share",
                                            sku_vocabulary)
    # Explicit lengths rather than -1: an empty vocabulary is a legal (if
    # useless) input, and reshape(1, -1) cannot infer a zero-width axis.
    fused = _fused_rows(attention.reshape(1, len(slot_ids)),
                        purchase.reshape(1, len(sku_vocabulary)),
                        planogram, slot_ids, sku_vocabulary, mode)

    scored = _score(
        real_attention, real_purchase_share,
        dict(zip(slot_ids, fused[0].tolist())),
        dict(zip(sku_vocabulary, purchase.tolist())),
        slot_ids, sku_vocabulary, mae_weight,
    )
    return {
        "variant_id": variant_id,
        "shares": {persona_id: weight for persona_id, weight in zip(persona_ids, weights)},
        **scored,
    }


def calibrate(
    real_attention: Mapping[str, float],
    real_purchase_share: Mapping[str, float],
    per_persona: Mapping[str, Mapping[str, Any]],
    *,
    planogram: Mapping[str, Any],
    slot_ids: Sequence[str],
    sku_ids: Sequence[str] | None = None,
    mode: str = DEFAULT_MODE,
    step: float = DEFAULT_STEP,
    mae_weight: float = DEFAULT_MAE_WEIGHT,
) -> Dict[str, Any]:
    """Grid-search the persona shares that best fit ONE variant's real panel.

    **Pass variant A.** `real_attention` is that variant's panel aggregate
    (`fusion.trimmed_mean` over `fusion.fuse_session` outputs),
    `real_purchase_share` its observed purchase shares, and `per_persona` the
    per-persona SimResults for the same variant. Handing this function a
    holdout variant's data would consume the holdout: the shares would no
    longer be frozen with respect to B and C, and the numbers reported for
    them would stop being out-of-sample. The variant is echoed in the result
    so a report can state which one was fitted.

    `planogram` is the RESOLVED planogram those SimResults were produced over
    -- it carries the sku -> slot map the synthetic interaction channel needs,
    and it must be the same variant, because a sku sits in different slots
    under different variants. `mode` is the capture mode `real_attention` was
    fused with, so both sides of the Spearman weight looking and interaction
    identically.

    Every candidate is scored as `(1 - spearman) + mae_weight x mae` and the
    smallest wins. Candidates are visited in `share_grid`'s ascending
    lexicographic order and an incumbent is replaced only on a strict
    improvement of more than `TIE_TOLERANCE`, so ties resolve to the
    lexicographically smallest share tuple -- deterministically, and without
    depending on `per_persona`'s iteration order (the personas are always
    taken in sorted id order).

    `mae_weight=0.0` reduces the objective to the attention term alone, which
    is what the vacuity guard in the tests uses to show that term does real
    work.

    Returns `{variant_id, shares, objective, attention_spearman,
    purchase_share_mae, n_candidates, step, mae_weight}`. `shares` is keyed by
    persona id and is ready for `calibrated_shares` in
    schemas/metrics.schema.json.
    """
    persona_ids, variant_id = _persona_order(per_persona)
    sku_vocabulary = _sku_vocabulary(per_persona, sku_ids)

    attention_basis = _stack(per_persona, persona_ids, "fixation_prob", slot_ids)
    purchase_basis = _stack(per_persona, persona_ids, "purchase_share", sku_vocabulary)

    grid = share_grid(step, len(persona_ids))
    candidates = np.asarray(grid, dtype=float)
    # The whole reason this is fast: one matrix product replaces 1,771
    # simulations, because combine() is linear in the shares.
    attention_matrix = candidates @ attention_basis
    purchase_matrix = candidates @ purchase_basis
    # ...and fusing all 1,771 mixed rows costs two more array operations, so
    # the like-for-like comparison is free.
    attention_rows = _fused_rows(attention_matrix, purchase_matrix,
                                 planogram, slot_ids, sku_vocabulary, mode).tolist()
    purchase_rows = purchase_matrix.tolist()

    best_shares: Tuple[float, ...] | None = None
    best_scored: Dict[str, float] = {}

    for index, shares in enumerate(grid):
        scored = _score(
            real_attention, real_purchase_share,
            dict(zip(slot_ids, attention_rows[index])),
            dict(zip(sku_vocabulary, purchase_rows[index])),
            slot_ids, sku_vocabulary, mae_weight,
        )
        if best_shares is None or scored["objective"] < best_scored["objective"] - TIE_TOLERANCE:
            best_shares, best_scored = shares, scored

    return {
        "variant_id": variant_id,
        "shares": {persona_id: float(share)
                   for persona_id, share in zip(persona_ids, best_shares)},
        **best_scored,
        "n_candidates": len(grid),
        "step": float(step),
        "mae_weight": float(mae_weight),
    }


def _fused_rows(
    attention_rows: np.ndarray,
    purchase_rows: np.ndarray,
    planogram: Mapping[str, Any],
    slot_ids: Sequence[str],
    sku_ids: Sequence[str],
    mode: str,
) -> np.ndarray:
    """The synthetic vectors the Spearman is taken against, one row per mix.

    `attention_rows` and `purchase_rows` are the mixed `fixation_prob` and
    `purchase_share` -- (n_mixes, n_slots) and (n_mixes, n_skus). The
    purchases are credited to slots and the two channels blended by
    `analytics/fusion.py`, which owns that formula; this function only routes
    the arrays into it, so `calibrate`'s 1,771 candidates and `evaluate`'s
    single frozen mix are fused by identical code -- and by the same code
    `api/app/routers/experiments.py` uses through `fuse_synthetic`.
    """
    slot_purchase_rows = purchase_rows @ purchase_slot_matrix(planogram, slot_ids, sku_ids)
    return fuse_synthetic_rows(attention_rows, slot_purchase_rows, mode=mode)


def _score(
    real_attention: Mapping[str, float],
    real_purchase_share: Mapping[str, float],
    synth_attention: Mapping[str, float],
    synth_purchase_share: Mapping[str, float],
    slot_ids: Sequence[str],
    sku_ids: Sequence[str],
    mae_weight: float,
) -> Dict[str, float]:
    """The objective and its two components -- the one place the formula lives.

    `calibrate` calls this once per candidate and `evaluate` once per frozen
    mix, so a fit score and a holdout score are computed by identical code.
    Both metrics come from analytics/metrics.py, and neither can return NaN:
    `attention_spearman` already collapses a constant vector to 0.0.
    """
    spearman = attention_spearman(real_attention, synth_attention, slot_ids)
    mae = purchase_share_mae(real_purchase_share, synth_purchase_share, sku_ids)
    return {
        "objective": float((1.0 - spearman) + mae_weight * mae),
        "attention_spearman": float(spearman),
        "purchase_share_mae": float(mae),
    }


def _persona_order(per_persona: Mapping[str, Mapping[str, Any]]) -> Tuple[List[str], str]:
    """Sorted persona ids plus the one variant they all belong to.

    Sorting fixes the column order of every matrix below, so the answer never
    depends on dict insertion order. The variant and seed checks mirror
    `sim.simulator.combine()`: this module stands in for that function inside
    the grid loop, so it must refuse exactly the inputs combine() refuses --
    blending two variants' results, or two different simulation seeds, would
    produce a population vector that corresponds to no actual run.
    """
    if not per_persona:
        raise ValueError("calibration needs at least one persona result")

    variants = {result["variant_id"] for result in per_persona.values()}
    if len(variants) != 1:
        raise ValueError(
            f"cannot calibrate across persona results from different variants: {sorted(variants)}"
        )
    seeds = {result["seed"] for result in per_persona.values()}
    if len(seeds) != 1:
        raise ValueError(
            f"cannot calibrate across persona results from different seeds: {sorted(seeds)}"
        )

    return sorted(per_persona), variants.pop()


def _checked_shares(shares: Mapping[str, float], persona_ids: Sequence[str]) -> List[float]:
    """`shares` as a list in `persona_ids` order, validated as a real mix.

    Keyed by persona id rather than positional so a caller cannot silently
    pair the wrong share with the wrong persona. The sum and sign checks are
    `combine()`'s, kept here because the grid loop bypasses it.
    """
    if set(shares) != set(persona_ids):
        raise ValueError(
            f"shares must name exactly the personas {sorted(persona_ids)}, got {sorted(shares)}"
        )
    weights = [float(shares[persona_id]) for persona_id in persona_ids]
    if any(weight < 0.0 for weight in weights):
        raise ValueError(f"shares must not be negative, got {dict(shares)}")
    if abs(sum(weights) - 1.0) > SUM_TOLERANCE:
        raise ValueError(f"shares must sum to 1, got {sum(weights)}")
    return weights


def _sku_vocabulary(
    per_persona: Mapping[str, Mapping[str, Any]], sku_ids: Sequence[str] | None
) -> List[str]:
    """The SKU comparison set: the caller's if given, else the sorted union of
    every persona's `purchase_share` keys. Sorted so the vocabulary -- and
    therefore the MAE's denominator -- never depends on iteration order."""
    if sku_ids is not None:
        return list(sku_ids)
    union: set[str] = set()
    for result in per_persona.values():
        union.update(result["purchase_share"])
    return sorted(union)


def _stack(
    per_persona: Mapping[str, Mapping[str, Any]],
    persona_ids: Sequence[str],
    field: str,
    keys: Sequence[str],
) -> np.ndarray:
    """(n_personas, len(keys)) matrix of one SimResult field, missing = 0.0."""
    return np.array(
        [[float(per_persona[persona_id][field].get(key, 0.0)) for key in keys]
         for persona_id in persona_ids],
        dtype=float,
    ).reshape(len(persona_ids), len(keys))
