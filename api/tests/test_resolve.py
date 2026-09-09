"""Pure-function tests for api.app.resolve.resolve(). No HTTP, no DB.

resolve() lives only in api/app/resolve.py (CLAUDE.md). These tests load the
real seed planogram and variants A/B/C from data/ and check resolve() against
the facts recorded in the S3 task brief, plus the schema and the two patch
ops (move_sku, set_ad_creative) not exercised by A/B/C (swap_texture,
set_price) and the unknown-reference error path.
"""
import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft7Validator

from api.app.resolve import PatchError, resolve

ROOT = Path(__file__).resolve().parents[2]
PLANOGRAM_PATH = ROOT / "data" / "planograms" / "demo_aisle.json"
VARIANTS_DIR = ROOT / "data" / "variants"
SCHEMA_PATH = ROOT / "schemas" / "planogram.schema.json"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def base_planogram() -> dict:
    return load_json(PLANOGRAM_PATH)


def variant(name: str) -> dict:
    return load_json(VARIANTS_DIR / f"{name}.json")


def planogram_validator() -> Draft7Validator:
    return Draft7Validator(load_json(SCHEMA_PATH))


def find_slot(pg: dict, slot_id: str) -> dict:
    for bay in pg["bays"]:
        for shelf in bay["shelves"]:
            for slot in shelf["slots"]:
                if slot["slot_id"] == slot_id:
                    return slot
    raise KeyError(slot_id)


def find_ad_slot(pg: dict, ad_slot_id: str) -> dict:
    for bay in pg["bays"]:
        for ad in bay["ad_slots"]:
            if ad["ad_slot_id"] == ad_slot_id:
                return ad
    raise KeyError(ad_slot_id)


def find_sku(pg: dict, sku_id: str) -> dict:
    for sku in pg["skus"]:
        if sku["sku_id"] == sku_id:
            return sku
    raise KeyError(sku_id)


def test_variant_a_resolves_to_base_unchanged_and_base_not_mutated():
    base = base_planogram()
    reference = copy.deepcopy(base)

    result = resolve(base, variant("A"))

    assert result == reference
    assert base == reference  # resolve() must not mutate its `base` argument


def test_variant_b_moves_sku_008_and_empties_source_slot():
    base = base_planogram()

    result = resolve(base, variant("B"))

    dest = find_slot(result, "B1S3P2")
    assert dest["sku_id"] == "SKU_008"
    assert dest["facings"] == 3

    source = find_slot(result, "B1S5P1")
    assert source["sku_id"] is None
    assert source["facings"] == 0


def test_variant_c_clears_endcap_and_sets_talker():
    base = base_planogram()

    result = resolve(base, variant("C"))

    endcap = find_ad_slot(result, "B3_ENDCAP")
    assert endcap["creative_id"] is None

    talker = find_ad_slot(result, "B1_TALKER")
    assert talker["creative_id"] == "AD_1"


@pytest.mark.parametrize("variant_name", ["A", "B", "C"])
def test_resolved_output_validates_against_planogram_schema(variant_name):
    base = base_planogram()
    result = resolve(base, variant(variant_name))

    validator = planogram_validator()
    errors = sorted(validator.iter_errors(result), key=str)
    assert errors == [], "\n".join(e.message for e in errors)


def test_move_sku_into_occupied_slot_swaps_the_two_skus():
    base = base_planogram()
    # SKU_008 lives at B1S5P1 (facings 3). B1S1P1 holds SKU_001 (facings 2).
    # Both are occupied, so this must swap rather than empty either slot.
    v = {
        "variant_id": "test_swap",
        "base_planogram_id": "demo_aisle",
        "name": "swap test",
        "patches": [{"op": "move_sku", "sku_id": "SKU_008", "to_slot_id": "B1S1P1"}],
    }

    result = resolve(base, v)

    dest = find_slot(result, "B1S1P1")
    source = find_slot(result, "B1S5P1")
    assert dest["sku_id"] == "SKU_008"
    assert dest["facings"] == 3
    assert source["sku_id"] == "SKU_001"
    assert source["facings"] == 2
    # neither slot became empty
    assert dest["sku_id"] is not None
    assert source["sku_id"] is not None
    # positional fields belong to the shelf position and never move
    assert dest["x_m"] == 0.05 and dest["width_m"] == 0.5 and dest["height_m"] == 0.22
    assert source["x_m"] == 0.05 and source["width_m"] == 0.5 and source["height_m"] == 0.22


