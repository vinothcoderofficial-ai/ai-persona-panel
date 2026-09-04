"""Attention fusion -- the single formula for turning real shoppers' raw
behavioural events into per-slot attention scores, for turning a simulated
population into the matching synthetic vector, and for aggregating those
scores across a panel of sessions.

Per CLAUDE.md, this is the *only* place the fusion maths may live:
`api/app/live.py` imports `fuse_session` directly on its hot path, so this
module stays pure -- no I/O, no database, no HTTP, no globals, no classes.

Two per-session formulas (SPEC M5), selected by the keyword-only `mode`,
which takes the same two values as `mode` in schemas/session.schema.json:

    mode="cursor_only" (the default):
        att = 0.7 * cursor_dwell_norm + 0.3 * interaction_norm

    mode="webcam":
        att = 0.5 * fix_dwell_norm + 0.3 * cursor_dwell_norm + 0.2 * interaction_norm

where, per slot:
  - fix_dwell_norm is the summed `fixation` duration (ms), normalised to sum
    to 1 across the session's slot vocabulary. Counted in webcam mode only.
  - cursor_dwell_norm is the summed `cursor_dwell` duration (ms), normalised
    the same way.
  - interaction_norm is the MAX interaction weight observed for that slot
    (hover=0.5, pickup=1.0, add_to_cart=1.0 -- not the sum, not the count),
    normalised the same way.

`fuse_session` keeps its original two-argument signature so the existing
positional call in `api/app/routers/experiments.py` is unaffected: omitting
`mode` gives cursor-only fusion, exactly as before.

The synthetic side: `fuse_synthetic`
------------------------------------
Real attention fuses looking AND interaction. The synthetic side it is scored
against used to be the population SimResult's raw `fixation_prob`, which
models looking only -- so the Spearman correlated "looking plus touching and
buying" against "looking only". That is not like-for-like, and calibration
absorbed the difference into the persona shares instead of leaving it
visible (~0.15 of displaced share, and it did not shrink with panel size).

`fuse_synthetic` gives the synthetic side a matching interaction channel, so
both sides are fused the same way:

    mode="cursor_only":  0.7 * fixation_prob_norm + 0.3 * synth_interaction_norm
    mode="webcam":       0.8 * fixation_prob_norm + 0.2 * synth_interaction_norm

The synthetic side has ONE looking channel where the real side has two, so
the real fixation and cursor weights collapse onto `fixation_prob` and the
interaction weight carries across unchanged. Both numbers are derived from
`_MODE_WEIGHTS` by `synthetic_weights`, never written down a second time:
retuning the real weights moves the synthetic ones automatically, which is
the whole point of the coupling.

The synthetic interaction channel is `purchase_share`: a simulated shopper
who buys a sku necessarily picked it up and added it to cart, which is what
the real `interaction` channel records. Each sku's share is credited to the
slot it occupies **in the resolved planogram passed in** -- a sku moves
between slots from variant to variant, so the map is looked up per planogram
and never assumed.

Across sessions (SPEC M5): `trimmed_mean` (10 % per tail) is the panel's
point estimate and `bootstrap_ci` (1,000 resamples -> 95 % CI) is its
uncertainty. Those two use numpy for speed; `fuse_session` itself remains
stdlib-only.
"""

from typing import Any, Mapping, Sequence

import numpy as np

# hover < pickup == add_to_cart. A slot's interaction score is the MAX of
# these observed for it, never a sum or a count.
_INTERACTION_WEIGHTS: Mapping[str, float] = {
    "hover": 0.5,
    "pickup": 1.0,
    "add_to_cart": 1.0,
}

# The component weights per capture mode, keyed by schemas/session.schema.json's
# `mode` enum. cursor_only weights fixations at 0 rather than omitting them,
# so both modes share one code path and the maths is written down once.
_MODE_WEIGHTS: Mapping[str, tuple[float, float, float]] = {
    #                fixation, cursor, interaction
    "cursor_only": (0.0, 0.7, 0.3),
    "webcam": (0.5, 0.3, 0.2),
}

DEFAULT_MODE = "cursor_only"

