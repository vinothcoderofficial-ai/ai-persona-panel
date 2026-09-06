"""Segmenting one shelf band into product facings (S30).

The second half of video -> planogram. A band says "there is a shelf here";
this says how many products stand on it and how wide each one is - which is
`facings` in the planogram, the second term in `sim/saliency.py` after shelf
level.

What a classical pipeline can and cannot get from pixels is the whole design
constraint here, and it is worth being blunt about it: **this finds facings,
not products.** It measures where one block of colour ends and the next begins,
how wide each is, and what colour it is. It does not know the brand, the name
or the price, and `vision/planogram.py` says so in the document it emits rather
than filling those in with something plausible.

The colour it measures is real signal and not decoration: `color_lab` feeds the
colour-contrast term of the saliency model, so a planogram built from video is
genuinely simulatable even with every product unidentified.
"""
from __future__ import annotations

import numpy as np

from vision.facings import facing_boxes
from vision.shelves import Band

WIDTH = 600
HEIGHT = 300


def band_frame(colours, *, top: int = 50, bottom: int = 250) -> np.ndarray:
    """A frame whose band holds `colours` as equal-width blocks, on grey."""
    frame = np.full((HEIGHT, WIDTH, 3), 150, dtype=np.uint8)
    span = WIDTH // len(colours)
    for index, colour in enumerate(colours):
        frame[top:bottom, index * span : (index + 1) * span] = colour
    return frame


BLUE = (200, 60, 40)
RED = (40, 50, 210)
GREEN = (60, 190, 70)
YELLOW = (40, 210, 220)

BAND = Band(top=50, bottom=250)


def test_finds_one_box_per_product_block():
    frame = band_frame([BLUE, RED, GREEN, YELLOW])

    boxes = facing_boxes(frame, BAND)

    assert len(boxes) == 4


def test_boxes_come_back_left_to_right():
    frame = band_frame([BLUE, RED, GREEN])

    boxes = facing_boxes(frame, BAND)

    assert [box.x0 for box in boxes] == sorted(box.x0 for box in boxes)


def test_a_box_spans_the_block_it_found():
    frame = band_frame([BLUE, RED])

    boxes = facing_boxes(frame, BAND)

    assert abs(boxes[0].x0 - 0) <= 8
    assert abs(boxes[0].x1 - 300) <= 8
    assert abs(boxes[1].x1 - WIDTH) <= 8


def test_a_box_takes_its_vertical_extent_from_the_band():
    frame = band_frame([BLUE, RED])

    box = facing_boxes(frame, BAND)[0]

    assert box.top == BAND.top
    assert box.bottom == BAND.bottom


def test_measures_the_colour_it_saw():
    """`color_lab` feeds the saliency model's colour-contrast term, so this is
    real signal rather than a field filled in to satisfy the schema."""
    frame = band_frame([BLUE, RED])

    blue_box, red_box = facing_boxes(frame, BAND)

    assert len(blue_box.color_lab) == 3
    # Lab: the two differ most on a* (green-red), and in the direction that
    # says one is red and the other is not.
    assert red_box.color_lab[1] > blue_box.color_lab[1]


def test_an_empty_shelf_yields_no_facings():
    """A uniform band is an empty shelf, and empty is a real planogram state -
    `sku_id: null`, `facings: 0` - not a detection failure to paper over."""
    frame = np.full((HEIGHT, WIDTH, 3), 150, dtype=np.uint8)

    assert facing_boxes(frame, BAND) == []


def test_a_sliver_is_not_a_facing():
    """A two-pixel colour change is a scuff or a joint. A pack has width, and
    counting slivers would inflate `facings`, which multiplies attention."""
    frame = np.full((HEIGHT, WIDTH, 3), 150, dtype=np.uint8)
    frame[50:250, 300:303] = RED

    assert facing_boxes(frame, BAND) == []


def test_two_identical_packs_side_by_side_are_two_facings():
    """The commonest real shelf: the same pack repeated. They are separated by
    a visible seam, and reporting one wide facing would halve the count that
    drives attention."""
    frame = np.full((HEIGHT, WIDTH, 3), 150, dtype=np.uint8)
    frame[50:250, 20:290] = BLUE
    frame[50:250, 310:580] = BLUE

    boxes = facing_boxes(frame, BAND)

    assert len(boxes) == 2


def test_confidence_is_higher_for_a_well_separated_block():
    """Confidence has to mean something. A block standing clearly against its
    neighbours is a more certain facing than one barely distinguishable, and a
    constant 1.0 in every slot would be a decoration on a guess."""
    distinct = facing_boxes(band_frame([BLUE, YELLOW]), BAND)
    faint = facing_boxes(
        band_frame([(150, 150, 150 + 12), (150, 150, 150 - 12)]), BAND
    )

    assert distinct
    assert all(0.0 <= box.confidence <= 1.0 for box in distinct)
    if faint:
        assert max(box.confidence for box in faint) < max(
            box.confidence for box in distinct
        )


def test_confidence_stays_inside_the_schema_range():
    """`slot.confidence` is 0..1 in schemas/planogram.schema.json, so anything
    outside it fails validation and the whole planogram is refused."""
    for colours in ([BLUE], [BLUE, RED], [BLUE, RED, GREEN, YELLOW]):
        for box in facing_boxes(band_frame(colours), BAND):
            assert 0.0 <= box.confidence <= 1.0


def test_a_band_outside_the_frame_yields_nothing_rather_than_raising():
    """Bands come from a different frame in a multi-frame run, and a clip that
    changes resolution mid-way must not crash the pipeline."""
    frame = band_frame([BLUE, RED])

    assert facing_boxes(frame, Band(top=5000, bottom=6000)) == []