def test_swap_texture_changes_only_texture_url():
    base = base_planogram()
    v = {
        "variant_id": "test_texture",
        "base_planogram_id": "demo_aisle",
        "name": "texture test",
        "patches": [
            {"op": "swap_texture", "sku_id": "SKU_003", "texture_url": "/textures/sku_003_v2.png"}
        ],
    }

    result = resolve(base, v)

    before = find_sku(base, "SKU_003")
    after = find_sku(result, "SKU_003")
    assert after["texture_url"] == "/textures/sku_003_v2.png"
    for field in ("sku_id", "name", "brand", "category", "price", "promo", "color_lab"):
        assert after[field] == before[field]


def test_set_price_changes_only_price_and_promo():
    base = base_planogram()
    v = {
        "variant_id": "test_price",
        "base_planogram_id": "demo_aisle",
        "name": "price test",
        "patches": [{"op": "set_price", "sku_id": "SKU_004", "price": 19.5, "promo": True}],
    }

    result = resolve(base, v)

    before = find_sku(base, "SKU_004")
    after = find_sku(result, "SKU_004")
    assert after["price"] == 19.5
    assert after["promo"] is True
    for field in ("sku_id", "name", "brand", "category", "texture_url", "color_lab"):
        assert after[field] == before[field]


def test_set_price_without_promo_leaves_promo_untouched():
    base = base_planogram()
    v = {
        "variant_id": "test_price_no_promo",
        "base_planogram_id": "demo_aisle",
        "name": "price test no promo",
        "patches": [{"op": "set_price", "sku_id": "SKU_002", "price": 12.0}],
    }

    result = resolve(base, v)

    before = find_sku(base, "SKU_002")
    after = find_sku(result, "SKU_002")
    assert after["price"] == 12.0
    assert after["promo"] == before["promo"]


@pytest.mark.parametrize(
    "patch",
    [
        {"op": "move_sku", "sku_id": "SKU_999", "to_slot_id": "B1S1P1"},
        {"op": "move_sku", "sku_id": "SKU_008", "to_slot_id": "NOPE"},
        {"op": "set_ad_creative", "ad_slot_id": "NOPE", "creative_id": "AD_1"},
        {"op": "set_ad_creative", "ad_slot_id": "B1_TALKER", "creative_id": "AD_999"},
        {"op": "swap_texture", "sku_id": "SKU_999", "texture_url": "/x.png"},
        {"op": "set_price", "sku_id": "SKU_999", "price": 1.0},
    ],
    ids=[
        "move_sku-unknown-sku",
        "move_sku-unknown-slot",
        "set_ad_creative-unknown-ad-slot",
        "set_ad_creative-unknown-creative",
        "swap_texture-unknown-sku",
        "set_price-unknown-sku",
    ],
)
def test_patch_with_unknown_reference_raises(patch):
    base = base_planogram()
    v = {
        "variant_id": "test_bad",
        "base_planogram_id": "demo_aisle",
        "name": "bad patch",
        "patches": [patch],
    }

    with pytest.raises(ValueError):
        resolve(base, v)


# ---------------------------------------------------------------------------
# add_ad_slot
#
# The op exists because a shelf read from video has nowhere to put an ad.
# `vision/planogram.py` emits `ad_slots: []` on purpose - it detects no signage
# and refuses to invent the one variable the whole experiment manipulates - and
# every other patch op only edits something already there. `set_ad_creative`
# needs an `ad_slot_id` that exists. So a video-read bay could be shopped and
# could produce purchases once labelled, and could never produce an ad lift,
# because there was no fixture to expose anybody to.
#
# The bay is derived from `attached_to` rather than passed alongside it. Two
# fields naming a location is two fields that can disagree, and a slot whose
# `attached_to` shelf sits in a different bay from its `ad_slots` array would
# put a talker on one bay and score it against another - `sim/saliency.py`
# reads `attached_to` for adjacency and the bay array for which bay carries it.


def test_add_ad_slot_appends_to_the_bay_that_owns_the_shelf():
    base = base_planogram()
    v = {
        "variant_id": "test_add_ad",
        "base_planogram_id": "demo_aisle",
        "name": "add an ad slot",
        "patches": [
            {
                "op": "add_ad_slot",
                "ad_slot_id": "B2_TALKER_2",
                "type": "shelf_talker",
                "attached_to": "B2S3",
                "x_m": 0.15,
                "width_m": 0.4,
            }
        ],
    }

    result = resolve(base, v)

    added = find_ad_slot(result, "B2_TALKER_2")
    assert added["attached_to"] == "B2S3"
    assert added["creative_id"] is None, "a new fixture is empty until something books it"
    owning = next(b for b in result["bays"] if b["bay_id"] == "B2")
    assert any(a["ad_slot_id"] == "B2_TALKER_2" for a in owning["ad_slots"])


