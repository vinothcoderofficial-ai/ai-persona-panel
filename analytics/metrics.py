"""Comparison metrics between a real shopper's fused attention (or purchase
shares) and a synthetic population's prediction -- the numbers the dashboard,
`POST /experiments` and `scripts/eval.py` report.

The set (SPEC M5): Spearman over slots, KL divergence of the two attention
heatmaps with epsilon smoothing, purchase-share MAE over the focal category,
Ad Slot Index Spearman, and decision agreement (same winning variant on the
focal KPI). `decision_agreement` returns exactly the block that
schemas/metrics.schema.json requires.

Pure and small: stdlib plus scipy.stats only. No I/O, no globals.
"""

import math
from typing import Any, Mapping, Sequence

from scipy.stats import spearmanr

# Smoothing added to both attention vectors before the KL divergence, so a
# slot with zero attention on either side stays finite (SPEC M5).
DEFAULT_KL_EPS = 1e-3


def attention_spearman(real: Mapping[str, float], synth: Mapping[str, float], slot_ids: Sequence[str]) -> float:
    """Spearman rank correlation between two per-slot attention vectors.

    Both vectors are built in `slot_ids` order; a slot missing from either
    mapping is treated as 0.0. If either resulting vector is constant (every
    entry tied -- e.g. all zero), rank correlation is mathematically
    undefined and scipy returns NaN; this returns 0.0 in that case instead,
    so "no signal" never propagates a NaN into the dashboard or eval
    pipeline.
    """
    real_vec = [real.get(slot_id, 0.0) for slot_id in slot_ids]
    synth_vec = [synth.get(slot_id, 0.0) for slot_id in slot_ids]

    if len(set(real_vec)) <= 1 or len(set(synth_vec)) <= 1:
        return 0.0

    rho, _p_value = spearmanr(real_vec, synth_vec)
    return float(rho)


def purchase_share_mae(
    real: Mapping[str, float],
    synth: Mapping[str, float],
    sku_ids: Sequence[str] | None = None,
) -> float:
    """Mean absolute error between two purchase-share mappings.

    Compares over `sku_ids` when given, else the union of both mappings'
    keys. A sku missing from one mapping is treated as 0.0 share there.
    Returns 0.0 for an empty comparison set (guards the division).
    """
    ids = sku_ids if sku_ids is not None else sorted(set(real) | set(synth))
    if not ids:
        return 0.0

    absolute_diffs = [abs(real.get(sku_id, 0.0) - synth.get(sku_id, 0.0)) for sku_id in ids]
    return sum(absolute_diffs) / len(absolute_diffs)


def heatmap_kl(
    real: Mapping[str, float],
    synth: Mapping[str, float],
    slot_ids: Sequence[str],
    *,
    eps: float = DEFAULT_KL_EPS,
) -> float:
    """KL(P_real || P_synth) between two per-slot attention heatmaps, in nats.

    Both vectors are built in `slot_ids` order (a slot missing from either
    mapping counts as 0.0), `eps` is added to every entry, and each vector is
    then renormalised to sum to 1. The divergence is
    `sum(p * log(p / q))` over the slots.

    Adding epsilon BEFORE renormalising is what keeps the result finite. Two
    things would otherwise blow up on real data: a slot with zero real
    attention makes the term `0 * log(0 / q)` evaluate to `0 * -inf` = NaN in
    floating point, and a slot the synthetic panel gave zero attention makes
    `log(p / 0)` infinite. Smoothing first removes both, at the cost of a
    small, uniform and predictable bias.

    Asymmetric by definition: `heatmap_kl(a, b) != heatmap_kl(b, a)`, and the
    real distribution goes first. Identical inputs give exactly 0.0, and an
    empty `slot_ids` gives 0.0 rather than dividing by a zero total.
    """
    real_vec = [real.get(slot_id, 0.0) + eps for slot_id in slot_ids]
    synth_vec = [synth.get(slot_id, 0.0) + eps for slot_id in slot_ids]

    real_total = sum(real_vec)
    synth_total = sum(synth_vec)
    if real_total <= 0 or synth_total <= 0:
        return 0.0

    divergence = 0.0
    for real_value, synth_value in zip(real_vec, synth_vec):
        p = real_value / real_total
        q = synth_value / synth_total
        divergence += p * math.log(p / q)
    return divergence


def ad_slot_index_spearman(
    real: Mapping[str, float],
    synth: Mapping[str, float],
    ad_slot_ids: Sequence[str],
) -> float:
    """Spearman rank correlation over the ad slots only (the Ad Slot Index).

    Identical maths to `attention_spearman` -- it delegates rather than
    repeating it -- but reported separately, because
    schemas/metrics.schema.json carries `ad_slot_index_spearman` alongside
    `attention_spearman` and the two answer different questions: whether the
    personas rank the *creatives* the way real shoppers do, versus whether
    they rank the *products* that way. `ad_slot_ids` are the planogram's
    `ad_slot_id`s, not product slot ids.

    Inherits the guard: a constant vector on either side (no creative drew
    any attention) returns 0.0 rather than NaN.
    """
    return attention_spearman(real, synth, ad_slot_ids)


def decision_agreement(
    real_by_variant: Mapping[str, float],
    synth_by_variant: Mapping[str, float],
    kpi: str,
) -> dict[str, Any]:
    """Do both panels pick the same winning variant on the focal KPI?

    `real_by_variant` and `synth_by_variant` map variant_id -> that variant's
    value of `kpi`. The winner on each side is its argmax; `agree` is whether
    the two winners are the same variant. This is the decision the metric
    actually protects: an experiment can miss on absolute numbers and still
    be useful if it recommends the same shelf, and can look accurate while
    recommending the wrong one.

    Ties are broken by sorted variant id -- the alphabetically first of the
    tied variants wins -- so the answer never depends on dict insertion order
    and RESULTS.md regenerates identically.

    Each side is argmaxed over its own keys; a variant only one panel scored
    is not dropped, because silently ignoring it would hide a disagreement.

    Returns exactly the `decision_agreement` block of
    schemas/metrics.schema.json: {kpi, winner_real, winner_synth, agree},
    with `agree` a real bool. An empty mapping on either side has no argmax
    and raises ValueError rather than inventing a winner.
    """
    winner_real = _argmax_variant(real_by_variant, "real")
    winner_synth = _argmax_variant(synth_by_variant, "synth")

    return {
        "kpi": kpi,
        "winner_real": winner_real,
        "winner_synth": winner_synth,
        "agree": winner_real == winner_synth,
    }


def _argmax_variant(by_variant: Mapping[str, float], side: str) -> str:
    """Variant id with the highest value, ties broken by sorted id.

    `max` keeps the first item holding the greatest key, so iterating the
    ids in sorted order makes the alphabetically first of any tied group the
    winner, deterministically.
    """
    if not by_variant:
        raise ValueError(f"{side} panel has no variants to pick a winner from")
    return max(sorted(by_variant), key=lambda variant_id: by_variant[variant_id])
