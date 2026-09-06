"""Agreeing across frames (S30).

PLAN S20 asks for "IoU 0.5 dedupe". One frame is a guess; the same facing found
in the same place across several frames is evidence, and the difference between
those two is exactly what `confidence` in the emitted planogram is supposed to
mean.

So this module does two things that look like one:

  * **dedupe** - the same pack seen in twelve frames is one facing, not twelve;
  * **corroborate** - how many frames agreed becomes part of that facing's
    confidence, so a slot detected once and a slot detected every time do not
    arrive in the planogram looking identical.

The second is the reason this exists at all. Without it a single frame's
misfire enters the planogram with the same authority as a shelf that was
visible throughout, and nothing downstream - not the simulator, not the
dashboard - can tell them apart.
"""
from __future__ import annotations

import pytest

from vision.facings import Facing
from vision.track import Track, iou, merge_across_frames


def facing(x0: int, x1: int, top: int = 100, bottom: int = 200, conf: float = 0.8) -> Facing:
    return Facing(
        x0=x0,
        x1=x1,
        top=top,
        bottom=bottom,
        color_lab=(50.0, 10.0, -5.0),
        confidence=conf,
    )


class TestIou:
    def test_identical_boxes_overlap_completely(self):
        assert iou((0, 0, 10, 10), (0, 0, 10, 10)) == pytest.approx(1.0)

    def test_disjoint_boxes_do_not_overlap(self):
        assert iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0

    def test_touching_boxes_do_not_overlap(self):
        """Sharing an edge is not sharing an area, and two packs side by side
        share an edge. Counting that as overlap would merge every facing on a
        shelf into one."""
        assert iou((0, 0, 10, 10), (10, 0, 20, 10)) == 0.0

    def test_half_overlap_is_a_third(self):
        # Intersection 50, union 150.
        assert iou((0, 0, 10, 10), (5, 0, 15, 10)) == pytest.approx(50 / 150)

    def test_a_zero_area_box_overlaps_nothing(self):
        """Never a division by zero, and never a NaN reaching a comparison."""
        assert iou((5, 5, 5, 5), (0, 0, 10, 10)) == 0.0


class TestMerge:
    def test_the_same_facing_in_every_frame_is_one_facing(self):
        frames = [[facing(0, 100)], [facing(2, 102)], [facing(1, 99)]]

        tracks = merge_across_frames(frames)

        assert len(tracks) == 1

    def test_records_how_many_frames_agreed(self):
        """The whole point: a facing seen three times is better evidenced than
        one seen once, and the planogram has to be able to say so."""
        frames = [[facing(0, 100)], [facing(2, 102)], [facing(1, 99)]]

        assert merge_across_frames(frames)[0].seen_in == 3

    def test_two_separate_facings_stay_separate(self):
        frames = [[facing(0, 100), facing(200, 300)]] * 3

        assert len(merge_across_frames(frames)) == 2

    def test_a_facing_seen_once_is_still_reported(self):
        """Dropped evidence is worse than weak evidence. It is kept, with a
        `seen_in` of 1, and the confidence maths downgrades it."""
        frames = [[facing(0, 100)], [facing(0, 100)], [facing(500, 600)]]

        tracks = merge_across_frames(frames)

        assert len(tracks) == 2
        assert min(track.seen_in for track in tracks) == 1

    def test_tracks_come_back_left_to_right(self):
        frames = [[facing(400, 500), facing(0, 100), facing(200, 300)]]

        tracks = merge_across_frames(frames)

        assert [track.x0 for track in tracks] == sorted(track.x0 for track in tracks)

    def test_the_merged_box_is_the_average_of_what_was_seen(self):
        frames = [[facing(0, 100)], [facing(10, 110)]]

        track = merge_across_frames(frames)[0]

        assert track.x0 == pytest.approx(5, abs=1)
        assert track.x1 == pytest.approx(105, abs=1)

    def test_confidence_rises_with_corroboration(self):
        once = merge_across_frames([[facing(0, 100)]])
        thrice = merge_across_frames([[facing(0, 100)]] * 3)

        assert thrice[0].confidence > once[0].confidence

    def test_confidence_never_leaves_the_schema_range(self):
        """`slot.confidence` is 0..1; anything else fails validation and the
        whole planogram is refused."""
        many = merge_across_frames([[facing(0, 100, conf=1.0)]] * 50)

        assert 0.0 <= many[0].confidence <= 1.0

    def test_a_weak_detection_stays_weak_however_often_it_repeats(self):
        """Corroboration multiplies the per-frame evidence rather than
        replacing it: fifty frames agreeing that something is *barely* there
        is still fifty frames of barely."""
        weak = merge_across_frames([[facing(0, 100, conf=0.05)]] * 50)
        strong = merge_across_frames([[facing(0, 100, conf=0.9)]] * 3)

        assert weak[0].confidence < strong[0].confidence

    def test_no_frames_means_no_tracks(self):
        assert merge_across_frames([]) == []

    def test_frames_with_nothing_in_them_are_not_an_error(self):
        """A clip that pans off the shelf has frames with no facings, and that
        is data rather than a failure."""
        assert merge_across_frames([[], [], []]) == []

    def test_is_deterministic(self):
        frames = [[facing(0, 100), facing(200, 300)], [facing(3, 103)]]

        first = merge_across_frames(frames)
        second = merge_across_frames(frames)

        assert [(t.x0, t.x1, t.seen_in) for t in first] == [
            (t.x0, t.x1, t.seen_in) for t in second
        ]


class TestTrack:
    def test_carries_the_colour_it_measured(self):
        track = merge_across_frames([[facing(0, 100)]])[0]

        assert len(track.color_lab) == 3
