"""video -> planogram, end to end (S30).

Every other test in this package works on numpy arrays, which is what makes
them fast and deterministic. This one writes an actual video file and reads it
back through the whole pipeline, because the stages agreeing individually is
not the same as the pipeline working: frame sampling, band reuse across frames,
IoU matching and schema assembly can each be right while the composition is
wrong, and only a file exercises the decode path at all.

The clip is generated rather than recorded. There is no aisle footage in this
repository and none can be invented, so what is tested here is that a video
*whose contents are known* is read correctly - which is the strongest statement
available without a camera. It is emphatically **not** a claim about accuracy
on real footage; the pipeline's limits are stated in `vision/pipeline.py` and
its confidence numbers are there so a real clip can be judged rather than
trusted.
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest
from jsonschema import Draft7Validator

from vision.frames import VideoUnreadable, extract_frames
from vision.pipeline import draw_overlay, run, write_overlays

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "schemas" / "planogram.schema.json").read_text(encoding="utf-8"))

WIDTH = 640
HEIGHT = 480
SHELF_EDGES = (150, 280, 410)
PRODUCTS = [
    [(200, 70, 50), (60, 60, 210), (70, 190, 80)],
    [(40, 200, 220), (190, 80, 190), (90, 90, 90)],
    [(30, 120, 220), (210, 190, 60), (120, 40, 160)],
]


def aisle_frame(jitter: int = 0) -> np.ndarray:
    """A front-on shelf bay: three shelf edges, three packs standing on each."""
    frame = np.full((HEIGHT, WIDTH, 3), 175, dtype=np.uint8)

    band_tops = [20] + [edge for edge in SHELF_EDGES[:-1]]
    for row, colours in zip(band_tops, PRODUCTS):
        bottom = SHELF_EDGES[band_tops.index(row)]
        for index, colour in enumerate(colours):
            x0 = 30 + index * 200 + jitter
            frame[row + 8 : bottom - 4, x0 : x0 + 170] = colour

    for edge in SHELF_EDGES:
        frame[edge : edge + 5, :, :] = 35

    return frame


@pytest.fixture(name="clip")
def clip_fixture(tmp_path: Path) -> Path:
    """A three-second 640x480 clip of a static shelf, written as mp4v."""
    path = tmp_path / "aisle.mp4"
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (WIDTH, HEIGHT)
    )
    assert writer.isOpened(), "OpenCV could not open an mp4v writer"
    try:
        for n in range(30):
            # A pixel of camera shake, so the IoU matching is actually
            # exercised rather than handed identical boxes every frame.
            writer.write(aisle_frame(jitter=n % 2))
    finally:
        writer.release()
    assert path.exists() and path.stat().st_size > 0
    return path


class TestFrames:
    def test_samples_at_the_requested_rate(self, clip: Path) -> None:
        # 30 frames at 10 fps, sampled at 2 fps.
        assert len(extract_frames(clip, fps=2.0)) == 6

    def test_respects_the_frame_cap(self, clip: Path) -> None:
        assert len(extract_frames(clip, fps=10.0, max_frames=4)) == 4

    def test_a_missing_file_is_distinguishable_from_an_empty_one(
        self, tmp_path: Path
    ) -> None:
        """Different problems, different fixes. Collapsing them leaves someone
        re-encoding a video that decoded perfectly and simply had no shelves."""
        with pytest.raises(VideoUnreadable, match="no such video"):
            extract_frames(tmp_path / "nope.mp4")

    def test_a_file_that_is_not_a_video_is_refused(self, tmp_path: Path) -> None:
        junk = tmp_path / "notavideo.mp4"
        junk.write_bytes(b"this is not an mp4")

        with pytest.raises(VideoUnreadable):
            extract_frames(junk)


class TestEndToEnd:
    def test_produces_a_schema_valid_planogram(self, clip: Path) -> None:
        result = run(clip)

        errors = sorted(Draft7Validator(SCHEMA).iter_errors(result.planogram), key=str)
        assert errors == [], [e.message for e in errors]

    def test_finds_the_shelves_that_are_in_the_clip(self, clip: Path) -> None:
        result = run(clip)

        assert len(result.planogram["bays"][0]["shelves"]) == len(PRODUCTS)

    def test_finds_the_products_that_are_in_the_clip(self, clip: Path) -> None:
        result = run(clip)

        found = sum(
            len(shelf["slots"]) for shelf in result.planogram["bays"][0]["shelves"]
        )
        assert found == sum(len(row) for row in PRODUCTS)

    def test_the_shelves_are_in_the_order_they_were_filmed(self, clip: Path) -> None:
        levels = [
            shelf["level"] for shelf in run(clip).planogram["bays"][0]["shelves"]
        ]

        assert levels[0] == "top"
        assert levels[-1] == "bottom"

    def test_every_slot_carries_a_real_confidence(self, clip: Path) -> None:
        for shelf in run(clip).planogram["bays"][0]["shelves"]:
            for slot in shelf["slots"]:
                assert 0.0 < slot["confidence"] <= 1.0

    def test_corroboration_across_frames_reaches_the_document(self, clip: Path) -> None:
        """Six frames all agreeing should read as better evidence than one
        frame would. If this ever drops to a single-frame level, the band reuse
        in `pipeline.run` has stopped matching and tracking has silently
        degraded to one track per frame."""
        result = run(clip)

        assert all(
            track.seen_in >= 3
            for tracks in result.tracks_by_band.values()
            for track in tracks
        )

    def test_says_what_it_did_and_what_it_could_not(self, clip: Path) -> None:
        notes = " ".join(run(clip).notes).lower()

        assert "frames sampled" in notes
        assert "not identified" in notes
        assert "no promotional signs" in notes or "no ad slots" in notes

    def test_is_reproducible(self, clip: Path) -> None:
        """The same clip must give the same planogram. A committed
        `video_aisle.json` that changed between runs would make every number
        derived from it unreproducible."""
        assert run(clip).planogram == run(clip).planogram


class TestRefusals:
    def _write(self, path: Path, frame: np.ndarray) -> Path:
        writer = cv2.VideoWriter(
            str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (WIDTH, HEIGHT)
        )
        try:
            for _ in range(10):
                writer.write(frame)
        finally:
            writer.release()
        return path

    def test_a_clip_with_no_shelves_is_refused_rather_than_guessed_at(
        self, tmp_path: Path
    ) -> None:
        """The most important refusal in the pipeline. A blank wall must not
        become a planogram - once it is JSON, nothing downstream can tell it
        from a real store."""
        blank = self._write(
            tmp_path / "blank.mp4", np.full((HEIGHT, WIDTH, 3), 175, dtype=np.uint8)
        )

        with pytest.raises(ValueError, match="no shelf edges"):
            run(blank)

    def test_the_refusal_says_what_kind_of_shot_it_needs(self, tmp_path: Path) -> None:
        blank = self._write(
            tmp_path / "blank.mp4", np.full((HEIGHT, WIDTH, 3), 175, dtype=np.uint8)
        )

        with pytest.raises(ValueError) as excinfo:
            run(blank)

        assert "front-on" in str(excinfo.value)


class TestCamera:
    """The two things a clip filmed by a person has that a rendering does not.

    Both were found by running the pipeline against its own fixture after
    degrading it the way a phone degrades it, and both were silent: the tilt
    returned fewer shelves than the bay had, and the walk returned five times
    as many products as the aisle held. Neither raised anything.
    """

    def _write(self, path: Path, frames) -> Path:
        writer = cv2.VideoWriter(
            str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (WIDTH, HEIGHT)
        )
        assert writer.isOpened()
        try:
            for frame in frames:
                writer.write(np.ascontiguousarray(frame))
        finally:
            writer.release()
        return path

    def _tilted(self, path: Path, degrees: float) -> Path:
        matrix = cv2.getRotationMatrix2D((WIDTH / 2, HEIGHT / 2), degrees, 1.0)
        return self._write(
            path,
            (
                cv2.warpAffine(
                    aisle_frame(jitter=n % 2),
                    matrix,
                    (WIDTH, HEIGHT),
                    borderValue=(175, 175, 175),
                )
                for n in range(30)
            ),
        )

    def _walking(self, path: Path) -> Path:
        """A pan across three bays, which is what filming an aisle looks like.

        The three bays are the same shelf drawn three times, so the number of
        products that exist is known exactly: nine.
        """
        strip = np.hstack([aisle_frame(), aisle_frame(), aisle_frame()])
        travel = strip.shape[1] - WIDTH
        return self._write(
            path,
            (strip[:, (travel * n) // 29 : (travel * n) // 29 + WIDTH] for n in range(30)),
        )

    def test_reads_a_tilted_clip_that_the_detector_alone_refuses(
        self, tmp_path: Path
    ) -> None:
        """Nobody holds a phone level. Four degrees is a steady pair of hands."""
        tilted = self._tilted(tmp_path / "tilted.mp4", 4.0)

        result = run(tilted)

        assert len(result.planogram["bays"][0]["shelves"]) == len(SHELF_EDGES)

    def test_says_how_far_off_level_the_camera_was(self, tmp_path: Path) -> None:
        """A reading corrected by four degrees is not the same evidence as one
        that needed no correcting, and the notes are where that is said."""
        result = run(self._tilted(tmp_path / "tilted.mp4", 4.0))

        assert abs(result.roll_degrees - 4.0) <= 0.5
        assert any("level" in note for note in result.notes)

    def test_a_level_clip_is_not_rotated(self, clip: Path) -> None:
        assert run(clip).roll_degrees == 0.0

    def test_a_walking_shot_is_refused_rather_than_multiplied(
        self, tmp_path: Path
    ) -> None:
        """The worst failure this pipeline had.

        Panning across three bays of nine products reported over a hundred
        facings, because a pack that has moved does not overlap itself at IoU
        0.5 and every appearance was counted as a new product. It reported them
        with confidences, and once the document is JSON nothing downstream can
        see that the camera was moving.
        """
        walking = self._walking(tmp_path / "walking.mp4")

        with pytest.raises(ValueError, match="camera"):
            run(walking)

    def test_the_movement_refusal_says_what_it_would_have_got_wrong(
        self, tmp_path: Path
    ) -> None:
        walking = self._walking(tmp_path / "walking.mp4")

        with pytest.raises(ValueError) as excinfo:
            run(walking)

        message = str(excinfo.value)
        assert "same product" in message and "fixed" in message


class TestOverlays:
    def test_draws_the_reading_over_the_frame(self, clip: Path) -> None:
        """The only way anybody can audit a computer-vision step after the
        fact: the picture, with what was read drawn on it."""
        result = run(clip)
        frame = extract_frames(clip, fps=2.0)[0].image

        overlay = draw_overlay(frame, result)

        assert overlay.shape == frame.shape
        assert not np.array_equal(overlay, frame)

    def test_writes_one_png_per_sampled_frame(self, clip: Path, tmp_path: Path) -> None:
        result = run(clip)

        written = write_overlays(clip, result, tmp_path / "overlays")

        assert len(written) == result.frames_sampled
        assert all(path.exists() and path.stat().st_size > 0 for path in written)
