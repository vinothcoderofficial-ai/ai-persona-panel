"""Vectorised Monte Carlo shopper simulator (SPEC M4).

Ten thousand synthetic shoppers walk a resolved planogram under one persona policy. The code is
vectorised over shoppers: it loops over stations and over the two purchase candidates, never over
shoppers. Budget is 10,000 shoppers x 4 personas in under 800 ms, because this sits on the what-if
hot path.

Two attention layers meet here. `sim.saliency` says what anyone would notice; the policy reweights
it by goals, brand affinity, price and promotion, blended by `exploration`:

    relevance = 0.5*goal_match + 0.3*brand_affinity + 0.1*(1-price_norm)*price_sensitivity
                + 0.1*promo*promo_sensitivity
    gate      = 1.0 where goal_match > 0, else exploration
    weight    = p_saliency**exploration * relevance**(1-exploration) * gate

The gate is what makes the two ends of the exploration range behave: at 0 a shopper can only look
at goal-category slots, at 1 the weights collapse to p_saliency exactly.

Fixation targets are occupied slots plus ad slots carrying a creative. Empty slots still occupy
shelf space (see sim/saliency.py) but are never looked at.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np

from .saliency import BaySaliency, compute_saliency

STATION_MOVE_S = 4.0
PURCHASE_GUMBEL_SCALE = 0.1
MAX_PURCHASE_CANDIDATES = 2


@dataclass(frozen=True)
class Store:
    """Planogram flattened into numpy arrays. Built once, reused by every persona and run."""

    planogram_id: str
    saliency: dict[str, BaySaliency]
    bay_ids: tuple[str, ...]
    bay_slice: tuple[slice, ...]
    target_ids: tuple[str, ...]
    is_ad: np.ndarray
    sku_of_target: np.ndarray
    brand_of_target: np.ndarray
    cat_of_target: np.ndarray
    price_norm_of_target: np.ndarray
    promo_of_target: np.ndarray
    p_saliency: np.ndarray
    mean_saliency_bay: np.ndarray
    goal_slots_bay: np.ndarray
    occupied_bay: np.ndarray
    categories: tuple[str, ...]
    brands: tuple[str, ...]
    sku_ids: tuple[str, ...]
    sku_brand: np.ndarray
    sku_category: np.ndarray
    sku_price_norm: np.ndarray
    sku_promo: np.ndarray
    ad_slot_ids: tuple[str, ...]
    ad_target_pos: np.ndarray

    @property
    def n_targets(self) -> int:
        return len(self.target_ids)

    @property
    def n_bays(self) -> int:
        return len(self.bay_ids)


def build_store(planogram: Mapping, weights: Mapping[str, float] | None = None) -> Store:
    """Flatten a *resolved* planogram into the arrays the simulator runs on."""
    saliency = compute_saliency(planogram, weights)

    skus = list(planogram["skus"])
    sku_ids = tuple(s["sku_id"] for s in skus)
    sku_pos = {sku_id: i for i, sku_id in enumerate(sku_ids)}
    creatives = {c["creative_id"]: c for c in planogram.get("creatives", [])}

    categories = tuple(sorted({s["category"] for s in skus}))
    cat_pos = {c: i for i, c in enumerate(categories)}
    brands = tuple(sorted({s["brand"] for s in skus} | {c["brand"] for c in creatives.values()}))
    brand_pos = {b: i for i, b in enumerate(brands)}

    prices = np.asarray([s["price"] for s in skus], dtype=np.float64)
    span = float(prices.max() - prices.min())
    sku_price_norm = (prices - prices.min()) / span if span > 0 else np.zeros_like(prices)
    sku_brand = np.asarray([brand_pos[s["brand"]] for s in skus], dtype=np.int64)
    sku_category = np.asarray([cat_pos[s["category"]] for s in skus], dtype=np.int64)
    sku_promo = np.asarray([1.0 if s["promo"] else 0.0 for s in skus], dtype=np.float64)

    slot_sku = {sl["slot_id"]: sl["sku_id"]
                for bay in planogram["bays"] for sh in bay["shelves"] for sl in sh["slots"]}
    ad_creative = {ad["ad_slot_id"]: ad["creative_id"]
                   for bay in planogram["bays"] for ad in bay["ad_slots"]}

    bay_ids: list[str] = []
    bay_slice: list[slice] = []
    target_ids: list[str] = []
    is_ad: list[bool] = []
    sku_of_target: list[int] = []
    brand_of_target: list[int] = []
    cat_of_target: list[int] = []
    price_norm_of_target: list[float] = []
    promo_of_target: list[float] = []
    p_saliency: list[float] = []
    mean_saliency_bay: list[float] = []
    goal_slots_bay: list[list[int]] = []
    occupied_bay: list[int] = []

    for bay in planogram["bays"]:
        bay_id = bay["bay_id"]
        bay_sal = saliency[bay_id]
        start = len(target_ids)
        counts = [0] * len(categories)
        for target_id, ad_flag, p in zip(bay_sal.target_ids, bay_sal.is_ad, bay_sal.p_saliency):
            target_ids.append(target_id)
            is_ad.append(bool(ad_flag))
            p_saliency.append(float(p))
            if ad_flag:
                # An ad slot carries no category and no price: relevance keeps only its brand term.
                sku_of_target.append(-1)
                brand_of_target.append(brand_pos[creatives[ad_creative[target_id]]["brand"]])
                cat_of_target.append(len(categories))
                price_norm_of_target.append(1.0)
                promo_of_target.append(0.0)
            else:
                i = sku_pos[slot_sku[target_id]]
                sku_of_target.append(i)
                brand_of_target.append(int(sku_brand[i]))
                cat_of_target.append(int(sku_category[i]))
                price_norm_of_target.append(float(sku_price_norm[i]))
                promo_of_target.append(float(sku_promo[i]))
                counts[int(sku_category[i])] += 1
        bay_ids.append(bay_id)
        bay_slice.append(slice(start, len(target_ids)))
        mean_saliency_bay.append(bay_sal.mean_raw)
        goal_slots_bay.append(counts)
        occupied_bay.append(sum(counts))

    target_pos = {t: i for i, t in enumerate(target_ids)}
    ad_slot_ids = tuple(ad["ad_slot_id"] for bay in planogram["bays"] for ad in bay["ad_slots"])

    return Store(
        planogram_id=planogram["planogram_id"],
        saliency=saliency,
        bay_ids=tuple(bay_ids),
        bay_slice=tuple(bay_slice),
        target_ids=tuple(target_ids),
        is_ad=np.asarray(is_ad, dtype=bool),
        sku_of_target=np.asarray(sku_of_target, dtype=np.int64),
        brand_of_target=np.asarray(brand_of_target, dtype=np.int64),
        cat_of_target=np.asarray(cat_of_target, dtype=np.int64),
        price_norm_of_target=np.asarray(price_norm_of_target, dtype=np.float64),
        promo_of_target=np.asarray(promo_of_target, dtype=np.float64),
        p_saliency=np.asarray(p_saliency, dtype=np.float64),
        mean_saliency_bay=np.asarray(mean_saliency_bay, dtype=np.float64),
        goal_slots_bay=np.asarray(goal_slots_bay, dtype=np.float64),
        occupied_bay=np.asarray(occupied_bay, dtype=np.float64),
        categories=categories,
        brands=brands,
        sku_ids=sku_ids,
        sku_brand=sku_brand,
        sku_category=sku_category,
        sku_price_norm=sku_price_norm,
        sku_promo=sku_promo,
        ad_slot_ids=ad_slot_ids,
        ad_target_pos=np.asarray([target_pos.get(a, -1) for a in ad_slot_ids], dtype=np.int64),
    )


def run(store: Store, policy: Mapping, *, n_runs: int, seed: int, variant_id: str,
        archetype: str | None = None) -> dict:
    """Simulate `n_runs` shoppers of one persona and return a SimResult dict.

    `archetype` defaults to the policy's `persona_id`; it is used for one rule only, the SPEC M4
    loop condition `goals non-empty or archetype == browser`, which lets a browser keep shopping
    after every goal category is satisfied.
    """
    n = int(n_runs)
    if n < 1:
        raise ValueError("n_runs must be at least 1")
    persona_id = str(policy["persona_id"])
    browses_without_goals = (persona_id if archetype is None else archetype) == "browser"

    rng = np.random.default_rng(seed)
    exploration = float(policy["exploration"])
    lam = float(policy["fixations_per_station"]["lam"])
    mu = float(policy["dwell_ms"]["mu"])
    sigma = float(policy["dwell_ms"]["sigma"])
    threshold = float(policy["purchase_threshold"])
    ad_receptivity = float(policy["ad_receptivity"])
    price_sensitivity = float(policy["price_sensitivity"])
    promo_sensitivity = float(policy["promo_sensitivity"])

    affinity = policy["brand_affinity"]
    fallback = float(affinity.get("_default", 0.0))
    brand_affinity = np.asarray([float(affinity.get(b, fallback)) for b in store.brands])

    n_bays, n_targets = store.n_bays, store.n_targets
    n_cat, n_sku = len(store.categories), len(store.sku_ids)

    # Everything the policy contributes that does not depend on the shopper, precomputed.
    # goal_match is boolean, so relevance only ever takes two values per target and the whole
    # weight -- p_saliency**exploration * relevance**(1-exploration) * gate -- collapses to a
    # choice between two constants per target.
    base_relevance = (
        0.3 * brand_affinity[store.brand_of_target]
        + 0.1 * (1.0 - store.price_norm_of_target) * price_sensitivity
        + 0.1 * store.promo_of_target * promo_sensitivity
    )
    saliency_pow = np.power(store.p_saliency, exploration)
    goal_weight = saliency_pow * np.power(0.5 + base_relevance, 1.0 - exploration)
    other_weight = saliency_pow * np.power(base_relevance, 1.0 - exploration) * exploration
    sku_utility = (
        0.4 * brand_affinity[store.sku_brand]
        + 0.25 * (1.0 - store.sku_price_norm) * price_sensitivity
        + 0.15 * store.sku_promo * promo_sensitivity
    )

    time_left = rng.normal(policy["time_budget_s"]["mean"], policy["time_budget_s"]["sd"], n)
    goals = np.zeros((n, n_cat + 1), dtype=bool)  # trailing column is the ad slots' "no category"
    cat_pos = {c: i for i, c in enumerate(store.categories)}
    for category in policy["goal_categories"]:
        if category in cat_pos:
            goals[:, cat_pos[category]] = True

    visited = np.zeros((n, n_bays), dtype=bool)
    stations = np.zeros(n, dtype=np.int64)
    elapsed = np.zeros(n, dtype=np.float64)
    ever_fixated = np.zeros((n, n_targets), dtype=bool)
    fixations = np.zeros(n_targets, dtype=np.int64)
    dwell_total = np.zeros(n_targets, dtype=np.float64)
    bought_by: list[np.ndarray] = []
    bought_sku: list[np.ndarray] = []
    rows_scratch = np.arange(n)

    def visit(shoppers: np.ndarray, bay: int) -> None:
        """One station for the shoppers standing at it: k fixations, then a purchase decision."""
        span = store.bay_slice[bay]
        n_t = span.stop - span.start
        if n_t == 0:
            return
        m = shoppers.size
        cat_local = store.cat_of_target[span]
        is_ad_local = store.is_ad[span]
        sku_local = store.sku_of_target[span]

        goal_match = goals[shoppers[:, None], cat_local[None, :]]
        weight = np.where(goal_match, goal_weight[span], other_weight[span])
        total = weight.sum(axis=1)

        k = rng.poisson(lam, m)
        k[total <= 0.0] = 0  # nothing here is worth looking at; the shopper still burns the 4 s
        n_fix = int(k.sum())

        counts = np.zeros((m, n_t), dtype=np.int64)
        dwell_by_target = np.zeros((m, n_t), dtype=np.float64)
        if n_fix:
            probability = np.divide(weight, total[:, None],
                                    out=np.zeros_like(weight), where=total[:, None] > 0.0)
            cumulative = np.cumsum(probability, axis=1)
            cumulative[:, -1] = 1.0  # absorb float error into the last bin
            rows = np.repeat(np.arange(m), k)
            draw = rng.random(n_fix)
            # Inverse CDF, one column at a time to avoid an (n_fix, n_t) temporary. `>=` skips
            # bins of probability 0, which matters at exploration = 0 where the gate shuts every
            # non-goal target. The last bin is 1.0 and draw < 1.0, so it can never be crossed.
            picked = np.zeros(n_fix, dtype=np.int64)
            for column in range(n_t - 1):
                picked += draw >= cumulative[rows, column]
            dwell = rng.lognormal(mu, sigma, n_fix)

            flat = rows * n_t + picked
            counts = np.bincount(flat, minlength=m * n_t).reshape(m, n_t)
            dwell_by_target = np.bincount(flat, weights=dwell,
                                          minlength=m * n_t).reshape(m, n_t)
            fixations[span] += counts.sum(axis=0)
            dwell_total[span] += dwell_by_target.sum(axis=0)
            spent = dwell_by_target.sum(axis=1) / 1000.0
            time_left[shoppers] -= spent
            elapsed[shoppers] += spent
            ever_fixated[shoppers, span.start:span.stop] |= counts > 0

        eligible = (~is_ad_local) & goal_match & (counts > 0)
        if not eligible.any():
            return

        # Rank by fixation count, break ties by total dwell, then by target order (stable sort).
        key = counts + dwell_by_target / (dwell_by_target.max() + 1.0)
        key = np.where(eligible, key, -1.0)
        order = np.argsort(-key, axis=1, kind="stable")
        rows_here = rows_scratch[:m]

        # Which brands did this shopper see advertised at this bay?
        brand_seen = np.zeros((m, len(store.brands)), dtype=bool)
        for column in np.flatnonzero(is_ad_local):
            brand_seen[:, store.brand_of_target[span.start + column]] |= counts[:, column] > 0

        settled = np.zeros(m, dtype=bool)
        for rank in range(min(MAX_PURCHASE_CANDIDATES, n_t)):
            candidate = order[:, rank]
            considers = (key[rows_here, candidate] > 0.0) & ~settled
            gumbel = rng.gumbel(0.0, PURCHASE_GUMBEL_SCALE, m)
            sku = np.maximum(sku_local[candidate], 0)
            ad_pull = 0.2 * ad_receptivity * brand_seen[rows_here, store.sku_brand[sku]]
            utility = sku_utility[sku] + ad_pull + gumbel
            takes = considers & (utility > threshold)
            if takes.any():
                taken = np.flatnonzero(takes)
                buyers, purchased = shoppers[taken], sku[taken]
                bought_by.append(buyers)
                bought_sku.append(purchased)
                goals[buyers, store.sku_category[purchased]] = False
                settled[taken] = True

    for _ in range(n_bays):
        active = (time_left > 0.0) & (goals[:, :n_cat].any(axis=1) | browses_without_goals)
        active &= ~visited.all(axis=1)
        here = np.flatnonzero(active)
        if here.size == 0:
            break

        goal_fraction = (goals[here, :n_cat].astype(np.float64) @ store.goal_slots_bay.T
                         / np.maximum(store.occupied_bay, 1.0))
        score = ((1.0 - exploration) * goal_fraction
                 + exploration * store.mean_saliency_bay
                 + rng.gumbel(size=(here.size, n_bays)))
        score[visited[here]] = -np.inf
        chosen = score.argmax(axis=1)
        visited[here, chosen] = True
        stations[here] += 1

        for bay in range(n_bays):
            at_bay = here[chosen == bay]
            if at_bay.size:
                visit(at_bay, bay)

        time_left[here] -= STATION_MOVE_S
        elapsed[here] += STATION_MOVE_S

    return _aggregate(
        store=store, variant_id=variant_id, persona_id=persona_id, n_runs=n, seed=int(seed),
        fixations=fixations, dwell_total=dwell_total, ever_fixated=ever_fixated,
        bought_by=bought_by, bought_sku=bought_sku, stations=stations, elapsed=elapsed,
        n_sku=n_sku,
    )


def combine(results: Sequence[Mapping], shares: Sequence[float], *,
            persona_id: str = "population") -> dict:
    """Population result = sum of share_of_population x persona result (SPEC M4)."""
    results = list(results)
    shares = [float(s) for s in shares]
    if not results or len(results) != len(shares):
        raise ValueError("combine() needs one share per result")
    if abs(sum(shares) - 1.0) > 1e-9:
        raise ValueError(f"shares must sum to 1, got {sum(shares)}")
    variants = {r["variant_id"] for r in results}
    if len(variants) != 1:
        raise ValueError(f"cannot combine results from different variants: {sorted(variants)}")
    seeds = {r["seed"] for r in results}
    if len(seeds) != 1:
        raise ValueError(f"cannot combine results from different seeds: {sorted(seeds)}")

    variant_id = variants.pop()
    seed = seeds.pop()
    n_runs = sum(int(r["n_runs"]) for r in results)

    def blend(field: str) -> dict[str, float]:
        keys: list[str] = []
        for result in results:
            keys.extend(k for k in result[field] if k not in keys)
        return {k: sum(s * float(r[field].get(k, 0.0)) for s, r in zip(shares, results))
                for k in keys}

    return {
        "sim_run_id": _sim_run_id(variant_id, persona_id, n_runs, seed),
        "variant_id": variant_id,
        "persona_id": persona_id,
        "n_runs": n_runs,
        "seed": seed,
        "fixation_prob": blend("fixation_prob"),
        "dwell_ms_mean": blend("dwell_ms_mean"),
        "ad_slot_attention": blend("ad_slot_attention"),
        "purchase_share": blend("purchase_share"),
        "ad_exposed_purchase_share": blend("ad_exposed_purchase_share"),
        "ad_unexposed_purchase_share": blend("ad_unexposed_purchase_share"),
        "path": {
            "stations_mean": sum(s * float(r["path"]["stations_mean"])
                                 for s, r in zip(shares, results)),
            "duration_s_mean": sum(s * float(r["path"]["duration_s_mean"])
                                   for s, r in zip(shares, results)),
        },
    }


def _aggregate(*, store: Store, variant_id: str, persona_id: str, n_runs: int, seed: int,
               fixations: np.ndarray, dwell_total: np.ndarray, ever_fixated: np.ndarray,
               bought_by: list[np.ndarray], bought_sku: list[np.ndarray],
               stations: np.ndarray, elapsed: np.ndarray, n_sku: int) -> dict:
    total_fixations = int(fixations.sum())
    probability = (fixations / total_fixations if total_fixations
                   else np.zeros_like(fixations, dtype=np.float64))
    mean_dwell = np.divide(dwell_total, fixations, out=np.zeros_like(dwell_total),
                           where=fixations > 0)

    attention = {}
    for ad_slot_id, position in zip(store.ad_slot_ids, store.ad_target_pos):
        # An ad slot with no creative is not a fixation target, so its attention is exactly 0.
        attention[ad_slot_id] = (float(ever_fixated[:, position].mean()) if position >= 0 else 0.0)

    buyers = np.concatenate(bought_by) if bought_by else np.zeros(0, dtype=np.int64)
    purchased = np.concatenate(bought_sku) if bought_sku else np.zeros(0, dtype=np.int64)
    ad_exposed = (ever_fixated[:, store.is_ad].any(axis=1) if store.is_ad.any()
                  else np.zeros(ever_fixated.shape[0], dtype=bool))
    exposed_event = ad_exposed[buyers] if buyers.size else np.zeros(0, dtype=bool)

    return {
        "sim_run_id": _sim_run_id(variant_id, persona_id, n_runs, seed),
        "variant_id": variant_id,
        "persona_id": persona_id,
        "n_runs": n_runs,
        "seed": seed,
        "fixation_prob": {t: float(p) for t, p in zip(store.target_ids, probability)},
        "dwell_ms_mean": {t: float(d) for t, d in zip(store.target_ids, mean_dwell)},
        "ad_slot_attention": attention,
        "purchase_share": _share(purchased, n_sku, store.sku_ids),
        "ad_exposed_purchase_share": _share(purchased[exposed_event], n_sku, store.sku_ids),
        "ad_unexposed_purchase_share": _share(purchased[~exposed_event], n_sku, store.sku_ids),
        "path": {
            "stations_mean": float(stations.mean()),
            "duration_s_mean": float(elapsed.mean()),
        },
    }


def _share(purchased: np.ndarray, n_sku: int, sku_ids: Sequence[str]) -> dict[str, float]:
    counts = np.bincount(purchased, minlength=n_sku).astype(np.float64)
    total = counts.sum()
    if total > 0:
        counts /= total
    return {sku_id: float(c) for sku_id, c in zip(sku_ids, counts)}


def _sim_run_id(variant_id: str, persona_id: str, n_runs: int, seed: int) -> str:
    payload = f"{variant_id}|{persona_id}|{n_runs}|{seed}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
