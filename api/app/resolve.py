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
        else:
            raise PatchError(f"unknown patch op {op!r}")

    return planogram
