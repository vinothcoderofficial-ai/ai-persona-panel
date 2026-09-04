"""Deterministic bottom-up saliency for a resolved planogram (SPEC M3).

This layer answers "what would anyone notice", with no persona in it. The persona policy
reweights `p_saliency` by goals and brand affinity in `sim/simulator.py`; an LLM never invents a
gaze pattern.

A bay's fixation targets are its occupied slots (`sku_id` is not null) plus its ad slots that
carry a creative. Empty slots and creative-less ad slots are never targets, but empty slots still
occupy shelf space: they count toward the bay's max facings and max slot area, and they break
left/right adjacency for the colour-contrast term.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping

import numpy as np

# The blend weights are the only tunable constants (SPEC M3).
DEFAULT_WEIGHTS: dict[str, float] = {
    "level": 0.30,
    "center": 0.15,
    "facings": 0.20,
    "color": 0.15,
    "ad": 0.10,
    "size": 0.10,
}

LEVEL_SCORES: dict[str, float] = {
    "eye": 1.0,
    "above_eye": 0.75,
    "below_eye": 0.7,
    "top": 0.5,
    "bottom": 0.35,
}

AD_SLOT_RAW: dict[str, float] = {
    "endcap_header": 0.6,
    "shelf_talker": 0.4,
    "floor_decal": 0.3,
    "screen": 0.7,
}

SOFTMAX_TEMPERATURE = 0.15

CENTER_PENALTY = 0.4


@dataclass(frozen=True)
class BaySaliency:
    """Saliency for one bay, in target order: occupied slots (shelf order), then ad slots."""

    bay_id: str
    target_ids: tuple[str, ...]
    is_ad: np.ndarray
    saliency_raw: np.ndarray
    p_saliency: np.ndarray

    def raw_by_id(self) -> dict[str, float]:
        return {t: float(v) for t, v in zip(self.target_ids, self.saliency_raw)}

    def p_by_id(self) -> dict[str, float]:
        return {t: float(v) for t, v in zip(self.target_ids, self.p_saliency)}

    @property
    def mean_raw(self) -> float:
        """Mean *raw* saliency over the bay's targets. p_saliency would just be 1/n_targets."""
        return float(self.saliency_raw.mean()) if self.saliency_raw.size else 0.0


def compute_saliency(planogram: Mapping, weights: Mapping[str, float] | None = None,
                     ) -> dict[str, BaySaliency]:
    """Saliency for every bay of a resolved planogram, keyed by bay_id in planogram order."""
    blend = DEFAULT_WEIGHTS if weights is None else {**DEFAULT_WEIGHTS, **weights}
    skus = {s["sku_id"]: s for s in planogram["skus"]}
    creatives = {c["creative_id"]: c for c in planogram.get("creatives", [])}
    return {bay["bay_id"]: bay_saliency(bay, skus, creatives, blend) for bay in planogram["bays"]}


def bay_saliency(bay: Mapping, skus: Mapping[str, Mapping], creatives: Mapping[str, Mapping],
                 weights: Mapping[str, float] | None = None) -> BaySaliency:
    """Saliency for a single bay. `skus` and `creatives` are id -> record maps."""
    blend = DEFAULT_WEIGHTS if weights is None else {**DEFAULT_WEIGHTS, **weights}
    half_width = bay["width_m"] / 2.0

    ad_carriers = {ad["attached_to"] for ad in bay["ad_slots"]
                   if ad["creative_id"] is not None and ad["creative_id"] in creatives}
    bay_has_ad = bay["bay_id"] in ad_carriers

    all_slots = [sl for shelf in bay["shelves"] for sl in shelf["slots"]]
    max_facings = max((sl["facings"] for sl in all_slots), default=0)
    facings_denom = math.log1p(max_facings) if max_facings > 0 else 0.0
    max_area = max((sl["width_m"] * sl["height_m"] for sl in all_slots), default=0.0)

    colour = _normalised_colour_contrast(bay, skus)

    target_ids: list[str] = []
    is_ad: list[bool] = []
    raw: list[float] = []

    for shelf in bay["shelves"]:
        f_level = LEVEL_SCORES[shelf["level"]]
        f_ad = 1.0 if (bay_has_ad or shelf["shelf_id"] in ad_carriers) else 0.0
        for slot in shelf["slots"]:
            if slot["sku_id"] is None:
                continue  # empty slots hold shelf space but are never fixation targets
            centre = slot["x_m"] + slot["width_m"] / 2.0
            f_center = 1.0 - CENTER_PENALTY * abs(centre - half_width) / half_width
            f_facings = math.log1p(slot["facings"]) / facings_denom if facings_denom else 0.0
            f_color = colour[slot["slot_id"]]
            f_size = (slot["width_m"] * slot["height_m"]) / max_area if max_area else 0.0
            target_ids.append(slot["slot_id"])
            is_ad.append(False)
            raw.append(
                blend["level"] * f_level
                + blend["center"] * f_center
                + blend["facings"] * f_facings
                + blend["color"] * f_color
                + blend["ad"] * f_ad
                + blend["size"] * f_size
            )

    for ad in bay["ad_slots"]:
        if ad["creative_id"] is None or ad["creative_id"] not in creatives:
            continue  # an ad slot with no creative shows nothing, so nobody looks at it
        target_ids.append(ad["ad_slot_id"])
        is_ad.append(True)
        raw.append(AD_SLOT_RAW[ad["type"]])

    raw_arr = np.asarray(raw, dtype=np.float64)
    return BaySaliency(
        bay_id=bay["bay_id"],
        target_ids=tuple(target_ids),
        is_ad=np.asarray(is_ad, dtype=bool),
        saliency_raw=raw_arr,
        p_saliency=_softmax(raw_arr, SOFTMAX_TEMPERATURE),
    )


def _normalised_colour_contrast(bay: Mapping, skus: Mapping[str, Mapping]) -> dict[str, float]:
    """Mean CIE76 dE to the nearest occupied neighbours on the same shelf, min-max within the bay.

    Empty slots break adjacency, so a slot whose shelf holds no other occupied slot scores 0.0
    before normalisation. When every contrast in the bay is identical the min-max range is zero
    and every normalised value is 0.0 -- softmax is not shift invariant here, because ad slots
    carry fixed raw scores.
    """
    deltas: dict[str, float] = {}
    for shelf in bay["shelves"]:
        occupied = sorted((sl for sl in shelf["slots"] if sl["sku_id"] is not None),
                          key=lambda sl: sl["x_m"])
        for i, slot in enumerate(occupied):
            lab = skus[slot["sku_id"]]["color_lab"]
            neighbours = []
            if i > 0:
                neighbours.append(skus[occupied[i - 1]["sku_id"]]["color_lab"])
            if i + 1 < len(occupied):
                neighbours.append(skus[occupied[i + 1]["sku_id"]]["color_lab"])
            deltas[slot["slot_id"]] = (
                sum(math.dist(lab, other) for other in neighbours) / len(neighbours)
                if neighbours else 0.0
            )

    if not deltas:
        return deltas
    lo, hi = min(deltas.values()), max(deltas.values())
    span = hi - lo
    if span <= 0.0:
        return {k: 0.0 for k in deltas}
    return {k: (v - lo) / span for k, v in deltas.items()}


def _softmax(raw: np.ndarray, temperature: float) -> np.ndarray:
    if raw.size == 0:
        return raw.copy()
    scaled = raw / temperature
    shifted = np.exp(scaled - scaled.max())
    return shifted / shifted.sum()
