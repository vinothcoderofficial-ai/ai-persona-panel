"""Tests for the variant referential-integrity lint in scripts/validate_data.py.

The lint exists to catch, over the committed files and before anything is
served, exactly the references `api/app/resolve.py` would refuse at resolve
time. Two implementations of the same rule drift, and this one had drifted in
both directions at once:

* **False failures.** The lint built its set of valid `ad_slot_id`s from the
  base planogram alone and had no `add_ad_slot` branch at all. So the natural
  way to write a new booked fixture -- `add_ad_slot` to install the holder,
  then `set_ad_creative` to put a poster in it, which is the composition
  `_apply_add_ad_slot`'s docstring tells you to write -- was reported as
  referencing an unknown ad slot. A perfectly resolvable document failed the
  build.

* **False passes, which are worse.** `resolve()` raises `PatchError` for an
  `add_ad_slot` that reuses an existing id and for one whose `attached_to`
  names no bay or shelf. The lint waved both through, so a variant that could
  never be resolved could still be committed with `make validate` green. The
  whole point of a pre-flight lint is that the file never gets that far.

The rule these tests pin is therefore not "check ids against the planogram" but
**check ids against the planogram as the patches have left it, in patch order**,
because that is what `resolve()` does. `test_the_lint_agrees_with_resolve_on_*`
states that equivalence directly, so the next op added to the schema has one
obvious place to be mirrored.
"""
import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from api.app.resolve import PatchError, resolve  # noqa: E402
from scripts import validate_data  # noqa: E402


@pytest.fixture
def planogram():
    """A two-bay aisle small enough to read, shaped like the committed one.

    One bay already carries an ad slot (`B1_TALKER`) so the "id already taken"
    case has something real to collide with; the other carries none, which is
    the case `add_ad_slot` was written for -- a bay read from video, where
    `vision/planogram.py` emits `ad_slots: []` on purpose.
    """
    return {
        "planogram_id": "test_aisle",
        "skus": [
            {"sku_id": "SKU_1", "price": 1.0},
            {"sku_id": "SKU_2", "price": 2.0},
            # Catalogued but on no shelf: a sku you may reprice and may not move.
            {"sku_id": "SKU_STOCKROOM", "price": 3.0},
        ],
        "creatives": [{"creative_id": "AD_1"}, {"creative_id": "AD_2"}],
        "bays": [
            {
                "bay_id": "B1",
                "shelves": [
                    {
                        "shelf_id": "B1S1",
                        "slots": [
                            {"slot_id": "B1S1P1", "sku_id": "SKU_1", "facings": 2},
                            {"slot_id": "B1S1P2", "sku_id": None, "facings": 0},
                        ],
                    }
                ],
                "ad_slots": [
                    {
                        "ad_slot_id": "B1_TALKER",
                        "type": "shelf_talker",
                        "attached_to": "B1S1",
                        "x_m": 0.1,
                        "width_m": 0.3,
                        "creative_id": None,
                    }
                ],
            },
            {
                "bay_id": "B2",
                "shelves": [
                    {
                        "shelf_id": "B2S1",
                        "slots": [
                            {"slot_id": "B2S1P1", "sku_id": "SKU_2", "facings": 1}
                        ],
                    }
                ],
                "ad_slots": [],
            },
        ],
    }


def variant(*patches):
    return {
        "variant_id": "T",
        "base_planogram_id": "test_aisle",
        "name": "test variant",
        "patches": list(patches),
    }


def errors(planogram, *patches):
    return validate_data.variant_patch_errors(planogram, variant(*patches))


INSTALL_TALKER = {
    "op": "add_ad_slot",
    "ad_slot_id": "B2_TALKER",
    "type": "shelf_talker",
    "attached_to": "B2S1",
    "x_m": 0.2,
    "width_m": 0.3,
}


# --- the false failure ------------------------------------------------------


def test_a_creative_can_be_booked_onto_a_slot_an_earlier_patch_installed():
    """This is the composition the schema was extended to allow.

    Installing a fixture and booking it are two patches by design -- one
    validation path for creatives rather than two -- so the lint has to carry
    the created id forward or it condemns the only way to write the pair.
    """
    pg = {
        "planogram_id": "p",
        "skus": [],
        "creatives": [{"creative_id": "AD_1"}],
        "bays": [{"bay_id": "B2", "shelves": [{"shelf_id": "B2S1", "slots": []}],
                  "ad_slots": []}],
    }
    assert errors(
        pg,
        INSTALL_TALKER,
        {"op": "set_ad_creative", "ad_slot_id": "B2_TALKER", "creative_id": "AD_1"},
    ) == []


