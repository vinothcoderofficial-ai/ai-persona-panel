"""What the camera did wrong, measured before the shelves are read (S30).

Two failures separate a clip somebody actually filmed from the rendering this
repository draws of its own planogram, and `vision/shelves.py` survives
neither.

**Roll.** `shelf_edge_rows` asks whether a single pixel row is more than
`MIN_SPAN` edge. A shelf lip tilted by theta smears its response across
`width * tan(theta)` rows, so the per-row fraction falls as the tilt grows and
at about one degree on a 960-wide frame no row clears the bar at all. One
degree is nothing - nobody holds a phone that level - and the failure is not
even honest across its whole range: a little under a degree the detector does
not refuse, it returns *fewer* shelves than the bay has, and shelf level is the
largest term in `sim/saliency.py`. So the tilt has to be measured and undone
before the edges are looked for, and this module is where that happens.

**Drift.** `vision/track.py` matches facings between frames at IoU 0.5, which
assumes the same pack lands in roughly the same place each time. That is true
of a phone propped against a shelf and false of a phone being carried down an
aisle: the pack moves, never overlaps itself, and every appearance is counted
as a different product. A twenty-second walk past three bays reports over a
hundred facings for two dozen packs, and reports them with confidence, because
nothing downstream can see that the camera moved. Measuring the drift is what
lets the pipeline refuse instead - which is the rule the rest of this package
already follows.

Every fixture here is drawn with numpy and rotated or shifted with OpenCV, so
the tests state the tilt and the pan exactly rather than hoping a video file
contains them.
"""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from vision.camera import (
    MAX_DRIFT_FRACTION,
    ROLL_SEARCH_DEGREES,
    camera_drift,
    deskew,
    estimate_roll,
)
from vision.facings import facing_boxes
from vision.shelves import shelf_bands

# The committed fixture's size, and not incidental: how far a tilt smears a
# shelf edge is `width * tan(theta)`, so the tilt at which the detector gives
# up is a function of frame width. At 640 wide a degree of roll is survivable
# and these tests would pass without the module they are testing.
HEIGHT = 720
WIDTH = 960
EDGE_ROWS = (180, 360, 540)


BACKING = 190

# Two packs per shelf, at the margins the committed fixture actually uses: the
# leftmost starts 40px into a 960-wide frame. That 4% is the whole test. With a
# comfortable margin the corner wedges a rotation leaves fall on backing and
# nothing goes wrong; at 40px they land on the pack, and whatever fills them
# becomes stock.
PACK_SPANS = ((40, 430), (480, 870))
PACK_COLOURS = ((200, 70, 50), (60, 60, 210))


def level_frame() -> np.ndarray:
    """A front-on bay: three full-width shelf lips on a grey backing."""
    frame = np.full((HEIGHT, WIDTH, 3), BACKING, dtype=np.uint8)
    for row in EDGE_ROWS:
        frame[row : row + 5, :, :] = 35
    return frame


def packed_frame() -> np.ndarray:
    """The same bay with three packs standing on each of its three shelves.

    They stop well short of the frame edge, as packs on a real shelf do, so
    the backing shows at both ends of every band - which is the condition
    `vision/facings.py` uses to tell a shelf's background from its stock.
    """
    frame = level_frame()
    tops = [0] + [row + 5 for row in EDGE_ROWS[:-1]]
    for top, bottom in zip(tops, EDGE_ROWS):
        for (x0, x1), colour in zip(PACK_SPANS, PACK_COLOURS):
            frame[top + 12 : bottom - 6, x0:x1] = colour
    return frame


def rolled(frame: np.ndarray, degrees: float) -> np.ndarray:
    """`frame` as a camera held `degrees` off level would have seen it."""
    matrix = cv2.getRotationMatrix2D((WIDTH / 2, HEIGHT / 2), degrees, 1.0)
    return cv2.warpAffine(frame, matrix, (WIDTH, HEIGHT), borderValue=(190, 190, 190))


def panned(frame: np.ndarray, dx: int) -> np.ndarray:
    """`frame` as seen after the camera has travelled `dx` pixels sideways."""
    matrix = np.float32([[1, 0, -dx], [0, 1, 0]])
    return cv2.warpAffine(frame, matrix, (WIDTH, HEIGHT), borderValue=(190, 190, 190))


