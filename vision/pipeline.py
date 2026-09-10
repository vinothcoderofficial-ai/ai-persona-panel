"""video -> planogram, end to end (S30).

    python -m vision.pipeline --video aisle.mp4 --out data/planograms/video_aisle.json
    python -m vision.pipeline --video aisle.mp4 --overlays data/vision/overlay_frames

Six stages, one per module, in the order PLAN S20 lays out:

    extract_frames  -> vision/frames.py     sample the clip at 2 fps
    camera_drift    -> vision/camera.py     refuse a clip the camera walked
    estimate_roll   -> vision/camera.py     measure the tilt, turn it back
    shelf_bands     -> vision/shelves.py    horizontal edges -> product bands
    facing_boxes    -> vision/facings.py    colour runs within a band -> facings
    merge_across_frames -> vision/track.py  IoU 0.5 dedupe + corroboration
    build_planogram -> vision/planogram.py  a schema-valid document

**What this pipeline is, and is not.** PLAN S20 specified Grounding DINO on a
GPU. This machine has no CUDA torch, so it was rebuilt on classical CV: edges
for shelves, colour runs for facings. That reads geometry and colour, which is
what `sim/saliency.py` actually consumes - shelf level, facings, colour
contrast - so a shelf read off a video is genuinely simulatable. It reads no
product identities and detects no promotional signs, and
`vision/planogram.py` writes both of those absences into the document rather
than filling them in.

The stages are separate modules and not one function because each is
independently testable on a numpy array, which is how the whole pipeline is
tested without needing a video file at all.

**Bands are measured once, on the sharpest frame, and applied to all of them.**
Shelf edges do not move within a clip of a static bay, and re-detecting them
per frame produced bands that drifted by a pixel or two and then failed to
match under IoU - the tracking stage saw the same pack as a different facing in
every frame and confidence collapsed. Sharpest by Laplacian variance, because
a blurred frame gives blurred edges and one bad reading would set the geometry
for the whole clip.

**The camera is checked before the shelves are.** Both of the stages that came
from `vision/camera.py` were added after running this pipeline against its own
fixture with the degradations a phone adds, and both failures were silent. A
frame tilted a degree returned *fewer* shelves than the bay had rather than
refusing, and shelf level is the largest term in `sim/saliency.py`. A clip
panned across three bays of nine products returned over a hundred facings,
because a pack that has moved does not overlap itself at IoU 0.5, and it
returned them with confidences attached. Tilt is measured and undone, because
it can be; drift is measured and refused, because correcting it means
registering the frames into a mosaic and reading the shelf once across the
whole strip, which is a larger pipeline than this one.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vision.camera import (  # noqa: E402
    MAX_DRIFT_FRACTION,
    ROLL_SEARCH_DEGREES,
    camera_drift,
    deskew,
    estimate_roll,
    roll_is_saturated,
)
from vision.facings import Facing, facing_boxes  # noqa: E402
from vision.frames import Frame, VideoUnreadable, extract_frames  # noqa: E402
from vision.planogram import build_planogram  # noqa: E402
from vision.shelves import Band, shelf_bands  # noqa: E402
from vision.track import Track, merge_across_frames  # noqa: E402


@dataclass(frozen=True)
class Result:
    """Everything a run produced, including what it could not do.

    `notes` is not decoration. A pipeline that read three shelves out of a clip
    it sampled forty frames from should say so where somebody will read it,
    because the difference between "this store has three shelves" and "we found
    three" is the whole difference between a measurement and a guess.
    """

    planogram: Dict[str, Any]
    bands: List[Band]
    tracks_by_band: Dict[Tuple[int, int], List[Track]]
    frames_sampled: int
    # The rate the clip was sampled at, carried for the same reason
    # `roll_degrees` is: everything in this result was read off *those* frames,
    # and anything drawn over a different sampling of the same clip is a
    # picture of frames the numbers did not come from. `write_overlays` used to
    # re-sample at a hardcoded 2 fps while `run` took whatever `--fps` said, so
    # `--fps 5 --overlays` wrote boxes from one set of frames over the images
    # of another - and the only artefact anybody can audit the reading against
    # was the thing that was wrong. Carried rather than passed again so no
    # caller can get it wrong.
    frames_fps: float
    notes: List[str]
    # How far off level the clip was, and therefore how far every frame was
    # turned before anything was measured. Carried rather than discarded
    # because `bands` and every track box are in the *corrected* frame, so an
    # overlay drawn on a raw frame would not line up with the reading - and
    # because a shelf that needed four degrees of correction is not the same
    # evidence as one that needed none.
    roll_degrees: float


def sharpest(frames: Sequence[Frame]) -> Frame:
    """The least blurred frame, by variance of the Laplacian.

    Shelf geometry is read once, from this frame, and applied to the whole
    clip; a blurred frame gives blurred edges, and one bad reading would set
    the geometry for everything.
    """
    def sharpness(frame: Frame) -> float:
        grey = cv2.cvtColor(frame.image, cv2.COLOR_BGR2GRAY)
        return float(cv2.Laplacian(grey, cv2.CV_64F).var())

    return max(frames, key=sharpness)


def run(
    video_path: str | Path,
    *,
    fps: float = 2.0,
    max_frames: Optional[int] = 120,
    planogram_id: str = "video_aisle",
) -> Result:
    """Read `video_path` and return the planogram it supports.

    Raises `VideoUnreadable` for a file that cannot be opened and `ValueError`
    for a clip with nothing in it - the two are different problems with
    different fixes, and collapsing them would leave a user re-encoding a video
    that decoded perfectly well and simply showed no shelves.
    """
    frames = extract_frames(video_path, fps=fps, max_frames=max_frames)
    if not frames:
        raise ValueError(
            "the video opened but contained no frames, so there was nothing to read."
        )

    notes: List[str] = [
        f"{len(frames)} frames sampled at {fps} fps from {Path(video_path).name}",
    ]

    # Before anything is measured: did the camera stay put? Every stage after
    # this assumes it did, and none of them can tell that it did not.
    drift = camera_drift([frame.image for frame in frames])
    if drift > MAX_DRIFT_FRACTION:
        raise ValueError(
            f"the camera moves about {drift * 100:.1f}% of the frame between "
            f"sampled frames, and this pipeline reads a fixed shot (under "
            f"{MAX_DRIFT_FRACTION * 100:.0f}%). Facings are matched between "
            "frames by overlap, so a pack that travels does not overlap itself "
            "and the same product is counted again in every frame it appears "
            "in - a pan across an aisle reports several times the stock that is "
            "actually on it. Film each bay as a separate clip from a fixed "
            "position rather than walking the aisle."
        )

    reference = sharpest(frames)
    roll_degrees = estimate_roll(reference.image)
    if roll_is_saturated(roll_degrees):
        # The search hit its own edge, so the real tilt is at least this and
        # could be far more. Correcting by the clamped value does not fail
        # cleanly - it leaves a residual tilt and reads a shelf short, which
        # then spreads the remaining bands over the level enum and can drop
        # `eye`, the largest term in sim/saliency.py, out of a bay that has one.
        raise ValueError(
            f"the camera is at least {ROLL_SEARCH_DEGREES:.0f} degrees off level, "
            f"which is past what this pipeline can turn back (it searches "
            f"+/-{ROLL_SEARCH_DEGREES:.0f} degrees). Correcting by the edge of that "
            "range would leave the shelves still sloping and read fewer of them "
            "than the bay has, so the clip is refused rather than half-read. "
            "This pipeline reads a roughly front-on shot with the shelf edges "
            "running across the frame; hold the camera level, or crop and "
            "rotate the clip before feeding it in."
        )

    if roll_degrees != 0.0:
        # Every frame, by the one angle measured on the sharpest: the bands
        # come from that frame and are applied to all the others, so correcting
        # them individually would put each frame in its own geometry.
        frames = [
            Frame(index=frame.index, time_s=frame.time_s,
                  image=deskew(frame.image, roll_degrees))
            for frame in frames
        ]
        reference = next(
            frame for frame in frames if frame.index == reference.index
        )
        notes.append(
            f"the camera was {roll_degrees:+.2f} degrees off level; every frame "
            "was turned back by that before the shelves were looked for"
        )

    bands = shelf_bands(reference.image)
    notes.append(
        f"{len(bands)} shelf band(s) read from frame {reference.index}, "
        "the sharpest sampled frame, and applied to every frame"
    )

    if not bands:
        raise ValueError(
            "no shelf edges were found in any sampled frame. This pipeline reads "
            "a roughly front-on shot of a shelf bay with the shelf edges visible "
            "across most of the frame; a moving camera, a steep angle or heavy "
            "occlusion will defeat it."
        )

    per_band: Dict[Tuple[int, int], List[List[Facing]]] = {
        (band.top, band.bottom): [] for band in bands
    }
    for frame in frames:
        for band in bands:
            per_band[(band.top, band.bottom)].append(facing_boxes(frame.image, band))

    tracks_by_band: Dict[Tuple[int, int], List[Track]] = {
        key: merge_across_frames(frames_of_band)
        for key, frames_of_band in per_band.items()
    }

    total = sum(len(tracks) for tracks in tracks_by_band.values())
    notes.append(f"{total} product facing(s) found, deduped across frames at IoU 0.5")
    notes.append(
        "products are not identified: brand, name, price and promotion are not "
        "observable from video and are written as unknown rather than guessed"
    )
    notes.append(
        "no promotional signs are detected, so the planogram carries no ad slots"
    )

    height, width = reference.image.shape[:2]
    planogram = build_planogram(
        tracks_by_band,
        frame_width=width,
        frame_height=height,
        planogram_id=planogram_id,
    )

    return Result(
        planogram=planogram,
        bands=bands,
        tracks_by_band=tracks_by_band,
        frames_sampled=len(frames),
        frames_fps=fps,
        notes=notes,
        roll_degrees=roll_degrees,
    )


def draw_overlay(frame: np.ndarray, result: Result) -> np.ndarray:
    """`frame` with the detected bands and facings drawn on it.

    Committed alongside the planogram so a reader can check the reading against
    the picture instead of taking the JSON on trust - which is the only way
    anybody can audit a computer-vision step after the fact.
    """
    canvas = frame.copy()
    for band in result.bands:
        cv2.line(canvas, (0, band.top), (canvas.shape[1], band.top), (0, 200, 255), 2)

    for tracks in result.tracks_by_band.values():
        for track in tracks:
            x0, top, x1, bottom = (int(round(v)) for v in track.box)
            cv2.rectangle(canvas, (x0, top), (x1, bottom), (80, 220, 90), 2)
            cv2.putText(
                canvas,
                f"{track.confidence:.2f} x{track.seen_in}",
                (x0 + 3, top + 16),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.42,
                (80, 220, 90),
                1,
                cv2.LINE_AA,
            )
    return canvas


def write_overlays(video_path: str | Path, result: Result, out_dir: str | Path) -> List[Path]:
    """Draw the reading over the sampled frames and write them as PNGs.

    The clip is re-sampled at `result.frames_fps` and capped at
    `result.frames_sampled`, which between them reproduce exactly the frames
    `run` read - `extract_frames` samples by frame index, so the same rate and
    the same count give the same indices. Sampling at a rate of this
    function's own would draw the boxes over frames the boxes did not come
    from, which is the one thing these images must not do.

    The frames are turned back by the same angle `run` measured before they are
    drawn on. `result.bands` and every track box are in the corrected frame, so
    drawing them over the raw footage would put every box a few degrees away
    from the pack it belongs to - and these images exist precisely so somebody
    can check the reading against the picture.
    """
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)

    written: List[Path] = []
    for frame in extract_frames(
        video_path, fps=result.frames_fps, max_frames=result.frames_sampled
    ):
        path = directory / f"frame_{frame.index:05d}.png"
        levelled = deskew(frame.image, result.roll_degrees)
        cv2.imwrite(str(path), draw_overlay(levelled, result))
        written.append(path)
    return written


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--video", required=True, help="path to the aisle clip")
    parser.add_argument(
        "--out",
        default=None,
        help="where to write the planogram JSON (default: print it)",
    )
    parser.add_argument(
        "--overlays",
        default=None,
        help="directory to write annotated frames into, for auditing the reading",
    )
    parser.add_argument("--fps", type=float, default=2.0)
    parser.add_argument("--max-frames", type=int, default=120)
    parser.add_argument("--planogram-id", default="video_aisle")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        result = run(
            args.video,
            fps=args.fps,
            max_frames=args.max_frames,
            planogram_id=args.planogram_id,
        )
    except (VideoUnreadable, ValueError) as exc:
        print(f"vision: {exc}", file=sys.stderr)
        return 1

    for note in result.notes:
        print(f"  {note}", file=sys.stderr)

    document = json.dumps(result.planogram, indent=2) + "\n"
    if args.out is None:
        print(document, end="")
    else:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(document, encoding="utf-8")
        print(f"wrote {args.out}", file=sys.stderr)

    if args.overlays is not None:
        written = write_overlays(args.video, result, args.overlays)
        print(f"wrote {len(written)} overlay frame(s) to {args.overlays}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