# 10 % from each tail (SPEC M5).
DEFAULT_TRIM = 0.10


def fuse_session(
    events: list[dict],
    slot_ids: Sequence[str],
    *,
    mode: str = DEFAULT_MODE,
) -> dict[str, float]:
    """Fuse one session's raw events into a per-slot attention vector.

    `slot_ids` is the full slot vocabulary of the resolved planogram: every
    id in it is a key in the returned dict, even when its attention is 0.0.
    This keeps the output aligned with whatever vector `metrics.py` compares
    it against.

    `mode` is the session's capture mode from schemas/session.schema.json --
    "cursor_only" (the default, and the behaviour of the two-argument call)
    or "webcam". Any other value raises ValueError rather than silently
    fusing with the wrong weights.

    Events follow schemas/event.schema.json. Only these types feed the
    formula; every other type (gaze, remove, station_enter, station_exit,
    checkout) is ignored:
      - fixation -> payload {x, y, dur_ms, slot_id|null, shelf_id|null},
        summed per slot. A fixation with slot_id null landed on a shelf
        rather than on a product slot, so it belongs to no slot and is
        skipped -- it does not enter any denominator. Weighted 0 in
        cursor_only mode, so it contributes nothing there.
      - cursor_dwell -> payload {slot_id, dur_ms}, summed per slot.
      - hover, pickup, add_to_cart -> payload {sku_id, slot_id}, max weight
        per slot (see _INTERACTION_WEIGHTS).

    An event naming a slot_id outside `slot_ids` is dropped rather than
    raising: a slot can disappear between planogram revisions, and this runs
    on the live engine's hot path where a crash is worse than a dropped
    sample.

    Every division is guarded: a component whose raw total is 0 (e.g. a
    webcam session that produced no fixations at all) normalises to an
    all-zero vector rather than NaN, and the fused output is simply the
    remaining components scaled by their own weights. The weights are
    deliberately NOT renormalised to compensate for a missing component, so
    such a session's vector sums to less than 1 -- an honest record that it
    carried less signal.
    """
    if mode not in _MODE_WEIGHTS:
        raise ValueError(
            f"unknown fusion mode {mode!r}; expected one of {sorted(_MODE_WEIGHTS)}"
        )
    fixation_weight, cursor_weight, interaction_weight_factor = _MODE_WEIGHTS[mode]

    known_slots = set(slot_ids)
    fixation_dwell_ms: dict[str, float] = {slot_id: 0.0 for slot_id in slot_ids}
    cursor_dwell_ms: dict[str, float] = {slot_id: 0.0 for slot_id in slot_ids}
    interaction_weight: dict[str, float] = {slot_id: 0.0 for slot_id in slot_ids}

    for event in events:
        payload = event.get("payload") or {}
        slot_id = payload.get("slot_id")
        if slot_id not in known_slots:
            continue  # unknown, absent or null slot_id -- ignore, never raise

        event_type = event.get("type")
        if event_type == "fixation":
            fixation_dwell_ms[slot_id] += payload.get("dur_ms", 0)
        elif event_type == "cursor_dwell":
            cursor_dwell_ms[slot_id] += payload.get("dur_ms", 0)
        elif event_type in _INTERACTION_WEIGHTS:
            weight = _INTERACTION_WEIGHTS[event_type]
            if weight > interaction_weight[slot_id]:
                interaction_weight[slot_id] = weight
        # every other event type is not part of this formula -- ignore

    fixation_norm = _normalise(fixation_dwell_ms, slot_ids)
    cursor_norm = _normalise(cursor_dwell_ms, slot_ids)
    interaction_norm = _normalise(interaction_weight, slot_ids)

    return {
        slot_id: (
            fixation_weight * fixation_norm[slot_id]
            + cursor_weight * cursor_norm[slot_id]
            + interaction_weight_factor * interaction_norm[slot_id]
        )
        for slot_id in slot_ids
    }


