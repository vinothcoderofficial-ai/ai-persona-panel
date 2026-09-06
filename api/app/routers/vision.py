"""POST /vision/planogram - upload a clip, get a shelf (S30).

`vision/pipeline.py` turns a video into a planogram at a terminal. This puts the
same pipeline behind an upload, so "input video, get a 3D store" is something a
person can do rather than something the repository merely contains.

Three properties this endpoint exists to preserve, all of them the pipeline's
and none of them invented here:

* **It refuses rather than guesses.** A clip with no shelf edges in it comes
  back 422 with the pipeline's own explanation of what kind of shot it reads.
  Returning a plausible planogram for a video of a wall would be the worst
  failure available: once it is JSON, nothing downstream can tell it from a
  real shelf.
* **It carries the notes.** The pipeline states what it did not observe -
  brands, names, prices, promotions, promotional signs - and that half must not
  be dropped at the HTTP boundary, because it is the half that stops the
  document being over-read.
* **Reading is not saving.** The planogram comes back for a person to look at
  and is not written to the database. An upload that quietly added a store the
  simulator could then be run against would put a video's guesses next to
  hand-authored evidence with nothing distinguishing them.

The upload is written to a temporary file because OpenCV decodes from a path
rather than from bytes, and it is removed on the way out whatever happened.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, File, HTTPException, UploadFile

from vision.frames import VideoUnreadable
from vision.pipeline import run

router = APIRouter(tags=["vision"])

# A shelf clip is seconds long. This is a bound on one request's work, not a
# judgement about the footage - the pipeline samples at 2 fps and caps frames
# anyway, so a longer file is read partially rather than refused.
MAX_UPLOAD_BYTES = 200 * 1024 * 1024


@router.post("/vision/planogram")
async def post_vision_planogram(video: UploadFile = File(...)) -> Dict[str, Any]:
    """Read an uploaded aisle clip into a planogram document.

    422 for a file that cannot be decoded, and 422 for one that decodes but
    holds no shelves - both are "this video is not what this pipeline reads",
    which is the client's problem to fix, and each carries the sentence that
    says which.
    """
    payload = await video.read()
    if not payload:
        raise HTTPException(status_code=422, detail="the uploaded file was empty")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"the upload is {len(payload) // (1024 * 1024)} MB; this endpoint "
                f"reads up to {MAX_UPLOAD_BYTES // (1024 * 1024)} MB. A few seconds "
                "of a shelf bay is all the pipeline uses."
            ),
        )

    suffix = Path(video.filename or "upload.mp4").suffix or ".mp4"
    handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        handle.write(payload)
        handle.close()

        try:
            result = run(handle.name)
        except VideoUnreadable as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ValueError as exc:
            # "No shelves in it" and "no facings on them" both land here, and
            # both are refusals with their own explanation attached.
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        return {
            "planogram": result.planogram,
            "frames_sampled": result.frames_sampled,
            "bands": [{"top": band.top, "bottom": band.bottom} for band in result.bands],
            "notes": result.notes,
            # Said out loud in the payload as well as in this docstring: the
            # screen shows it, so nobody has to infer it from the absence of an
            # id in /planograms.
            "saved": False,
        }
    finally:
        Path(handle.name).unlink(missing_ok=True)
