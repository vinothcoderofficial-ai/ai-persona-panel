"""resolve() applies a variant's patches to a base planogram.

This is the ONLY implementation of resolve() in the whole project (CLAUDE.md:
"resolve() lives only in api/app/resolve.py"). The web app never computes
this client-side - it always calls GET /variants/{id}/resolved.
"""
import copy
from typing import Any, Dict, Set


class PatchError(ValueError):
    """A variant patch referenced something that does not exist in the base
    planogram: an unknown sku_id, slot_id, ad_slot_id or creative_id."""


def _index_slots(planogram: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    slots: Dict[str, Dict[str, Any]] = {}
    for bay in planogram["bays"]:
        for shelf in bay["shelves"]:
            for slot in shelf["slots"]:
                slots[slot["slot_id"]] = slot
    return slots


def _index_ad_slots(planogram: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    ad_slots: Dict[str, Dict[str, Any]] = {}
    for bay in planogram["bays"]:
        for ad_slot in bay["ad_slots"]:
            ad_slots[ad_slot["ad_slot_id"]] = ad_slot
    return ad_slots


def _apply_move_sku(patch: Dict[str, Any], slots: Dict[str, Dict[str, Any]]) -> None:
    sku_id = patch["sku_id"]
    to_slot_id = patch["to_slot_id"]

    if to_slot_id not in slots:
        raise PatchError(f"move_sku: unknown to_slot_id {to_slot_id!r}")

    source_slot = next((s for s in slots.values() if s["sku_id"] == sku_id), None)
    if source_slot is None:
        raise PatchError(f"move_sku: unknown sku_id {sku_id!r} (not placed in any slot)")

    dest_slot = slots[to_slot_id]
    if source_slot["slot_id"] == dest_slot["slot_id"]:
        return  # moving a sku onto its own slot is a no-op

    if dest_slot["sku_id"] is None:
        # destination empty: the sku and its facings move; the source becomes empty.
        dest_slot["sku_id"] = source_slot["sku_id"]
        dest_slot["facings"] = source_slot["facings"]
        source_slot["sku_id"] = None
        source_slot["facings"] = 0
    else:
        # destination occupied: swap the two slots' sku_id and facings.
        source_slot["sku_id"], dest_slot["sku_id"] = dest_slot["sku_id"], source_slot["sku_id"]
        source_slot["facings"], dest_slot["facings"] = dest_slot["facings"], source_slot["facings"]


def _apply_set_ad_creative(
    patch: Dict[str, Any],
    ad_slots: Dict[str, Dict[str, Any]],
    creative_ids: Set[str],
) -> None:
    ad_slot_id = patch["ad_slot_id"]
    creative_id = patch["creative_id"]

    if ad_slot_id not in ad_slots:
        raise PatchError(f"set_ad_creative: unknown ad_slot_id {ad_slot_id!r}")
    if creative_id is not None and creative_id not in creative_ids:
        raise PatchError(f"set_ad_creative: unknown creative_id {creative_id!r}")

    ad_slots[ad_slot_id]["creative_id"] = creative_id


def _apply_swap_texture(patch: Dict[str, Any], skus: Dict[str, Dict[str, Any]]) -> None:
    sku_id = patch["sku_id"]
    if sku_id not in skus:
        raise PatchError(f"swap_texture: unknown sku_id {sku_id!r}")
    skus[sku_id]["texture_url"] = patch["texture_url"]


def _apply_set_price(patch: Dict[str, Any], skus: Dict[str, Dict[str, Any]]) -> None:
    sku_id = patch["sku_id"]
    if sku_id not in skus:
        raise PatchError(f"set_price: unknown sku_id {sku_id!r}")
    skus[sku_id]["price"] = patch["price"]
    if "promo" in patch:
        skus[sku_id]["promo"] = patch["promo"]


def _apply_add_ad_slot(
    patch: Dict[str, Any],
    planogram: Dict[str, Any],
    ad_slots: Dict[str, Dict[str, Any]],
) -> None:
    """Hang a new, empty ad fixture on a shelf or a bay.

    Every other op edits something the planogram already carries. This one adds,
    and it exists because a shelf read from video carries no fixtures at all:
    `vision/planogram.py` emits `ad_slots: []` on purpose, since it detects no
    signage and will not invent the one variable the experiment manipulates. So
    a video-read bay could be shopped, and could sell things once an operator
    labelled it, and could never produce an ad lift - there was nothing for a
    shopper to be exposed to.

    **The bay is derived from `attached_to`, never passed alongside it.** Two
    fields naming a location are two fields that can disagree, and the two
    consumers read different ones: `sim/saliency.py` scores adjacency from
    `attached_to`, while the bay's own `ad_slots` array decides which bay
    carries the fixture. A patch that set them inconsistently would put a
    talker on one bay and score it against another, and nothing downstream
    could see it.

    **The fixture is created empty.** `set_ad_creative` books it, already exists
    and already refuses a creative the planogram does not carry, so splitting
    the two keeps one validation path rather than two. A variant that wants a
    booked fixture writes both patches, in that order, which also reads as what
    it is: install the holder, then put a poster in it.
    """
    ad_slot_id = patch["ad_slot_id"]
    if ad_slot_id in ad_slots:
        raise PatchError(
            f"add_ad_slot: ad_slot_id {ad_slot_id!r} already exists. Two fixtures "
            "with one id would make the ad-slot index ambiguous, and "
            "set_ad_creative would book whichever it happened to find."
        )

    attached_to = patch["attached_to"]
    owner = None
    for bay in planogram["bays"]:
        if bay["bay_id"] == attached_to or any(
            shelf["shelf_id"] == attached_to for shelf in bay["shelves"]
        ):
            owner = bay
            break

    if owner is None:
        raise PatchError(
            f"add_ad_slot: unknown attached_to {attached_to!r} (no bay or shelf "
            "carries that id)"
        )

    fixture = {
        "ad_slot_id": ad_slot_id,
        "type": patch["type"],
        "attached_to": attached_to,
        "x_m": patch["x_m"],
        "width_m": patch["width_m"],
        "creative_id": None,
    }
    owner["ad_slots"].append(fixture)
    ad_slots[ad_slot_id] = fixture


def resolve(base: Dict[str, Any], variant: Dict[str, Any]) -> Dict[str, Any]:
    """Apply variant["patches"] to base, in list order, and return a full
    resolved planogram.

    Pure function: `base` is deep-copied and never mutated. The returned
    planogram keeps base's planogram_id, so a variant with no patches
    resolves deep-equal to `base`.
    """
    planogram = copy.deepcopy(base)

    skus = {sku["sku_id"]: sku for sku in planogram["skus"]}
    slots = _index_slots(planogram)
    ad_slots = _index_ad_slots(planogram)
    creative_ids = {c["creative_id"] for c in planogram["creatives"]}

    for patch in variant.get("patches", []):
        op = patch["op"]
        if op == "move_sku":
            _apply_move_sku(patch, slots)
        elif op == "set_ad_creative":
            _apply_set_ad_creative(patch, ad_slots, creative_ids)
        elif op == "swap_texture":
            _apply_swap_texture(patch, skus)
        elif op == "set_price":
            _apply_set_price(patch, skus)
        elif op == "add_ad_slot":
            # Given the planogram itself, not just the index: this op appends to
            # a bay, so it has to find the bay that owns `attached_to`. It keeps
            # `ad_slots` current so a later `set_ad_creative` in the same variant
            # can book what it just installed.
            _apply_add_ad_slot(patch, planogram, ad_slots)
        else:
            raise PatchError(f"unknown patch op {op!r}")

    return planogram