def synthetic_weights(mode: str = DEFAULT_MODE) -> tuple[float, float]:
    """`(looking_weight, interaction_weight)` for the synthetic side of the
    comparison, DERIVED from `_MODE_WEIGHTS` -- there is one weight table in
    this module, never two.

    The real formula has two looking channels (fixation dwell and cursor
    dwell) and one interaction channel; a SimResult has one looking channel
    (`fixation_prob`) and one interaction channel (`purchase_share`). So the
    two real looking weights are summed onto the single synthetic looking
    channel and the interaction weight is carried across unchanged:

        cursor_only: (0.0 + 0.7, 0.3) -> (0.7, 0.3)
        webcam:      (0.5 + 0.3, 0.2) -> (0.8, 0.2)

    Retuning `_MODE_WEIGHTS` therefore retunes the synthetic side in the same
    edit, which is what keeps the two sides comparable.
    """
    if mode not in _MODE_WEIGHTS:
        raise ValueError(
            f"unknown fusion mode {mode!r}; expected one of {sorted(_MODE_WEIGHTS)}"
        )
    fixation_weight, cursor_weight, interaction_weight = _MODE_WEIGHTS[mode]
    return fixation_weight + cursor_weight, interaction_weight


def purchase_slot_matrix(
    planogram: Mapping[str, Any],
    slot_ids: Sequence[str],
    sku_ids: Sequence[str],
) -> np.ndarray:
    """`(len(sku_ids), len(slot_ids))` matrix crediting each sku's purchase
    share to the slot it occupies in `planogram`.

    This is the one place the sku -> slot credit rule is written down;
    `fuse_synthetic` and `analytics/calibration.py` both go through it, so the
    scalar and the vectorised paths cannot drift apart.

    The map comes from the resolved planogram handed in, never from a cached
    table: variant B moves SKU_008 from the bottom shelf to eye level, so the
    same sku's purchases belong to a different slot under a different variant.

    A sku in no slot of `slot_ids` gets an all-zero row and so contributes
    nothing -- not to any slot, and not to the denominator. The planogram
    model gives a sku at most one slot (`api/app/resolve.py`'s `move_sku`
    relies on it), but if one were listed in several its share is divided
    equally between them rather than counted once per slot, so the credit rule
    is total-preserving either way.
    """
    slot_index = {slot_id: index for index, slot_id in enumerate(slot_ids)}

    holders: dict[str, list[int]] = {}
    for bay in planogram["bays"]:
        for shelf in bay["shelves"]:
            for slot in shelf["slots"]:
                sku_id = slot.get("sku_id")
                index = slot_index.get(slot["slot_id"])
                if sku_id is None or index is None:
                    continue
                holders.setdefault(sku_id, []).append(index)

    matrix = np.zeros((len(sku_ids), len(slot_ids)), dtype=float)
    for row, sku_id in enumerate(sku_ids):
        columns = holders.get(sku_id)
        if columns:
            matrix[row, columns] = 1.0 / len(columns)
    return matrix


def fuse_synthetic_rows(
    fixation_rows: np.ndarray,
    slot_purchase_rows: np.ndarray,
    *,
    mode: str = DEFAULT_MODE,
) -> np.ndarray:
    """The synthetic fusion formula, applied to a whole stack of candidates.

    `fixation_rows` and `slot_purchase_rows` are both `(n, len(slot_ids))`
    arrays already expressed over the same slot vocabulary -- raw
    `fixation_prob` and purchase share already credited to slots by
    `purchase_slot_matrix`. Each is normalised over its own row and the two
    are blended by `synthetic_weights(mode)`.

    `fuse_synthetic` is a one-row call into this function and
    `analytics/calibration.py` passes all 1,771 grid candidates at once, so
    the per-experiment answer and the per-candidate answer come from
    identical arithmetic and the grid search costs two array operations
    rather than 1,771 re-simulations.
    """
    looking_weight, interaction_weight = synthetic_weights(mode)
    return (
        looking_weight * _normalise_rows(fixation_rows)
        + interaction_weight * _normalise_rows(slot_purchase_rows)
    )


