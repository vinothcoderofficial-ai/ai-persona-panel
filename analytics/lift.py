"""Ad-to-Purchase Lift -- what the ad exposure was actually worth (PLAN S18).

    lift = (purchase share of the advertised brand among ad-exposed shoppers
            - among non-exposed) / non-exposed

This is the project's headline metric and is on PLAN section 9's never-drop
list. Attention is a commodity -- predictive-attention vendors already sell
heatmaps. This number is the one they cannot produce, because they do not
model purchase.

It is reported per persona (plus a population row) for BOTH panels, and both
panels go through the same three functions -- `brand_share` to aggregate SKU
shares up to the advertised brand, and `lift` to divide -- so "the synthetic
number and the real number are the same arithmetic" is a property of the code
rather than a claim in a README.

Exposure, and which exposure
----------------------------
`sim/simulator.py` uses two scopes of ad exposure by design. Its *purchase
utility* uses BAY-LOCAL exposure with a brand match, because that is how SPEC
M4 writes the utility term. Its `ad_exposed_purchase_share` /
`ad_unexposed_purchase_share` split uses TRIP-LEVEL exposure -- the shopper
fixated any ad slot anywhere on the trip.

Trip-level is the correct basis for this metric, because it is how a real
panel splits: you know whether a person saw the ad on their trip, not which
shelf they were standing at when the utility moved. So this module reads the
two committed SimResult vectors and never re-derives exposure, and the real
panel is split the same way -- did this session touch a creative-carrying ad
slot at any point.

What is undefined, and how it is reported
-----------------------------------------
Two things make the ratio undefined, and neither is ever reported as 0.0:

* **An empty arm.** No exposed shoppers, or no unexposed shoppers, or an arm
  that recorded no purchases at all. `brand_share` returns None for it. A
  real panel this thin is normal early in a study.
* **A zero denominator.** The unexposed arm bought things, but none of them
  the advertised brand, so `share_unexposed` is 0. `lift` returns None. This
  is exactly the case where a naive implementation reports `inf` and poisons
  a headline number: a persona that never buys the brand unexposed is the
  most interesting persona in the deck, not a division accident.

In the emitted block, an undefined `real` is the JSON value `null` (the
schema types it `["number", "null"]`) and an undefined `synth` is an ABSENT
key, because the schema types `synth` as a plain number and cannot hold null.

The two intervals, and why they are not the same kind of thing
--------------------------------------------------------------
A row can carry two intervals, under deliberately different names.

**`ci95` is a confidence interval and it belongs to `real`.** It resamples the
real panel's SHOPPERS with replacement -- the same unit, the same
`np.random.default_rng(seed)` and the same 2.5/97.5 percentiles as
`analytics/fusion.py:bootstrap_ci`. `noise_ceiling.ci95` in the same schema is
likewise an interval over the real panel, and this name keeps meaning that.

The panel is resampled POOLED, keeping each shopper's exposure flag, and then
re-split -- so the uncertainty in the exposure rate itself is inside the
interval, which it would not be if the two arms were resampled separately at
fixed sizes. Resamples whose own denominator is undefined are dropped, and if
fewer than `MIN_DEFINED_FRACTION` of them survive there is no interval at all
rather than one built from a minority of the draws.

**`synth_mc95` is not a confidence interval.** `synth` is a deterministic
function of (planogram, policy, seed, n_runs), and the synthetic panel is not
a sample drawn from a population of shoppers, so it has no sampling
uncertainty to report. What `synth_mc95` reports is Monte Carlo resolution:
resample each arm's purchase EVENTS at that arm's own event count and see how
far the ratio moves. It answers "is this number resolved at `n_runs`, or is it
noise?" -- the same question `analytics/optimizer.py` answers with
`SeedSpread`, and it is named apart from `ci95` for the same reason.

The interval the project is judged on stays `ci95`. "The synthetic lift of
+18 % sits inside the real panel's 95 % CI of [11 %, 25 %]" is the sentence
this block exists to support, and it needs `ci95` to keep meaning the real
panel. `synth_mc95` only says whether that +18 % is itself resolved; a tight
one is not evidence that the personas are right.

`synth_mc95` needs `n_purchases_exposed` / `n_purchases_unexposed`, the arms'
purchase-event counts. `sim/simulator.py` emits them and they are optional in
schemas/simresult.schema.json, so a SimResult predating them still reports
`synth` -- just without an interval.

Pure: no HTTP, no file I/O, no globals, no wall-clock randomness. `seed` is
required and keyword-only, because `scripts/eval.py` has to regenerate
RESULTS.md identically from the committed sessions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Collection, Iterable, Mapping, Sequence

import numpy as np

# 1,000 resamples -> 95 % CI, matching analytics/fusion.py (SPEC M5).
DEFAULT_N_BOOT = 1000
DEFAULT_CI = 0.95

# The row key the population figure is reported under. `sim.simulator.combine`
# already labels its share-weighted population result `persona_id:
# "population"`; pass that result under this key to get the population row.
# This module does no share weighting of its own -- combine() owns it.
POPULATION_KEY = "population"

# A real session counts as ad-exposed if it produced any of these on an ad
# slot carrying the creative. `gaze` is deliberately absent: the raw stream is
# not a look, `fixation` is (see web/src/capture/FixationFilter.ts).
# `add_to_cart` and `remove` cannot land on an ad slot at all.
EXPOSURE_EVENT_TYPES = frozenset({"fixation", "hover", "pickup"})

# Below this fraction of bootstrap resamples yielding a defined lift, the
# percentiles would describe a minority of the draws, so no interval is
# reported.
MIN_DEFINED_FRACTION = 0.5

# The two SimResult fields this metric reads. Both are optional in
# schemas/simresult.schema.json, so their absence is a real possibility and
# has to be an error rather than a silent "undefined".
EXPOSED_FIELD = "ad_exposed_purchase_share"
UNEXPOSED_FIELD = "ad_unexposed_purchase_share"

# The arms' purchase-event counts, which the two share vectors above cannot
# carry because `sim.simulator._share` normalises them. Also optional in the
# schema: a SimResult predating them reports `synth` without `synth_mc95`.
EXPOSED_COUNT_FIELD = "n_purchases_exposed"
UNEXPOSED_COUNT_FIELD = "n_purchases_unexposed"


@dataclass(frozen=True)
class Shopper:
    """One real shopper: were they exposed to the creative, and what did they
    put in the cart.

    `basket` is the sku_ids of the session's `add_to_cart` events, in order,
    with repeats kept -- buying two packs of the same SKU is two purchases,
    the same way the simulator counts two purchase events.
    """

    exposed: bool
    basket: tuple[str, ...]


# ---------------------------------------------------------------------------
# reading the planogram (no maths -- just the three lookups this metric needs)
# ---------------------------------------------------------------------------


def sku_brands(planogram: Mapping) -> dict[str, str]:
    """`sku_id -> brand` for every SKU in a resolved planogram."""
    return {sku["sku_id"]: sku["brand"] for sku in planogram["skus"]}


def creative_brand(planogram: Mapping, creative_id: str) -> str:
    """The brand a creative advertises -- the brand whose share this metric
    measures. Raises ValueError for an unknown creative rather than guessing.
    """
    for creative in planogram.get("creatives", []):
        if creative["creative_id"] == creative_id:
            return creative["brand"]
    raise ValueError(f"planogram has no creative {creative_id!r}")


def ad_slots_showing(planogram: Mapping, creative_id: str) -> tuple[str, ...]:
    """The ad slots currently carrying `creative_id`, in planogram order.

    These are the slots a real session has to have looked at to count as
    exposed. An empty tuple is a legitimate answer: a variant can leave a
    creative unplaced (data/variants/C.json does exactly that to AD_1's
    original slot), and then nobody is exposed to it.
    """
    return tuple(
        ad["ad_slot_id"]
        for bay in planogram["bays"]
        for ad in bay["ad_slots"]
        if ad["creative_id"] == creative_id
    )


# ---------------------------------------------------------------------------
# the formula
# ---------------------------------------------------------------------------


def brand_share(
    purchases: Mapping[str, float],
    brand_of_sku: Mapping[str, str],
    brand: str,
) -> float | None:
    """The advertised brand's share of one arm's purchases.

    `purchases` is sku_id -> weight: either a SimResult arm vector (already
    normalised to sum to 1) or a raw count per SKU. Either works, because the
    function normalises by the observed total rather than assuming 1.0 --
    which is what lets the synthetic and real panels share this function.

    A brand with several SKUs sums them; the denominator is every purchase in
    the arm, whatever the brand.

    Returns None for an EMPTY arm -- total weight of 0, which is exactly what
    `sim.simulator._share` emits when nobody in that arm bought anything, and
    what a real arm with no sessions or no `add_to_cart` events gives. None
    means "there is no share to report here", which is different from a share
    of 0.0 ("they bought, just never this brand"), and the two must not be
    confused: one is undefined, the other is a -100 % lift.

    A sku_id absent from `brand_of_sku` raises ValueError. Dropping it would
    silently shrink the denominator and inflate the share.
    """
    total = 0.0
    matching = 0.0
    for sku_id, weight in purchases.items():
        if sku_id not in brand_of_sku:
            raise ValueError(f"sku {sku_id!r} is not in the planogram's sku -> brand map")
        value = float(weight)
        total += value
        if brand_of_sku[sku_id] == brand:
            matching += value

    if total <= 0.0:
        return None
    return matching / total


def lift(exposed_share: float | None, unexposed_share: float | None) -> float | None:
    """`(exposed - unexposed) / unexposed`, guarded (PLAN S18).

    Returns None -- never `inf`, never `nan`, never 0.0 -- when either arm is
    undefined (None from `brand_share`) or when `unexposed_share` is 0. A zero
    denominator is not a huge lift, it is an unanswerable question: with no
    unexposed baseline there is nothing to be a percentage of.

    An exposed share of 0 against a positive unexposed share IS answerable and
    returns exactly -1.0: the exposed group bought the brand not at all.
    """
    if exposed_share is None or unexposed_share is None:
        return None
    if unexposed_share <= 0.0:
        return None
    return (exposed_share - unexposed_share) / unexposed_share


# ---------------------------------------------------------------------------
# the two panels
# ---------------------------------------------------------------------------


def synth_lift(sim_result: Mapping, *, brand_of_sku: Mapping[str, str], brand: str) -> float | None:
    """The synthetic panel's lift, from a SimResult's two arm vectors.

    Reads `ad_exposed_purchase_share` and `ad_unexposed_purchase_share`
    exactly as `sim/simulator.py` wrote them -- trip-level exposure, SKU
    shares within the arm -- and puts them through `brand_share` and `lift`.

    Raises ValueError if either field is missing. Both are optional in
    schemas/simresult.schema.json, so a SimResult predating S16 will not have
    them; answering None there would report "undefined lift" for what is
    really "this run cannot answer the question".
    """
    arms = []
    for field in (EXPOSED_FIELD, UNEXPOSED_FIELD):
        arm = sim_result.get(field)
        if arm is None:
            raise ValueError(
                f"SimResult has no {field!r}; ad-to-purchase lift needs both arm vectors"
            )
        arms.append(brand_share(arm, brand_of_sku, brand))
    return lift(arms[0], arms[1])


def split_panel(
    sessions: Iterable[Sequence[Mapping]],
    *,
    ad_slot_ids: Collection[str],
) -> tuple[Shopper, ...]:
    """Split a real panel into ad-exposed and non-exposed shoppers.

    `sessions` is one event list per **accepted** session, each event shaped
    by schemas/event.schema.json. Filtering to accepted sessions is the
    caller's job, the same way it is for `analytics/fusion.py` -- this module
    does not read schemas/session.schema.json.

    `ad_slot_ids` are the ad slots carrying the creative under test; use
    `ad_slots_showing(planogram, creative_id)`. A session is exposed when it
    produced a `fixation`, `hover` or `pickup` naming one of them.

    The basket is the session's `add_to_cart` events' `sku_id`s, in order.
    `remove` is NOT netted out: PLAN S18 defines the real arm over
    `add_to_cart` events, and `analytics/fusion.py` likewise treats
    `add_to_cart` as the interaction and ignores `remove`.
    """
    slots = set(ad_slot_ids)
    shoppers: list[Shopper] = []

    for events in sessions:
        exposed = False
        basket: list[str] = []
        for event in events:
            payload = event.get("payload") or {}
            event_type = event.get("type")
            if event_type in EXPOSURE_EVENT_TYPES and payload.get("slot_id") in slots:
                exposed = True
            elif event_type == "add_to_cart":
                sku_id = payload.get("sku_id")
                if sku_id is not None:
                    basket.append(sku_id)
        shoppers.append(Shopper(exposed=exposed, basket=tuple(basket)))

    return tuple(shoppers)


def real_lift(
    shoppers: Sequence[Shopper],
    *,
    brand_of_sku: Mapping[str, str],
    brand: str,
) -> float | None:
    """The real panel's lift, from `split_panel`'s shoppers.

    Pools each arm's baskets into a per-SKU purchase count and puts them
    through the same `brand_share` and `lift` the synthetic panel uses.
    Returns None whenever either arm is empty or the unexposed arm bought
    none of the advertised brand.
    """
    exposed_counts = _pool(shopper.basket for shopper in shoppers if shopper.exposed)
    unexposed_counts = _pool(shopper.basket for shopper in shoppers if not shopper.exposed)
    return lift(
        brand_share(exposed_counts, brand_of_sku, brand),
        brand_share(unexposed_counts, brand_of_sku, brand),
    )


def bootstrap_lift_ci(
    shoppers: Sequence[Shopper],
    *,
    brand_of_sku: Mapping[str, str],
    brand: str,
    seed: int,
    n_boot: int = DEFAULT_N_BOOT,
    ci: float = DEFAULT_CI,
) -> tuple[float, float] | None:
    """Bootstrap confidence interval around `real_lift`, over resampled shoppers.

    **What is resampled:** the panel of shoppers, POOLED and with replacement,
    `n_boot` times, each resample the same size as the panel. Each drawn
    shopper brings their exposure flag and their whole basket, so a resample
    is re-split into the two arms afterwards and the arms' sizes vary from
    draw to draw. That is deliberate: the exposure rate is itself estimated
    from this panel, and resampling the arms separately at fixed sizes would
    quietly drop that source of uncertainty.

    **What the interval therefore means:** the spread of lifts you would see
    if you had drawn a different panel of the same size from the same
    population of shoppers. It says nothing about whether the personas are
    right, and nothing about the simulator's Monte Carlo error -- see the
    module docstring on why `ci95` belongs to the real panel.

    `seed` is required and keyword-only and feeds `np.random.default_rng`, so
    the same panel and seed always give byte-identical bounds; RESULTS.md has
    to regenerate exactly.

    Returns None rather than an interval when there is nothing to resample (an
    empty panel) or when fewer than `MIN_DEFINED_FRACTION` of the resamples
    have a defined lift -- a resample can land with an empty arm, or with an
    unexposed arm that bought none of the brand, and those have no ratio.
    Undefined resamples are dropped from the percentiles.
    """
    if n_boot < 1:
        raise ValueError(f"n_boot must be at least 1, got {n_boot!r}")
    if not 0.0 < ci < 1.0:
        raise ValueError(f"ci must be in (0, 1), got {ci!r}")

    n_shoppers = len(shoppers)
    if n_shoppers == 0:
        return None

    exposed = np.array([shopper.exposed for shopper in shoppers], dtype=bool)
    brand_bought = np.empty(n_shoppers, dtype=np.float64)
    total_bought = np.empty(n_shoppers, dtype=np.float64)
    for i, shopper in enumerate(shoppers):
        for sku_id in shopper.basket:
            if sku_id not in brand_of_sku:
                raise ValueError(f"sku {sku_id!r} is not in the planogram's sku -> brand map")
        brand_bought[i] = sum(1 for s in shopper.basket if brand_of_sku[s] == brand)
        total_bought[i] = len(shopper.basket)

    rng = np.random.default_rng(seed)
    draws = rng.integers(0, n_shoppers, size=(n_boot, n_shoppers))
    drawn_exposed = exposed[draws]
    drawn_brand = brand_bought[draws]
    drawn_total = total_bought[draws]

    exposed_brand = np.where(drawn_exposed, drawn_brand, 0.0).sum(axis=1)
    exposed_total = np.where(drawn_exposed, drawn_total, 0.0).sum(axis=1)
    unexposed_brand = np.where(drawn_exposed, 0.0, drawn_brand).sum(axis=1)
    unexposed_total = np.where(drawn_exposed, 0.0, drawn_total).sum(axis=1)

    # `brand_share` and `lift` applied to a whole stack of resamples at once:
    # matching / total per arm, then (exposed - unexposed) / unexposed. The
    # guards are the same two -- an arm with no purchases and a zero
    # denominator -- expressed as the `defined` mask instead of a None.
    exposed_share = np.divide(exposed_brand, exposed_total,
                              out=np.zeros_like(exposed_brand), where=exposed_total > 0.0)
    unexposed_share = np.divide(unexposed_brand, unexposed_total,
                                out=np.zeros_like(unexposed_brand), where=unexposed_total > 0.0)

    defined = (exposed_total > 0.0) & (unexposed_total > 0.0) & (unexposed_share > 0.0)
    if int(defined.sum()) < MIN_DEFINED_FRACTION * n_boot:
        return None

    lifts = ((exposed_share[defined] - unexposed_share[defined])
             / unexposed_share[defined])

    tail = (1.0 - ci) / 2.0 * 100.0
    return float(np.percentile(lifts, tail)), float(np.percentile(lifts, 100.0 - tail))


def bootstrap_synth_lift_ci(
    sim_result: Mapping,
    *,
    brand_of_sku: Mapping[str, str],
    brand: str,
    seed: int,
    n_boot: int = DEFAULT_N_BOOT,
    ci: float = DEFAULT_CI,
) -> tuple[float, float] | None:
    """Monte Carlo interval around `synth_lift`, over resampled purchase events.

    **This is not a confidence interval and it is not `ci95`.** The synthetic
    panel is a deterministic function of (planogram, policy, seed, n_runs), not
    a sample from a population of shoppers. What this measures is how far the
    reported lift would move if the same configuration were re-run at the same
    `n_runs` -- Monte Carlo resolution, not sampling uncertainty. See the
    module docstring, and `analytics/optimizer.py:SeedSpread` for the same
    distinction drawn about a different number.

    **What is resampled:** each arm's purchase EVENTS, `n_boot` times, as a
    multinomial over that arm's own per-SKU share vector at that arm's own
    event count. Only the advertised brand's marginal reaches the ratio, and
    the marginal of a `Multinomial(n, p)` summed over the brand's SKUs is
    exactly `Binomial(n, brand share)` -- so that is what is drawn. The
    identity is checked numerically against a written-out multinomial
    bootstrap in `analytics/tests/test_lift.py`, not just asserted here.

    **What it does not capture,** and why it is systematically narrower than
    the real panel's interval:

    * The two arms' event counts are held FIXED at what the run produced.
      `bootstrap_lift_ci` resamples shoppers pooled and re-splits, so the
      exposure rate's own uncertainty is inside `ci95` and is not inside this.
    * Nothing about whether the personas, the policies or the saliency model
      are right. A tight interval here says the simulator is self-consistent
      at this run size, not that it is correct.

    Returns None -- never `inf`, never `nan` -- when either arm recorded no
    purchases (nothing to resample), when either arm's brand share is
    undefined, or when fewer than `MIN_DEFINED_FRACTION` of the draws land
    with a non-zero denominator. Raises ValueError on a SimResult carrying no
    purchase-event counts: a run predating them is a different thing from a
    run whose arms were empty, and must not be reported as one.

    `seed` is required and keyword-only for the reason `bootstrap_lift_ci`'s
    is -- RESULTS.md has to regenerate byte-identically. It feeds a separate
    `np.random.default_rng(seed)`; the two bootstraps run over disjoint data
    with different draw shapes, so sharing a seed couples nothing.
    """
    if n_boot < 1:
        raise ValueError(f"n_boot must be at least 1, got {n_boot!r}")
    if not 0.0 < ci < 1.0:
        raise ValueError(f"ci must be in (0, 1), got {ci!r}")

    counts: list[int] = []
    for field in (EXPOSED_COUNT_FIELD, UNEXPOSED_COUNT_FIELD):
        value = sim_result.get(field)
        if value is None:
            raise ValueError(
                f"SimResult has no {field!r}; the synthetic interval needs both arms' "
                "purchase-event counts"
            )
        count = int(value)
        if count < 0:
            raise ValueError(f"{field} must be at least 0, got {count!r}")
        counts.append(count)
    n_exposed, n_unexposed = counts

    shares = []
    for field in (EXPOSED_FIELD, UNEXPOSED_FIELD):
        arm = sim_result.get(field)
        if arm is None:
            raise ValueError(
                f"SimResult has no {field!r}; ad-to-purchase lift needs both arm vectors"
            )
        shares.append(brand_share(arm, brand_of_sku, brand))
    exposed_share, unexposed_share = shares

    if not n_exposed or not n_unexposed:
        return None
    if exposed_share is None or unexposed_share is None:
        return None

    rng = np.random.default_rng(seed)
    drawn_exposed = rng.binomial(n_exposed, exposed_share, size=n_boot) / n_exposed
    drawn_unexposed = rng.binomial(n_unexposed, unexposed_share, size=n_boot) / n_unexposed

    # The same zero-denominator guard as `lift`, expressed as a mask: a draw in
    # which the unexposed arm bought none of the brand has no ratio.
    defined = drawn_unexposed > 0.0
    if int(defined.sum()) < MIN_DEFINED_FRACTION * n_boot:
        return None

    lifts = (drawn_exposed[defined] - drawn_unexposed[defined]) / drawn_unexposed[defined]

    tail = (1.0 - ci) / 2.0 * 100.0
    return float(np.percentile(lifts, tail)), float(np.percentile(lifts, 100.0 - tail))


# ---------------------------------------------------------------------------
# the reported block
# ---------------------------------------------------------------------------


def ad_to_purchase_lift(
    synth: Mapping[str, Mapping],
    *,
    brand_of_sku: Mapping[str, str],
    brand: str,
    real: Mapping[str, Sequence[Shopper]] | None = None,
    seed: int,
    n_boot: int = DEFAULT_N_BOOT,
    ci: float = DEFAULT_CI,
) -> dict[str, dict]:
    """The `ad_to_purchase_lift` block of schemas/metrics.schema.json.

    `synth` maps a row key -> that row's SimResult. The row keys are yours:
    one per persona, plus `POPULATION_KEY` for the share-weighted population
    result from `sim.simulator.combine` (pass it in and it is reported like
    any other row -- this module never re-derives the share weighting).

    `real` maps the SAME row keys -> that row's shoppers, from `split_panel`.
    Omit it entirely and no row claims a real number; include a key `synth`
    does not have and it raises, because a mistyped `archetype_label` would
    otherwise make a whole segment of the real panel silently vanish.

    Each row holds at most four keys, and each is absent rather than faked:

      * `synth` -- the synthetic lift. ABSENT when undefined, because the
        schema types it as a plain number and cannot carry null.
      * `synth_mc95` -- `[low, high]` Monte Carlo spread around `synth`, from
        `bootstrap_synth_lift_ci`. NOT a confidence interval (see the module
        docstring). Present only when `synth` is a number, the SimResult
        carries the arms' purchase-event counts, and the resample could
        support an interval.
      * `real` -- the real panel's lift, or `null` when undefined. Present
        for every row `real` covers; never 0.0 standing in for "we do not
        know".
      * `ci95` -- `[low, high]` confidence interval around `real`, from
        `bootstrap_lift_ci`. Present only when `real` is a number and the
        bootstrap could support an interval. It is the REAL panel's interval
        and stays that way; `synth_mc95` never displaces it.

    Rows come out in sorted key order so RESULTS.md regenerates identically.
    """
    if real is not None:
        unknown = sorted(set(real) - set(synth))
        if unknown:
            raise ValueError(
                f"real panel has rows the synthetic panel does not: {unknown}; "
                "every reported row needs a SimResult"
            )

    block: dict[str, dict] = {}
    for key in sorted(synth):
        row: dict = {}

        result = synth[key]
        synthetic = synth_lift(result, brand_of_sku=brand_of_sku, brand=brand)
        if synthetic is not None:
            row["synth"] = float(synthetic)
            # Skipped rather than raised when the counts are absent: that is a
            # SimResult predating them, and `synth` is still perfectly good.
            if EXPOSED_COUNT_FIELD in result and UNEXPOSED_COUNT_FIELD in result:
                spread = bootstrap_synth_lift_ci(
                    result, brand_of_sku=brand_of_sku, brand=brand,
                    seed=seed, n_boot=n_boot, ci=ci,
                )
                if spread is not None:
                    row["synth_mc95"] = [float(spread[0]), float(spread[1])]

        if real is not None and key in real:
            shoppers = real[key]
            observed = real_lift(shoppers, brand_of_sku=brand_of_sku, brand=brand)
            row["real"] = None if observed is None else float(observed)
            if observed is not None:
                interval = bootstrap_lift_ci(
                    shoppers, brand_of_sku=brand_of_sku, brand=brand,
                    seed=seed, n_boot=n_boot, ci=ci,
                )
                if interval is not None:
                    row["ci95"] = [float(interval[0]), float(interval[1])]

        block[key] = row

    return block


def _pool(baskets: Iterable[Sequence[str]]) -> dict[str, float]:
    """Pool baskets into `sku_id -> purchase count`. Repeats count."""
    counts: dict[str, float] = {}
    for basket in baskets:
        for sku_id in basket:
            counts[sku_id] = counts.get(sku_id, 0.0) + 1.0
    return counts
