"""Turning detections into a planogram document (S30).

The last step, and the one where honesty is easiest to lose. A classical
pipeline reads **positions, sizes and colours** off a video. It does not read
brands, product names, prices or promotions, and `schemas/planogram.schema.json`
requires all four on every SKU.

So the question this module answers is: what do you write in a required field
you did not measure? The answer taken here, and pinned below, is *say so in the
field itself*. `brand: "unknown"`, `name: "unidentified product 3"`,
`price: 0`, `promo: false` - values a reader trips over rather than trusts. The
alternative, plausible-looking invented products, would produce a document that
reads exactly like the hand-authored seed planogram while being fiction, and
every number computed from it would inherit that.

What *is* measured is measured properly: `color_lab` is the real mean colour of
the facing, `facings` is the real count, `confidence` is the real evidence, and
shelf `level` follows the real vertical order. That is enough for
`sim/saliency.py` - shelf level, facings, colour contrast - which is what makes
a video-derived planogram genuinely simulatable despite knowing no product
identities at all.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft7Validator

from vision.planogram import UNKNOWN_BRAND, build_planogram
from vision.track import Track

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "schemas" / "planogram.schema.json").read_text(encoding="utf-8"))


def track(x0: float, x1: float, top: float, bottom: float, conf: float = 0.8, seen: int = 3):
    return Track(
        x0=x0,
        x1=x1,
        top=top,
        bottom=bottom,
        color_lab=(55.0, 12.0, -8.0),
        confidence=conf,
        seen_in=seen,
    )


def three_shelves():
    """Three bands, two facings each, in a 640x480 frame."""
    return {
        (40, 150): [track(20, 200, 40, 150), track(220, 400, 40, 150)],
        (150, 300): [track(20, 200, 150, 300), track(220, 400, 150, 300)],
        (300, 440): [track(20, 200, 300, 440), track(220, 400, 300, 440)],
    }


def build(**kwargs):
    return build_planogram(
        three_shelves(), frame_width=640, frame_height=480, **kwargs
    )


class TestSchema:
    def test_the_document_validates(self):
        """A planogram that does not validate cannot be POSTed, resolved or
        simulated, so this is the gate everything else is behind."""
        errors = sorted(Draft7Validator(SCHEMA).iter_errors(build()), key=str)

        assert errors == [], [e.message for e in errors]

    def test_says_it_came_from_video(self):
        assert build()["source"] == "video"

    def test_uses_the_id_it_was_given(self):
        assert build(planogram_id="video_aisle")["planogram_id"] == "video_aisle"


class TestWhatWasMeasured:
    def test_one_shelf_per_band(self):
        assert len(build()["bays"][0]["shelves"]) == 3

    def test_one_slot_per_facing(self):
        shelves = build()["bays"][0]["shelves"]

        assert [len(shelf["slots"]) for shelf in shelves] == [2, 2, 2]

    def test_shelves_run_top_to_bottom(self):
        """`level` is the strongest term in sim/saliency.py, so the vertical
        order has to be the real one - the topmost band is the top shelf."""
        levels = [shelf["level"] for shelf in build()["bays"][0]["shelves"]]

        assert levels[0] == "top"
        assert levels[-1] == "bottom"

    def test_a_slot_carries_the_confidence_it_earned(self):
        slot = build()["bays"][0]["shelves"][0]["slots"][0]

        assert 0.0 <= slot["confidence"] <= 1.0
        assert slot["confidence"] == pytest.approx(0.8)

    def test_the_sku_carries_the_colour_that_was_measured(self):
        """Real signal: this feeds the colour-contrast term of the saliency
        model, which is most of what a video-derived planogram can contribute."""
        sku = build()["skus"][0]

        assert sku["color_lab"] == [55.0, 12.0, -8.0]

    def test_positions_are_metres_across_the_bay(self):
        slots = build()["bays"][0]["shelves"][0]["slots"]

        assert slots[0]["x_m"] < slots[1]["x_m"]
        assert all(slot["width_m"] > 0 for slot in slots)
        assert slots[-1]["x_m"] + slots[-1]["width_m"] <= build()["bays"][0]["width_m"] + 1e-6


class TestWhatWasNotMeasured:
    """The part that matters. A video gives no brand, no name, no price."""

    def test_brands_say_they_are_unknown(self):
        assert all(sku["brand"] == UNKNOWN_BRAND for sku in build()["skus"])

    def test_names_say_the_product_was_not_identified(self):
        assert all("unidentified" in sku["name"] for sku in build()["skus"])

    def test_prices_are_zero_rather_than_plausible(self):
        """A made-up price would be indistinguishable from a real one in every
        downstream number, and price feeds `price_sensitivity` in every persona
        policy."""
        assert all(sku["price"] == 0 for sku in build()["skus"])

    def test_nothing_is_marked_as_on_promotion(self):
        """`promo` drives the promo-sensitivity term. Guessing it would invent
        a campaign that was never filmed."""
        assert all(sku["promo"] is False for sku in build()["skus"])

    def test_the_name_of_the_planogram_says_what_is_missing(self):
        """The document is read by people, months later, next to the
        hand-authored seed. It has to be obvious which is which."""
        name = build()["name"].lower()

        assert "video" in name
        assert "not identified" in name or "unidentified" in name

    def test_no_ad_slots_are_invented(self):
        """PLAN S20 wanted promotional signs detected and turned into ad slots.
        This pipeline does not detect signs, so it reports none rather than
        placing a creative nobody filmed - which would fabricate the exact
        variable the whole experiment manipulates.
        """
        assert build()["bays"][0]["ad_slots"] == []
        assert build()["creatives"] == []


class TestEdges:
    def test_a_shelf_with_no_facings_is_still_a_shelf(self):
        """An empty shelf is a real planogram state, and the thing "move a SKU
        to eye level" moves something into (CLAUDE.md)."""
        bands = {(40, 150): [track(20, 200, 40, 150)], (150, 300): []}

        document = build_planogram(bands, frame_width=640, frame_height=480)
        shelves = document["bays"][0]["shelves"]

        assert len(shelves) == 2
        assert shelves[1]["slots"] == []

    def test_nothing_detected_at_all_is_refused_rather_than_emitted(self):
        """`planogram.schema.json` needs at least one bay and one SKU, and a
        document with neither is not a store. Raising beats emitting an empty
        planogram that later reads as a shop with no stock."""
        with pytest.raises(ValueError, match="no shelves"):
            build_planogram({}, frame_width=640, frame_height=480)

    def test_shelves_but_no_products_is_refused_too(self):
        with pytest.raises(ValueError, match="no product"):
            build_planogram(
                {(40, 150): [], (150, 300): []}, frame_width=640, frame_height=480
            )

    def test_more_than_five_bands_still_produce_valid_levels(self):
        """`level` is a five-value enum. A seven-shelf bay must map onto it
        rather than emitting an eighth level name the schema refuses."""
        bands = {
            (n * 60, n * 60 + 55): [track(20, 200, n * 60, n * 60 + 55)]
            for n in range(7)
        }

        document = build_planogram(bands, frame_width=640, frame_height=480)
        errors = sorted(Draft7Validator(SCHEMA).iter_errors(document), key=str)

        assert errors == [], [e.message for e in errors]

    def test_one_shelf_is_eye_level(self):
        """With a single band there is no top or bottom to speak of, and eye
        level is the honest reading of "the shelf that was filmed"."""
        bands = {(40, 150): [track(20, 200, 40, 150)]}

        document = build_planogram(bands, frame_width=640, frame_height=480)

        assert document["bays"][0]["shelves"][0]["level"] == "eye"


