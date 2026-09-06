"""Finding the shelves in a frame (S30).

The first half of video -> planogram, and the half everything else hangs off:
a facing can only be placed once the shelf it sits on is known, and the shelf
*level* - top, eye, bottom - is the single strongest term in
`sim/saliency.py`. Get the bands wrong and every attention number derived from
the video is wrong in the same direction.

Classical CV rather than a detection model, per the choice made for this
machine: a shelf edge is a long horizontal discontinuity, which a vertical
Sobel and a column sum find directly and cheaply. It is less general than
Grounding DINO and this suite is written to say where it stops: a frame with no
shelves in it must return no shelves, not one weak guess.

Every fixture here is drawn with numpy rather than loaded, so the tests are
deterministic, need no video file, and run on any machine.
"""
from __future__ import annotations

import numpy as np
import pytest

from vision.shelves import cluster_1d, shelf_bands, shelf_edge_rows

HEIGHT = 480
WIDTH = 640


def shelf_frame(edge_rows, *, thickness: int = 4) -> np.ndarray:
    """A grey wall with dark horizontal shelf edges across the full width."""
    frame = np.full((HEIGHT, WIDTH, 3), 190, dtype=np.uint8)
    for row in edge_rows:
        frame[row : row + thickness, :, :] = 40
    return frame


def test_finds_the_rows_a_frame_actually_has():
    frame = shelf_frame([80, 200, 320])

    rows = shelf_edge_rows(frame)

    assert len(rows) == 3
    for expected, found in zip([80, 200, 320], sorted(rows)):
        assert abs(found - expected) <= 6, (expected, found)


def test_finds_nothing_in_a_frame_with_no_shelves():
    """A blank wall has no shelves, and the honest answer is none.

    A detector that always returns its best guess turns "there was nothing to
    see" into a planogram, and nothing downstream can tell the difference.
    """
    frame = np.full((HEIGHT, WIDTH, 3), 190, dtype=np.uint8)

    assert shelf_edge_rows(frame) == []


def test_ignores_a_short_horizontal_mark():
    """A price label or a shadow is not a shelf. Only an edge spanning most of
    the frame counts, which is what `min_span` is for."""
    frame = np.full((HEIGHT, WIDTH, 3), 190, dtype=np.uint8)
    frame[200:204, 10:80, :] = 40  # a 70px mark in a 640px frame

    assert shelf_edge_rows(frame) == []


def test_ignores_vertical_structure():
    """Bay uprights are vertical and must not be read as shelves."""
    frame = np.full((HEIGHT, WIDTH, 3), 190, dtype=np.uint8)
    frame[:, 100:106, :] = 40
    frame[:, 400:406, :] = 40

    assert shelf_edge_rows(frame) == []


def test_two_edges_a_few_pixels_apart_are_one_shelf():
    """A shelf lip has a top and a bottom edge. Reporting both would double
    the shelf count and halve every band."""
    frame = shelf_frame([200])
    frame[206:210, :, :] = 40  # the underside of the same lip

    assert len(shelf_edge_rows(frame)) == 1


class TestBands:
    """A band is the space *between* shelf edges - where products stand."""

    def test_one_band_per_gap_plus_the_top(self):
        frame = shelf_frame([120, 240, 360])

        bands = shelf_bands(frame)

        # Above the first edge, and between each consecutive pair.
        assert len(bands) == 3

    def test_a_band_spans_from_one_edge_to_the_next(self):
        frame = shelf_frame([120, 240, 360])

        bands = shelf_bands(frame)

        assert bands[1].top >= 118 and bands[1].top <= 130
        assert bands[1].bottom >= 234 and bands[1].bottom <= 246
        assert bands[1].bottom > bands[1].top

    def test_bands_come_back_top_to_bottom(self):
        frame = shelf_frame([360, 120, 240])

        bands = shelf_bands(frame)

        assert [band.top for band in bands] == sorted(band.top for band in bands)

    def test_no_shelves_means_no_bands(self):
        frame = np.full((HEIGHT, WIDTH, 3), 190, dtype=np.uint8)

        assert shelf_bands(frame) == []

    def test_a_band_too_thin_to_hold_a_product_is_dropped(self):
        """Two edges 6 pixels apart bound a gap nothing can stand in. Keeping
        it would produce a shelf of zero-height slots."""
        frame = np.full((HEIGHT, WIDTH, 3), 190, dtype=np.uint8)
        frame[100:104, :, :] = 40
        frame[300:304, :, :] = 40
        frame[306:310, :, :] = 40  # merged into the one above, not a new band

        bands = shelf_bands(frame)

        assert all(band.bottom - band.top >= 20 for band in bands)


class TestCluster1d:
    """PLAN S20 asks for 1-D k-means on centre y. It is used to collapse the
    same shelf seen at slightly different rows across frames."""

    def test_groups_nearby_values(self):
        centres = cluster_1d([100.0, 102.0, 98.0, 300.0, 301.0], k=2)

        assert len(centres) == 2
        assert abs(centres[0] - 100.0) < 3
        assert abs(centres[1] - 300.5) < 3

    def test_returns_centres_in_order(self):
        assert cluster_1d([300.0, 100.0], k=2) == sorted(cluster_1d([300.0, 100.0], k=2))

    def test_k_larger_than_the_data_returns_the_data(self):
        """Never invents a cluster to reach k: an empty cluster has no centre,
        and a fabricated one would become a shelf nobody filmed."""
        centres = cluster_1d([100.0, 200.0], k=5)

        assert centres == [100.0, 200.0]

    def test_empty_input_is_empty_output(self):
        assert cluster_1d([], k=3) == []

    def test_is_deterministic(self):
        values = [10.0, 12.0, 400.0, 402.0, 200.0]

        assert cluster_1d(values, k=3) == cluster_1d(values, k=3)

    @pytest.mark.parametrize("k", [0, -1])
    def test_a_meaningless_k_is_refused(self, k: int):
        with pytest.raises(ValueError):
            cluster_1d([1.0, 2.0], k=k)
