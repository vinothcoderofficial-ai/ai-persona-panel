"""Comparison metrics between a real shopper's fused attention (or purchase
shares) and a synthetic population's prediction -- the numbers the dashboard,
`POST /experiments` and `scripts/eval.py` report.

Pure and small: stdlib plus scipy.stats only. No I/O, no globals.
"""

from typing import Mapping, Sequence

from scipy.stats import spearmanr


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
