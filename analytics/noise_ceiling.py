"""The real panel's noise ceiling -- how well it agrees with *itself* -- and
the `relative_agreement` ratio that benchmarks the synthetic panel against it
(PLAN S17: "200 half-splits -> mean rho + 2.5/97.5 percentiles;
relative_agreement = min(1, rho / ceiling)").

Why this exists at all: a synthetic-vs-real Spearman of 0.6 means nothing on
its own. If the real panel split in half only agrees with itself at 0.65, then
0.6 is close to everything the data can support and the personas are doing
well. If it agrees with itself at 0.95, the same 0.6 is a poor model. Every
accuracy claim in RESULTS.md is quoted against this number, which is why
PLAN §9 lists it under "never drop".

The procedure, per split: shuffle the accepted sessions, cut them into two
disjoint halves, aggregate each half with `fusion.trimmed_mean` (the panel
estimator the real reported numbers use -- not a second aggregation formula
invented here), and take `metrics.attention_spearman` between the two halves.
Repeat `n_splits` times; report the mean and the 2.5/97.5 percentiles.

The halves are complementary, not independent samples -- what a session
contributes to one half it withholds from the other. That is what split-half
reliability means, and it does not bias the estimate: over slots the shared
signal raises the covariance between the two half-means while the withheld
noise cancels it, so a panel with no repeatable signal centres on 0 and one
with a strong shared ranking goes to 1. A ceiling that comes out **negative**
therefore means "this panel does not repeat", not "the code is broken", and
`relative_agreement` below turns it into 0.0 rather than a ratio.

Pure: no I/O, no globals, no wall-clock randomness. `seed` is required and
keyword-only because `scripts/eval.py` has to regenerate RESULTS.md
identically from the committed sessions.

Both public functions return exactly what schemas/metrics.schema.json asks
for: `noise_ceiling` returns the `noise_ceiling` block, `relative_agreement`
returns the `relative_agreement` number.
"""

from typing import Mapping, Sequence

import numpy as np

from analytics.fusion import trimmed_mean
from analytics.metrics import attention_spearman

# PLAN S17: 200 half-splits.
DEFAULT_N_SPLITS = 200

# Below this the split-half is not measuring a panel. At n = 3 each half holds
# a single session, so the "ceiling" would be the agreement between two
# individuals -- a number that looks like a panel statistic and is not one.
MIN_SESSIONS = 4

# The percentiles the reported interval is cut at (PLAN S17: "2.5/97.5").
CI_LOWER_PERCENTILE = 2.5
CI_UPPER_PERCENTILE = 97.5


def noise_ceiling(
    per_session: Sequence[Mapping[str, float]],
    slot_ids: Sequence[str],
    *,
    n_splits: int = DEFAULT_N_SPLITS,
    seed: int,
) -> dict:
    """Split-half repeatability of a panel of fused sessions.

    `per_session` is one `fusion.fuse_session` output per accepted session;
    `slot_ids` is the shared slot vocabulary both halves are aggregated over.

    Each of the `n_splits` iterations draws a fresh random permutation of the
    sessions and takes the first `n // 2` as one half and the next `n // 2` as
    the other. With an odd panel the leftover session sits out that split --
    which one it is changes from split to split, so no session is
    systematically excluded and the two halves are always the same size (an
    unequal split would give the halves different noise levels and bias the
    correlation downward).

    Returns the `noise_ceiling` block of schemas/metrics.schema.json:
    `{"spearman_mean": float, "ci95": [lo, hi], "n_splits": int}`.

    No NaN can escape: `metrics.attention_spearman` already returns 0.0 rather
    than NaN when a half's aggregate is constant (an all-zero panel, say), so
    the mean and percentiles are always real numbers.

    `ci95` is a percentile interval over the observed splits, not a bootstrap
    interval and not symmetric about the mean.

    Raises ValueError for a panel below `MIN_SESSIONS` or for `n_splits < 1`,
    rather than returning a number that would be read as a panel statistic.
    """
    n_sessions = len(per_session)
    if n_sessions < MIN_SESSIONS:
        raise ValueError(
            f"noise ceiling needs at least {MIN_SESSIONS} sessions to split, got {n_sessions}"
        )
    if n_splits < 1:
        raise ValueError(f"n_splits must be at least 1, got {n_splits!r}")

    rng = np.random.default_rng(seed)
    half = n_sessions // 2
    spearmans = np.empty(n_splits, dtype=float)

    for split in range(n_splits):
        order = rng.permutation(n_sessions)
        first = [per_session[i] for i in order[:half]]
        second = [per_session[i] for i in order[half : 2 * half]]
        spearmans[split] = attention_spearman(
            trimmed_mean(first, slot_ids),
            trimmed_mean(second, slot_ids),
            slot_ids,
        )

    return {
        "spearman_mean": float(spearmans.mean()),
        "ci95": [
            float(np.percentile(spearmans, CI_LOWER_PERCENTILE)),
            float(np.percentile(spearmans, CI_UPPER_PERCENTILE)),
        ],
        "n_splits": int(n_splits),
    }


def relative_agreement(spearman: float, ceiling_mean: float) -> float:
    """The synthetic panel's Spearman as a fraction of what the real panel
    manages against itself: `min(1, rho / ceiling)`, clamped at both ends.

    Clamped ABOVE at 1.0 (PLAN S17): a model cannot agree with the real panel
    better than the panel agrees with itself in any meaningful sense, so the
    excess is measurement noise, not accuracy. Reporting 1.2 would read as
    "120 % accurate".

    Clamped BELOW at 0.0: a negative rho -- the personas ranked the shelf
    backwards -- divided by a positive ceiling gives a negative ratio, and
    divided by a *negative* ceiling gives a positive one. Without the lower
    clamp that second case would report an anti-correlated prediction against
    a self-contradictory panel as partial agreement. Both land at 0.0.

    A ceiling at or below 0 means the panel does not agree with itself at all;
    there is nothing to be a fraction of, so the answer is 0.0 -- which also
    guards the division.
    """
    if ceiling_mean <= 0.0:
        return 0.0
    return float(min(1.0, max(0.0, spearman / ceiling_mean)))