def fuse_synthetic(
    sim_result: Mapping[str, Any],
    planogram: Mapping[str, Any],
    slot_ids: Sequence[str],
    *,
    mode: str = DEFAULT_MODE,
) -> dict[str, float]:
    """Fuse one SimResult into a per-slot attention vector comparable, term
    for term, with `fuse_session`'s output.

    `sim_result` is a SimResult from `sim/simulator.py` -- usually the
    share-weighted population result from `combine()`. Only two of its fields
    are read: `fixation_prob` (the looking channel, keyed by slot id and
    covering ad slots too) and `purchase_share` (the interaction channel,
    keyed by sku id).

    `planogram` is the RESOLVED planogram the SimResult was produced over; it
    supplies the sku -> slot map (see `purchase_slot_matrix`).

    `slot_ids` is the same slot vocabulary the real side was fused over, and
    every id in it is a key of the returned dict. Entries of `fixation_prob`
    outside that vocabulary (ad slots, or slots from another revision) are
    dropped and do not enter any denominator, exactly as `fuse_session` drops
    events naming an unknown slot.

    `mode` selects the weights, and must be the mode the real side was fused
    with -- comparing a webcam-fused panel against a cursor-only-fused
    synthetic vector would reintroduce the mismatch this function exists to
    remove. An unknown mode raises ValueError rather than fusing with the
    wrong weights.

    Every division is guarded the same way `fuse_session` guards its own: a
    component with nothing in it normalises to an all-zero vector rather than
    NaN, and the weights are NOT renormalised to compensate. A SimResult in
    which nobody bought anything therefore sums to the looking weight alone,
    not to 1 -- the same honest record that it carried less signal.
    """
    sku_ids = list(sim_result["purchase_share"])

    fixation_rows = np.array(
        [[float(sim_result["fixation_prob"].get(slot_id, 0.0)) for slot_id in slot_ids]],
        dtype=float,
    ).reshape(1, len(slot_ids))
    purchase_rows = np.array(
        [[float(sim_result["purchase_share"][sku_id]) for sku_id in sku_ids]],
        dtype=float,
    ).reshape(1, len(sku_ids))

    slot_purchase_rows = purchase_rows @ purchase_slot_matrix(planogram, slot_ids, sku_ids)
    fused = fuse_synthetic_rows(fixation_rows, slot_purchase_rows, mode=mode)

    return dict(zip(slot_ids, fused[0].tolist()))


def trimmed_mean(
    per_session: Sequence[Mapping[str, float]],
    slot_ids: Sequence[str],
    trim: float = DEFAULT_TRIM,
) -> dict[str, float]:
    """Per-slot 10 % trimmed mean across a panel of fused sessions (SPEC M5).

    `per_session` is one `fuse_session` output per accepted session. Each
    slot is trimmed on its own values, independently of the others: the
    lowest and highest `int(n * trim)` session values for that slot are
    dropped and the rest averaged. A slot missing from a session's mapping
    counts as 0.0 there, so every slot is averaged over the same n.

    The count is rounded down PER TAIL, so a panel of fewer than ten
    sessions has int(n * 0.10) == 0 and is not trimmed at all -- the result
    is then a plain arithmetic mean. Ten sessions drop one value per tail,
    twenty drop two, and so on.

    An empty panel returns 0.0 for every slot rather than dividing by zero.
    `trim` outside [0, 0.5) raises ValueError: at 0.5 and above both tails
    would consume every value and leave nothing to average.
    """
    if not 0.0 <= trim < 0.5:
        raise ValueError(f"trim must be in [0, 0.5), got {trim!r}")

    matrix = _session_matrix(per_session, slot_ids)
    if matrix.shape[0] == 0:
        return {slot_id: 0.0 for slot_id in slot_ids}

    means = _trimmed_mean_over_sessions(matrix, trim)
    return {slot_id: float(means[i]) for i, slot_id in enumerate(slot_ids)}