class TestReproducibility:
    def test_the_same_detections_give_the_same_document(self):
        """A committed planogram that changed between runs would make every
        number derived from it unreproducible, which is what `scripts/eval.py`
        exists to prevent."""
        assert build() == build()


def test_shelf_height_is_elevation_above_the_floor_not_band_thickness():
    """`shelf.height_m` means how high the shelf sits, not how tall it is.

    Nothing in `schemas/planogram.schema.json` says which - it types the field
    as a number and stops - so the meaning lives in the two places that read
    it. `web/src/store/geometry.ts` puts a slot at `shelf.height_m +
    slot.height_m / 2` and the shelf board at `shelf.height_m -
    SHELF_BOARD_THICKNESS_M / 2`, both of which are elevations, and the seed
    planogram descends 1.7, 1.45, 1.2, 0.85, 0.4 down the bay.

    Writing the band's thickness here instead put every shelf of a video-read
    bay at roughly the same height - five shelves inside one 0.35 m blob - and
    it would have been found on stage rather than here, because the vision
    reading has no route into the scene yet.
    """
    bands = {
        (0, 140): [track(10, 60, 0, 10)],
        (140, 300): [track(10, 60, 0, 10)],
        (300, 480): [track(10, 60, 0, 10)],
    }

    document = build_planogram(bands, frame_width=960, frame_height=720)

    heights = [shelf["height_m"] for shelf in document["bays"][0]["shelves"]]
    assert heights == sorted(heights, reverse=True), heights
    assert all(height >= 0 for height in heights)


def test_the_lowest_shelf_sits_near_the_floor_and_the_highest_near_the_top():
    """The bay is 1.8 m tall, so the elevations have to span most of it.

    A reading whose shelves all landed in the bottom tenth would still descend
    and still pass the ordering test above, while rendering as a heap.
    """
    bands = {(0, 180): [track(10, 60, 0, 10)], (180, 700): [track(10, 60, 0, 10)]}

    document = build_planogram(bands, frame_width=960, frame_height=720)

    top, bottom = [shelf["height_m"] for shelf in document["bays"][0]["shelves"]]
    bay_height = document["bays"][0]["height_m"]
    assert top > bay_height * 0.6, top
    assert bottom < bay_height * 0.15, bottom