class TestEstimateRoll:
    def test_a_level_frame_has_no_roll(self) -> None:
        assert abs(estimate_roll(level_frame())) <= 0.25

    @pytest.mark.parametrize("degrees", [-5.0, -3.0, -1.0, 1.0, 3.0, 5.0])
    def test_measures_the_tilt_a_frame_actually_has(self, degrees: float) -> None:
        """Within a quarter degree, which is the search step.

        Signed, and the sign matters: `deskew` has to know which way to turn.
        """
        found = estimate_roll(rolled(level_frame(), degrees))

        assert abs(found - degrees) <= 0.3, (degrees, found)

    def test_a_frame_with_nothing_in_it_reports_no_roll(self) -> None:
        """A blank wall has no shelf edges and therefore no measurable tilt.

        Reporting the argmax of a flat objective would rotate a frame by an
        arbitrary angle on no evidence, and the frame that comes out would be
        read as though the geometry had been corrected.
        """
        assert estimate_roll(np.full((HEIGHT, WIDTH, 3), 190, dtype=np.uint8)) == 0.0

    def test_never_reports_more_than_it_searched(self) -> None:
        """A bay filmed from far off level is beyond what this can fix.

        The honest outcome is the best angle inside the search range, not a
        wrong angle outside it - the caller finds no shelves and the pipeline
        refuses, which is the same answer it gives today for footage it cannot
        read.
        """
        found = estimate_roll(rolled(level_frame(), 20.0))

        assert abs(found) <= ROLL_SEARCH_DEGREES


class TestDeskew:
    def test_leaves_a_level_frame_alone(self) -> None:
        frame = level_frame()

        assert np.array_equal(deskew(frame, 0.0), frame)

    @pytest.mark.parametrize("degrees", [1.0, 2.0, 3.0, 5.0])
    def test_restores_the_shelves_a_tilt_destroys(self, degrees: float) -> None:
        """The whole reason this module exists.

        `shelf_bands` finds three bands on the level frame and nothing at all
        once it is tilted a degree. After deskewing it finds them again.
        """
        tilted = rolled(level_frame(), degrees)
        assert shelf_bands(tilted) == [], "fixture no longer defeats the detector"

        levelled = deskew(tilted, estimate_roll(tilted))

        assert len(shelf_bands(levelled)) == len(EDGE_ROWS)

    def test_does_not_invent_a_shelf_in_an_empty_frame(self) -> None:
        blank = np.full((HEIGHT, WIDTH, 3), 190, dtype=np.uint8)

        assert shelf_bands(deskew(blank, estimate_roll(blank))) == []

    @pytest.mark.parametrize("degrees", [1.0, 3.0, 5.0])
    def test_does_not_invent_products_at_the_edges_of_the_frame(
        self, degrees: float
    ) -> None:
        """Correcting the geometry must not change the stock count.

        Turning a frame leaves wedges at the corners that were never filmed,
        and whatever fills them is read by `vision/facings.py` as more shelf.
        Filling them by replicating the border smears a pack sideways to the
        frame edge, which breaks that module's rule that a band with matching
        ends has a background behind it - background suppression switches off
        and every colour run becomes a facing. The count then rises with the
        tilt, which would make "how level was the phone" a term in the shelf's
        attention.
        """
        packed = packed_frame()
        expected = sum(len(facing_boxes(packed, band)) for band in shelf_bands(packed))
        assert expected == len(PACK_SPANS) * len(EDGE_ROWS), "fixture is not 6 packs"

        tilted = rolled(packed, degrees)
        levelled = deskew(tilted, estimate_roll(tilted))

        found = sum(len(facing_boxes(levelled, band)) for band in shelf_bands(levelled))
        assert found == expected, (degrees, found, expected)


class TestCameraDrift:
    def test_a_locked_off_camera_has_no_drift(self) -> None:
        frames = [level_frame() for _ in range(6)]

        assert camera_drift(frames) < MAX_DRIFT_FRACTION

    def test_a_pixel_of_handheld_shake_is_not_movement(self) -> None:
        """Both committed fixtures jitter by a pixel on purpose, so the IoU
        stage is exercised. That must stay under the bar."""
        frames = [panned(level_frame(), n % 3 - 1) for n in range(8)]

        assert camera_drift(frames) < MAX_DRIFT_FRACTION

    def test_a_walk_down_the_aisle_is_movement(self) -> None:
        """Thirty-two pixels a sample on a 640-wide frame is five percent of
        the frame per step - the rate a pan across three bays actually moves."""
        frames = [panned(level_frame(), 32 * n) for n in range(8)]

        assert camera_drift(frames) > MAX_DRIFT_FRACTION

    def test_one_frame_cannot_have_moved(self) -> None:
        """Drift is a property of a sequence. With nothing to compare against
        the answer is zero, not a crash."""
        assert camera_drift([level_frame()]) == 0.0

    def test_no_frames_is_no_drift(self) -> None:
        assert camera_drift([]) == 0.0
