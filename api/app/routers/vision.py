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

That third property is the one that made this endpoint feel like a dead end, so
it is worth saying exactly what changed and what did not. `#/vision` now lets an
operator label the reading and POST it to `/planograms` themselves. This handler
takes no part in that: it accepts no labels and still writes nothing. Passing
the operator's typing back through here would give the same document two write
paths - one that saves what the camera measured and one that saves what a person
typed on top - and the whole point is that those two stay distinguishable. The
screen holds the reading it was given, applies the labels to it, and posts the
result to the endpoint that already exists for storing planograms, which
validates it against the same schema every other store goes through.

What this endpoint *did* gain is the vocabulary the labelling step needs, and
that genuinely has to come from the server: it is the union of `goal_categories`
over the committed policy of every persona in `data/personas/`, both of which
are files on this machine. It sits beside the notes about what the camera could
not see because it is the same sentence - here is what was not observed, and
here is the only set of words a person could supply instead that the simulator
would act on.

Why it matters, measured rather than assumed: run the four committed policies
against a nine-facing video reading and `path.stations_mean` is 0.0 for
loyalist, mission and switcher. They never take a step. `sim/simulator.py`
keeps a shopper active only while their `goals` are unmet, and goals are matched
against the store's category list, which on a video reading holds the single
string "unknown". Browser walks the bay - it is the one archetype allowed to
shop without goals - and buys nothing, because a purchase needs a goal match
too. Give those same nine facings categories drawn from the policies and all
four walk and all four buy. A dropdown of free text would not have fixed it; a
dropdown of the categories the personas are actually going after does.

The upload is written to a temporary file because OpenCV decodes from a path
rather than from bytes, and it is removed on the way out whatever happened.

The read itself runs on a worker thread. Decoding a minute of video and
segmenting 120 frames is about thirteen seconds of CPU-bound work, and called
straight from this coroutine it would hold the event loop for all of it - so a
single person trying the vision demo would stall every other request on the
server, including a live session flushing gaze over the websocket in the next
room. Those samples are not recoverable, which makes this a data-loss bug
rather than a slow page.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, File, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from vision.frames import VideoUnreadable
from vision.pipeline import run

router = APIRouter(tags=["vision"])

ROOT = Path(__file__).resolve().parents[3]
# The same two directories `api/app/routers/ai.py` reads, deliberately named
# again rather than imported from it: this router has no other business with the
# AI layer, and one import would put the LLM client in the vision screen's
# dependency graph for the sake of two paths.
PERSONAS_DIR = ROOT / "data" / "personas"
POLICIES_DIR = ROOT / "data" / "cache" / "policies"

# A shelf clip is seconds long. This is a bound on one request's work, not a
# judgement about the footage - the pipeline samples at 2 fps and caps frames
# anyway, so a longer file is read partially rather than refused.
MAX_UPLOAD_BYTES = 200 * 1024 * 1024


def _shoppable_categories() -> List[str]:
    """Every category some persona is actually going after, sorted.

    Derived, never listed. A constant here would agree with the personas on the
    day it was typed and drift the moment `make seed` or an LLM re-ask moved a
    `goal_categories` array, and the failure would be silent in the worst
    direction: the screen would keep offering a word the simulator no longer
    honours, and the operator's labelled store would sit there being ignored by
    every persona for a reason nothing on screen could explain.

    Persona documents themselves carry `archetype`, `share_of_population` and a
    description - not categories. What a persona shops lives in its committed
    policy in `data/cache/policies/{persona_id}_{planogram_id}.json`, which is
    the file `sim/simulator.py` reads, so that is what is unioned here. The
    persona directory still decides *who* counts, so a persona nobody has
    generated a policy for contributes nothing rather than being invented.

    Every failure mode is absorbed: no persona directory, no policy for a
    persona, a policy that is unreadable or has no `goal_categories`. The
    vocabulary is a convenience on top of the reading, and it must never be the
    thing that turns a successful upload into a 500.
    """
    if not PERSONAS_DIR.exists():
        return []

    categories: set[str] = set()
    for persona_path in sorted(PERSONAS_DIR.glob("*.json")):
        try:
            persona_id = json.loads(persona_path.read_text(encoding="utf-8"))["persona_id"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        # Non-recursive on purpose: `POLICIES_DIR/preview/` holds the answers a
        # re-ask produced and deliberately did not adopt (see ai.py), and a
        # preview must not widen what the screen offers.
        for policy_path in sorted(POLICIES_DIR.glob(f"{persona_id}_*.json")):
            try:
                policy = json.loads(policy_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            goals = policy.get("goal_categories")
            if isinstance(goals, list):
                categories.update(str(c) for c in goals)

    return sorted(categories)


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
            # Off the event loop: see the module docstring. `run` is read from
            # the module at call time, so a test may still substitute it.
            result = await run_in_threadpool(run, handle.name)
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
            # The words an operator can label this reading with and have any
            # persona act on. Read off the personas and their committed
            # policies; see `_shoppable_categories`.
            "shoppable_categories": _shoppable_categories(),
            # Said out loud in the payload as well as in this docstring: the
            # screen shows it, so nobody has to infer it from the absence of an
            # id in /planograms. Still false, and still nothing written here -
            # the operator saves through /planograms, deliberately.
            "saved": False,
        }
    finally:
        Path(handle.name).unlink(missing_ok=True)
