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


def soft_edged_band_frame(colours, *, ramp: int, top: int = 50, bottom: int = 250) -> np.ndarray:
    """`colours` as blocks, but blending into each other over `ramp` columns.

    A drawn rectangle has an edge one pixel wide. Nothing filmed does: depth of
    field, motion blur, the shelf's own shadow and any resampling on the way in
    all spread a pack's edge over a band of columns instead.
    """
    frame = np.full((HEIGHT, WIDTH, 3), 150, dtype=np.float32)
    span = WIDTH // len(colours)
    for index, colour in enumerate(colours):
        frame[top:bottom, index * span : (index + 1) * span] = colour

    blurred = frame.copy()
    for index in range(1, len(colours)):
        edge = index * span
        left = frame[top:bottom, edge - 1].astype(np.float32)
        right = frame[top:bottom, edge].astype(np.float32)
        for step in range(-ramp // 2, ramp // 2 + 1):
            weight = (step + ramp / 2) / ramp
            blurred[top:bottom, edge + step] = left * (1 - weight) + right * weight

    return blurred.astype(np.uint8)


def test_a_soft_edge_between_two_packs_is_still_two_facings():
    """The break test has to survive an edge that arrives gradually.

    `_raw_runs` compares each column against the one before it, so a boundary
    that ramps over twenty columns never shows a single step big enough to
    count - the two packs merge into one run and the shelf reads as holding
    half the stock it holds. Rotating a frame produces exactly this ramp, and
    so does any real lens, which is why this is not a synthetic worry.

    Re-measured on the rendering `scripts/make_vision_fixture.py` draws of bay
    0 of `data/planograms/demo_aisle.json`, which holds five shelves and eight
    filled slots: it reads five bands and eight facings level, and five bands
    and eight facings at every tilt from 0.5 through 6.0 degrees turned and
    turned back. Seven degrees saturates the search and is refused rather than
    read. This once said the fixture read ten after a rotation; that number is
    from before the two-neighbourhood break test above, and it no longer holds.
    """
    frame = soft_edged_band_frame([BLUE, RED], ramp=24)

    assert len(facing_boxes(frame, BAND)) == 2


def test_a_soft_edged_shelf_reads_the_same_count_as_a_sharp_one():
    sharp = facing_boxes(band_frame([BLUE, RED, GREEN]), BAND)
    soft = facing_boxes(soft_edged_band_frame([BLUE, RED, GREEN], ramp=24), BAND)

    assert len(soft) == len(sharp) == 3


def gradient_band_frame(low: int, high: int, *, top: int = 50, bottom: int = 250) -> np.ndarray:
    """An EMPTY shelf, lit unevenly: backing only, brightening left to right."""
    frame = np.zeros((HEIGHT, WIDTH, 3), dtype=np.float32)
    ramp = np.linspace(low, high, WIDTH, dtype=np.float32)
    frame[:, :, :] = ramp[None, :, None]
    return frame.astype(np.uint8)


def vignetted_band_frame(depth: float, *, base: int = 190) -> np.ndarray:
    """An EMPTY shelf under a lens that falls off at both ends.

    The realistic optical case, and harder than a straight ramp: a parabola
    leaves a running mean faster than a line does.
    """
    x = np.linspace(-1.0, 1.0, WIDTH, dtype=np.float32)
    falloff = 1.0 - depth * (x ** 2)
    frame = np.zeros((HEIGHT, WIDTH, 3), dtype=np.float32)
    frame[:, :, :] = (base * falloff)[None, :, None]
    return frame.astype(np.uint8)


def test_an_empty_shelf_lit_unevenly_is_still_empty():
    """A lighting gradient is not stock.

    The break test compares a column against its run's mean so far, which is
    what lets it see an edge that arrives gradually. The same property makes it
    sensitive to a gradient that never arrives at all: drift from the mean of
    the first n columns grows like slope*n/2, so a smooth ramp eventually
    crosses any fixed threshold no matter how gentle it is, and the band splits.
    Once it is two runs, `_background` finds their means far apart, assumes
    there is no backing, and returns both as facings.

    Measured before this was fixed: an empty band spanning about 25 L* read as
    two products, and a 30% radial falloff - ordinary for a phone lens - was
    enough. Nothing downstream filters them, because a lighting gradient is
    static: it appears in every sampled frame, and `vision/track.py` reads that
    as corroboration and raises the confidence.
    """
    assert facing_boxes(gradient_band_frame(150, 210), BAND) == []


def test_a_lens_that_falls_off_at_the_edges_does_not_stock_the_shelf():
    assert facing_boxes(vignetted_band_frame(0.40), BAND) == []


def test_a_gradient_across_a_real_pack_finds_the_pack_whole():
    """The pack is found, once, at its true edges - and the backing beside it
    is NOT suppressed, which is a limitation rather than a success.

    `_background` decides there is backing by asking whether the band's two end
    stretches are the same colour. Under a gradient they are genuinely not, so
    it concludes the band is stocked edge to edge and returns the empty
    stretches as products too. That is a real cost on real footage, where a
    shelf is almost never lit evenly.

    It is recorded here rather than fixed because the obvious fix is worse.
    Removing a linear trend before the comparison does suppress the backing -
    and it also flattens genuine structure, so a band holding two differently
    coloured packs starts reading as backing. Measured: it fixed this case and
    broke five that already worked.

    So the property worth pinning is the one that holds: the pack itself is
    found once, at its real boundaries, whatever the light is doing. An
    over-counted backing run inflates `facings` on a shelf; a split or missing
    pack would corrupt the geometry the whole planogram is built from.
    """
    frame = gradient_band_frame(150, 210).astype(np.int16)
    frame[60:240, 200:440] = np.array(RED, dtype=np.int16)

    boxes = facing_boxes(frame.astype(np.uint8), BAND)

    pack = [box for box in boxes if box.x0 <= 210 and box.x1 >= 430]
    assert len(pack) == 1, f"the pack was split or lost: {boxes}"
    assert abs(pack[0].x0 - 200) <= 4 and abs(pack[0].x1 - 440) <= 4