def test_an_installed_slot_can_be_booked_then_cleared(planogram):
    """`creative_id: null` is legal on a created slot as much as a base one."""
    assert errors(
        planogram,
        INSTALL_TALKER,
        {"op": "set_ad_creative", "ad_slot_id": "B2_TALKER", "creative_id": "AD_2"},
        {"op": "set_ad_creative", "ad_slot_id": "B2_TALKER", "creative_id": None},
    ) == []


def test_the_committed_variants_pass_the_lint(planogram):
    """Whatever else changes, data/variants/*.json must stay clean."""
    base = json.loads(
        (ROOT / "data" / "planograms" / "demo_aisle.json").read_text()
    )
    for path in sorted((ROOT / "data" / "variants").glob("*.json")):
        found = validate_data.variant_patch_errors(
            base, json.loads(path.read_text())
        )
        assert found == [], f"{path.name}: {found}"


# --- patch order, because resolve() applies in patch order ------------------


def test_booking_a_slot_before_the_patch_that_installs_it_is_caught(planogram):
    """resolve() would raise here; the ordering is not cosmetic."""
    found = errors(
        planogram,
        {"op": "set_ad_creative", "ad_slot_id": "B2_TALKER", "creative_id": "AD_1"},
        INSTALL_TALKER,
    )
    assert len(found) == 1
    assert "B2_TALKER" in found[0]


def test_booking_an_ad_slot_nothing_created_is_still_caught(planogram):
    """The original check must survive the fix -- that was its whole job."""
    found = errors(
        planogram,
        {"op": "set_ad_creative", "ad_slot_id": "B9_TALKER", "creative_id": "AD_1"},
    )
    assert len(found) == 1
    assert "B9_TALKER" in found[0]


def test_booking_an_unknown_creative_onto_an_installed_slot_is_caught(planogram):
    """Creating the holder does not create posters to put in it."""
    found = errors(
        planogram,
        INSTALL_TALKER,
        {"op": "set_ad_creative", "ad_slot_id": "B2_TALKER", "creative_id": "AD_9"},
    )
    assert len(found) == 1
    assert "AD_9" in found[0]


# --- the mirror problem: what add_ad_slot itself must be checked for --------


def test_installing_an_ad_slot_over_an_existing_id_is_caught(planogram):
    found = errors(
        planogram,
        {**INSTALL_TALKER, "ad_slot_id": "B1_TALKER"},
    )
    assert len(found) == 1
    assert "B1_TALKER" in found[0]
    assert "already" in found[0]


def test_installing_the_same_new_ad_slot_twice_is_caught(planogram):
    """Two patches in one variant collide exactly as a patch and the base do."""
    found = errors(planogram, INSTALL_TALKER, INSTALL_TALKER)
    assert len(found) == 1
    assert "B2_TALKER" in found[0]
    assert "already" in found[0]


def test_installing_an_ad_slot_on_a_shelf_that_does_not_exist_is_caught(planogram):
    found = errors(planogram, {**INSTALL_TALKER, "attached_to": "B9S9"})
    assert len(found) == 1
    assert "B9S9" in found[0]


def test_an_ad_slot_may_attach_to_a_bay_as_well_as_a_shelf(planogram):
    """An endcap header hangs off the bay; only shelf talkers need a shelf."""
    assert errors(
        planogram,
        {**INSTALL_TALKER, "ad_slot_id": "B2_HEADER", "type": "endcap_header",
         "attached_to": "B2"},
    ) == []


def test_an_ad_slot_may_not_attach_to_a_slot_id(planogram):
    """`attached_to` is a bay or a shelf. A slot id looks plausible and is not
    one -- resolve() finds no owner bay and raises."""
    found = errors(planogram, {**INSTALL_TALKER, "attached_to": "B2S1P1"})
    assert len(found) == 1
    assert "B2S1P1" in found[0]


def test_a_bad_attached_to_does_not_also_blame_the_booking_that_follows(planogram):
    """One fault, one line.

    A cascade here would name the `set_ad_creative` as the broken patch when
    the broken patch is the install above it, and whoever reads the build log
    would go and edit the wrong line.
    """
    found = errors(
        planogram,
        {**INSTALL_TALKER, "attached_to": "B9S9"},
        {"op": "set_ad_creative", "ad_slot_id": "B2_TALKER", "creative_id": "AD_1"},
    )
    assert len(found) == 1
    assert "B9S9" in found[0]


# --- the ops that were already linted --------------------------------------


def test_moving_an_unplaced_sku_is_caught(planogram):
    found = errors(
        planogram, {"op": "move_sku", "sku_id": "SKU_9", "to_slot_id": "B1S1P2"}
    )
    assert len(found) == 1
    assert "SKU_9" in found[0]