def bootstrap_ci(
    per_session: Sequence[Mapping[str, float]],
    slot_ids: Sequence[str],
    *,
    n_boot: int = 1000,
    seed: int,
    ci: float = 0.95,
) -> dict[str, tuple[float, float]]:
    """Bootstrap confidence interval around `trimmed_mean`, per slot (SPEC M5).

    Resamples the panel of sessions with replacement `n_boot` times (each
    resample the same size as the panel), recomputes the trimmed mean of
    each resample, and returns the lower/upper percentile bounds of that
    bootstrap distribution -- (2.5, 97.5) for the default ci=0.95.

    `seed` is required and keyword-only, and feeds `np.random.default_rng`:
    `scripts/eval.py` has to regenerate RESULTS.md identically from the same
    committed sessions, so an implicit or wall-clock seed is not acceptable
    here. The same seed and inputs always give exactly the same bounds.

    A panel whose sessions are all identical has a single-point bootstrap
    distribution and therefore a zero-width interval. An empty panel returns
    (0.0, 0.0) per slot rather than dividing by zero.
    """
    if n_boot < 1:
        raise ValueError(f"n_boot must be at least 1, got {n_boot!r}")
    if not 0.0 < ci < 1.0:
        raise ValueError(f"ci must be in (0, 1), got {ci!r}")

    matrix = _session_matrix(per_session, slot_ids)
    n_sessions = matrix.shape[0]
    if n_sessions == 0:
        return {slot_id: (0.0, 0.0) for slot_id in slot_ids}

    rng = np.random.default_rng(seed)
    draws = rng.integers(0, n_sessions, size=(n_boot, n_sessions))
    # (n_boot, n_sessions, n_slots) -> trimmed mean over the sessions axis
    boot_means = _trimmed_mean_over_sessions(matrix[draws], DEFAULT_TRIM)

    tail = (1.0 - ci) / 2.0 * 100.0
    lower = np.percentile(boot_means, tail, axis=0)
    upper = np.percentile(boot_means, 100.0 - tail, axis=0)

    return {
        slot_id: (float(lower[i]), float(upper[i]))
        for i, slot_id in enumerate(slot_ids)
    }


def _normalise(totals: Mapping[str, float], slot_ids: Sequence[str]) -> dict[str, float]:
    """Scale `totals` so it sums to 1 over `slot_ids`.

    Guards the division: when the raw total is 0 or less (nothing observed
    for any slot), returns an all-zero vector instead of dividing by zero.
    """
    grand_total = sum(totals[slot_id] for slot_id in slot_ids)
    if grand_total <= 0:
        return {slot_id: 0.0 for slot_id in slot_ids}
    return {slot_id: totals[slot_id] / grand_total for slot_id in slot_ids}


def _normalise_rows(rows: np.ndarray) -> np.ndarray:
    """The array form of `_normalise`: scale each row to sum to 1.

    Identical rule, identical guard -- a row whose total is 0 or less becomes
    an all-zero row rather than NaN -- so the synthetic side and the real side
    normalise the same way. `_normalise` stays dict-based because
    `fuse_session` runs on the live engine's hot path and is stdlib-only;
    this one exists so the whole calibration grid normalises in one operation.
    """
    totals = rows.sum(axis=-1, keepdims=True)
    return np.divide(rows, totals, out=np.zeros_like(rows), where=totals > 0)


def _session_matrix(
    per_session: Sequence[Mapping[str, float]], slot_ids: Sequence[str]
) -> np.ndarray:
    """Stack the panel into a (n_sessions, n_slots) float array in `slot_ids`
    order, treating a slot missing from a session's mapping as 0.0.
    """
    return np.array(
        [[float(session.get(slot_id, 0.0)) for slot_id in slot_ids] for session in per_session],
        dtype=float,
    ).reshape(len(per_session), len(slot_ids))


def _trimmed_mean_over_sessions(matrix: np.ndarray, trim: float) -> np.ndarray:
    """Trimmed mean along the sessions axis (-2) of a (..., n_sessions,
    n_slots) array, returning (..., n_slots).

    This is the one place the trimming rule is written down: `trimmed_mean`
    applies it to a single panel and `bootstrap_ci` applies it to a whole
    stack of resampled panels at once, so neither reimplements it.
    """
    n_sessions = matrix.shape[-2]
    per_tail = int(n_sessions * trim)
    ordered = np.sort(matrix, axis=-2)
    kept = ordered[..., per_tail : n_sessions - per_tail, :]
    return kept.mean(axis=-2)
