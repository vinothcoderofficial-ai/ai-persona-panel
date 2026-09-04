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

from api.app.resolve import resolve

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
