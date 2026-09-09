"""Detections -> a planogram document (S30).

The last step of video -> planogram, and the one where honesty is easiest to
lose.

A classical pipeline reads **positions, sizes and colours** off a video. It does
not read brands, product names, prices or promotions - and
`schemas/planogram.schema.json` requires all four on every SKU. So the real
question this module answers is: what do you write in a required field you did
not measure?

The answer taken here is **say so in the field itself**. `brand: "unknown"`,
`name: "unidentified product 3"`, `price: 0`, `promo: false` - values a reader
trips over rather than trusts. The alternative, plausible-looking invented
products, would produce a document indistinguishable at a glance from the
hand-authored seed planogram while being fiction, and every number computed
from it would inherit that quietly. `price` in particular feeds
`price_sensitivity` in every persona policy, and `promo` feeds
`promo_sensitivity`, so a guess there is not cosmetic.

**And no ad slots are invented.** PLAN S20 wanted promotional signs detected and
turned into ad slots. This pipeline does not detect signs, so it emits none.
Placing a creative nobody filmed would fabricate the exact variable the whole
experiment manipulates - the four variants differ in almost nothing else.

What *is* measured is measured properly, and it is enough to be useful:
`color_lab` is the real mean colour of the facing, `facings` the real count,
`confidence` the real evidence, and shelf `level` the real vertical order.
Those are precisely the inputs `sim/saliency.py` uses - shelf level, facings,
colour contrast - so a shelf read off a video is genuinely simulatable while
knowing no product identities at all. That is the claim this pipeline can
support, and it is the only one it makes.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Sequence, Tuple

from vision.track import Track

UNKNOWN_BRAND = "unknown"
UNKNOWN_CATEGORY = "unknown"

# Physical size assumed for the bay a frame shows. Nothing in a monocular video
# gives absolute scale - there is no reference object and no camera intrinsics -
# so this is a stated assumption rather than a measurement, and every `x_m` and
# `width_m` below is a *proportion of the frame* expressed in these units. The
# simulator only ever uses these lengths relatively (positions within a bay,
# widths against their shelf), so the assumption cancels; it would not cancel if
# anything downstream compared a video bay against a hand-measured one in metres.
BAY_WIDTH_M = 1.2
BAY_HEIGHT_M = 1.8

# `schemas/planogram.schema.json`'s five-value enum, top to bottom.
LEVELS = ("top", "above_eye", "eye", "below_eye", "bottom")

DEFAULT_STATION = {
    "camera_pos": [0.0, 1.5, 2.2],
    "look_at": [0.0, 1.2, 0.0],
}

Band = Tuple[float, float]


def _level_for(index: int, count: int) -> str:
    """The shelf-level name for band `index` of `count`, top to bottom.

    A bay with more bands than the enum has names is spread across the five,
    rather than emitting a sixth name the schema would refuse. A single band is
    eye level: with no shelf above or below it, that is the honest reading of
    "the shelf that was filmed", and it is the level the saliency model treats
    as neutral.
    """
    if count <= 1:
        return "eye"
    position = index / (count - 1)
    return LEVELS[min(len(LEVELS) - 1, int(round(position * (len(LEVELS) - 1))))]


def build_planogram(
    bands: Mapping[Band, Sequence[Track]],
    *,
    frame_width: int,
    frame_height: int,
    planogram_id: str = "video_aisle",
    bay_id: str = "V1",
) -> Dict[str, Any]:
    """A schema-valid planogram from one bay's worth of detections.

    `bands` maps (top, bottom) pixel rows to the facings standing in that band.
    Bands are ordered top to bottom here regardless of the mapping's own order,
    because that order becomes shelf `level` and level is load-bearing.

    Raises `ValueError` rather than emitting a document when nothing was
    detected. `planogram.schema.json` requires at least one bay and one SKU, and
    a store with no shelves or no stock is not a store - an empty planogram
    would later read as a shop that was filmed and found bare, which is a claim
    about the shop rather than about the footage.
    """
    if not bands:
        raise ValueError(
            "no shelves were detected in this video, so there is no planogram to "
            "build. A front-on shot of a shelf bay, with the shelf edges visible "
            "across most of the frame, is what this pipeline reads."
        )

    ordered = sorted(bands.items(), key=lambda item: item[0][0])
    if not any(facings for _, facings in ordered):
        raise ValueError(
            "shelves were detected but no product facings were found on any of "
            "them. Either the shelves are empty, or the products are not "
            "separable by colour at this resolution."
        )

    shelves = []
    skus = []
    sku_index = 0

    for shelf_index, ((top, bottom), facings) in enumerate(ordered):
        shelf_id = f"{bay_id}S{shelf_index + 1}"
        slots = []

        for slot_index, facing in enumerate(sorted(facings, key=lambda t: t.x0)):
            sku_index += 1
            sku_id = f"V_{sku_index:03d}"

            skus.append(
                {
                    "sku_id": sku_id,
                    # Every one of these four says, in the field, that it was
                    # not measured. See the module docstring.
                    "name": f"unidentified product {sku_index}",
                    "brand": UNKNOWN_BRAND,
                    "category": UNKNOWN_CATEGORY,
                    "price": 0,
                    "promo": False,
                    "texture_url": "",
                    # Measured, and real. This is what makes the document
                    # simulatable despite the four fields above.
                    "color_lab": [round(float(v), 4) for v in facing.color_lab],
                }
            )

            slots.append(
                {
                    "slot_id": f"{shelf_id}P{slot_index + 1}",
                    "sku_id": sku_id,
                    # One detected block is one facing. A classical segmenter
                    # cannot tell one wide pack from two identical narrow ones
                    # standing flush, and claiming a count it did not measure
                    # would multiply attention by a guess.
                    "facings": 1,
                    "x_m": round(facing.x0 / frame_width * BAY_WIDTH_M, 4),
                    "width_m": round(
                        max(facing.width, 1) / frame_width * BAY_WIDTH_M, 4
                    ),
                    "height_m": round(
                        max(facing.bottom - facing.top, 1) / frame_height * BAY_HEIGHT_M,
                        4,
                    ),
                    "confidence": round(float(facing.confidence), 4),
                }
            )

        shelves.append(
            {
                "shelf_id": shelf_id,
                # How high the shelf *sits*, not how tall its band is. Nothing
                # in planogram.schema.json says which of the two this field
                # means - it types it as a number and stops - so the meaning
                # lives in the code that reads it: `web/src/store/geometry.ts`
                # places a slot at `shelf.height_m + slot.height_m / 2` and the
                # shelf board at `shelf.height_m - board / 2`, and the seed
                # planogram descends 1.7, 1.45, 1.2, 0.85, 0.4 down the bay.
                #
                # The band's own thickness is not lost: it is `slot.height_m`
                # below, which is what it always meant. Writing it here as well
                # put all five shelves of a video-read bay within four
                # centimetres of each other, and the only reason that never
                # showed is that the reading has no route into the scene.
                #
                # Measured to the shelf's front lip - the bottom of the band -
                # because that is the surface a pack stands on, and from the
                # bottom of the frame because that is where the floor is.
                "height_m": round(
                    max(frame_height - bottom, 0) / frame_height * BAY_HEIGHT_M, 4
                ),
                "level": _level_for(shelf_index, len(ordered)),
                "slots": slots,
            }
        )

    return {
        "planogram_id": planogram_id,
        # Read by a person, months later, beside the hand-authored seed. It has
        # to be obvious at a glance which is which and what is missing from it.
        "name": "Aisle read from video — products not identified, prices unknown",
        "source": "video",
        "bays": [
            {
                "bay_id": bay_id,
                "type": "shelf",
                "width_m": BAY_WIDTH_M,
                "height_m": BAY_HEIGHT_M,
                "station": dict(DEFAULT_STATION),
                "shelves": shelves,
                # Empty, deliberately: no sign detection, so no invented ads.
                "ad_slots": [],
            }
        ],
        "skus": skus,
        "creatives": [],
    }