def test_moving_into_a_slot_that_does_not_exist_is_caught(planogram):
    """No op creates slots, so the base planogram is the whole universe here."""
    found = errors(
        planogram, {"op": "move_sku", "sku_id": "SKU_1", "to_slot_id": "B9S9P9"}
    )
    assert len(found) == 1
    assert "B9S9P9" in found[0]


def test_moving_a_sku_the_catalogue_lists_but_no_shelf_carries_is_caught(planogram):
    """`move_sku` moves a facing, not a catalogue entry.

    resolve() looks for the slot that currently holds the sku and refuses when
    there is none, so checking `sku_id` against `planogram["skus"]` -- which is
    what the lint used to do -- passes a patch that cannot be applied.
    """
    found = errors(
        planogram,
        {"op": "move_sku", "sku_id": "SKU_STOCKROOM", "to_slot_id": "B1S1P2"},
    )
    assert len(found) == 1
    assert "SKU_STOCKROOM" in found[0]


def test_repricing_a_sku_no_shelf_carries_is_allowed(planogram):
    """The mirror of the above: `set_price` edits the catalogue, which does
    carry it. The two ops read two different sets and always did."""
    assert errors(
        planogram, {"op": "set_price", "sku_id": "SKU_STOCKROOM", "price": 4.0}
    ) == []


def test_pricing_and_texturing_an_unknown_sku_is_caught(planogram):
    found = errors(
        planogram,
        {"op": "set_price", "sku_id": "SKU_9", "price": 3.0},
        {"op": "swap_texture", "sku_id": "SKU_8", "texture_url": "/t.png"},
    )
    assert len(found) == 2
    assert "SKU_9" in found[0]
    assert "SKU_8" in found[1]


def test_a_variant_with_no_patches_has_nothing_to_complain_about(planogram):
    assert errors(planogram) == []


# --- the equivalence the lint is for ---------------------------------------


PATCH_SETS = [
    pytest.param([INSTALL_TALKER], id="install"),
    pytest.param(
        [INSTALL_TALKER,
         {"op": "set_ad_creative", "ad_slot_id": "B2_TALKER", "creative_id": "AD_1"}],
        id="install-then-book",
    ),
    pytest.param(
        [{"op": "set_ad_creative", "ad_slot_id": "B2_TALKER", "creative_id": "AD_1"},
         INSTALL_TALKER],
        id="book-before-install",
    ),
    pytest.param([{**INSTALL_TALKER, "ad_slot_id": "B1_TALKER"}], id="duplicate-id"),
    pytest.param([INSTALL_TALKER, INSTALL_TALKER], id="duplicate-within-variant"),
    pytest.param([{**INSTALL_TALKER, "attached_to": "B9S9"}], id="attached-nowhere"),
    pytest.param([{**INSTALL_TALKER, "attached_to": "B2S1P1"}], id="attached-to-slot"),
    pytest.param([{**INSTALL_TALKER, "attached_to": "B2"}], id="attached-to-bay"),
    pytest.param(
        [INSTALL_TALKER,
         {"op": "set_ad_creative", "ad_slot_id": "B2_TALKER", "creative_id": "AD_9"}],
        id="unknown-creative",
    ),
    pytest.param(
        [{"op": "move_sku", "sku_id": "SKU_1", "to_slot_id": "B1S1P2"}], id="move"
    ),
    pytest.param(
        [{"op": "move_sku", "sku_id": "SKU_9", "to_slot_id": "B1S1P2"}],
        id="move-unknown-sku",
    ),
    pytest.param(
        [{"op": "move_sku", "sku_id": "SKU_STOCKROOM", "to_slot_id": "B1S1P2"}],
        id="move-unplaced-sku",
    ),
    pytest.param(
        [{"op": "set_price", "sku_id": "SKU_9", "price": 1.0}], id="price-unknown-sku"
    ),
    pytest.param(
        [{"op": "set_price", "sku_id": "SKU_STOCKROOM", "price": 1.0}],
        id="price-unplaced-sku",
    ),
]


@pytest.mark.parametrize("patches", PATCH_SETS)
def test_the_lint_agrees_with_resolve_on_every_patch_shape(planogram, patches):
    """The lint fails a document if and only if resolve() would refuse it.

    This is the property that stops the two from drifting again. A new op that
    resolve() learns to refuse will fail here until the lint learns it too.
    """
    try:
        resolve(planogram, variant(*patches))
    except PatchError as exc:
        refused = str(exc)
    else:
        refused = None

    found = validate_data.variant_patch_errors(planogram, variant(*patches))
    assert (found != []) == (refused is not None), (
        f"lint said {found!r}, resolve said {refused!r}"
    )


# --- end to end -------------------------------------------------------------


def test_the_committed_repository_still_validates_clean():
    """`make validate` is in the definition of done for every module."""
    done = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "validate_data.py")],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "0 error(s)" in done.stdout