def test_add_ad_slot_can_attach_to_a_bay_rather_than_a_shelf():
    """`attached_to` is a shelf_id or a bay_id - `geometry.ts` says so, and an
    endcap header hangs off the bay, not off one of its shelves."""
    base = base_planogram()
    v = {
        "variant_id": "test_add_bay",
        "base_planogram_id": "demo_aisle",
        "name": "bay-level fixture",
        "patches": [
            {
                "op": "add_ad_slot",
                "ad_slot_id": "B1_HEADER",
                "type": "endcap_header",
                "attached_to": "B1",
                "x_m": 0.0,
                "width_m": 1.0,
            }
        ],
    }

    result = resolve(base, v)

    owning = next(b for b in result["bays"] if b["bay_id"] == "B1")
    assert any(a["ad_slot_id"] == "B1_HEADER" for a in owning["ad_slots"])


def test_add_ad_slot_then_set_ad_creative_books_it():
    """The two ops compose, which is why `add_ad_slot` does not take a creative
    of its own. One op creates the fixture, the existing one books it - and
    `set_ad_creative` already refuses a creative the planogram does not carry."""
    base = base_planogram()
    v = {
        "variant_id": "test_add_book",
        "base_planogram_id": "demo_aisle",
        "name": "add then book",
        "patches": [
            {
                "op": "add_ad_slot",
                "ad_slot_id": "B2_TALKER_2",
                "type": "shelf_talker",
                "attached_to": "B2S3",
                "x_m": 0.15,
                "width_m": 0.4,
            },
            {"op": "set_ad_creative", "ad_slot_id": "B2_TALKER_2", "creative_id": "AD_1"},
        ],
    }

    result = resolve(base, v)

    assert find_ad_slot(result, "B2_TALKER_2")["creative_id"] == "AD_1"


def test_add_ad_slot_does_not_mutate_the_base():
    base = base_planogram()
    before = sum(len(bay["ad_slots"]) for bay in base["bays"])
    v = {
        "variant_id": "test_add_pure",
        "base_planogram_id": "demo_aisle",
        "name": "purity",
        "patches": [
            {
                "op": "add_ad_slot",
                "ad_slot_id": "B3_EXTRA",
                "type": "floor_decal",
                "attached_to": "B3",
                "x_m": 0.2,
                "width_m": 0.3,
            }
        ],
    }

    resolve(base, v)

    assert sum(len(bay["ad_slots"]) for bay in base["bays"]) == before


def test_add_ad_slot_output_validates_against_the_planogram_schema():
    base = base_planogram()
    v = {
        "variant_id": "test_add_valid",
        "base_planogram_id": "demo_aisle",
        "name": "schema",
        "patches": [
            {
                "op": "add_ad_slot",
                "ad_slot_id": "B1_TALKER_2",
                "type": "screen",
                "attached_to": "B1S2",
                "x_m": 0.05,
                "width_m": 0.5,
            }
        ],
    }

    errors = sorted(planogram_validator().iter_errors(resolve(base, v)), key=str)

    assert errors == []


@pytest.mark.parametrize(
    "patch,message",
    [
        (
            {
                "op": "add_ad_slot",
                "ad_slot_id": "NOWHERE",
                "type": "shelf_talker",
                "attached_to": "B9S9",
                "x_m": 0.1,
                "width_m": 0.2,
            },
            "attached_to",
        ),
        (
            {
                "op": "add_ad_slot",
                "ad_slot_id": "B1_TALKER",
                "type": "shelf_talker",
                "attached_to": "B1S1",
                "x_m": 0.1,
                "width_m": 0.2,
            },
            "already",
        ),
    ],
    ids=["unknown-attachment", "duplicate-ad-slot-id"],
)
def test_add_ad_slot_refuses_a_fixture_it_cannot_place(patch, message):
    """An unplaceable fixture must not be silently dropped or silently
    duplicated. A second `B1_TALKER` would make `_index_ad_slots` ambiguous and
    `set_ad_creative` would then book whichever one it happened to index."""
    base = base_planogram()
    v = {
        "variant_id": "bad",
        "base_planogram_id": "demo_aisle",
        "name": "bad",
        "patches": [patch],
    }

    with pytest.raises(PatchError, match=message):
        resolve(base, v)
