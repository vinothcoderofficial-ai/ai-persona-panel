"""GET /collection/status - what the real panel actually contains (S31).

`RESULTS.md` has said "not yet collected" for every real-panel number since the
project started, and the only way to find out why was to open `shoppertwin.db`
in a SQL client. An operator running a panel of people could not see it filling
up, could not see what people were being rejected for, and could not tell
whether what they had collected had reached the committed corpus at all. The
first real session was rejected for lasting 29 seconds against a 45-second
minimum, and nobody found out until the run was over and the session was
already evidence. That floor has since been replaced by the shelf-coverage rule
the gate now applies (see `web/src/capture/SessionGate.ts`), which is why this
screen reports `min_observed_slots` and no duration at all.

The endpoint reports **three things that are easy to conflate**, and keeping
them apart is most of its value:

* **live** - everyone who has shopped, in the database, whatever became of them.
* **committed** - what is in `data/sessions/anon/`, which is the only thing
  `scripts/eval.py` reads. Collected is not committed: a panel that showed one
  number for both would let somebody believe the evidence was in the repository
  when it was still in SQLite.
* **locks** - the pre-registration files in `predictions/`.

It also reports the acceptance thresholds, so a rejection is diagnosable here
rather than by reading `web/src/capture/SessionGate.ts`, and names the two
commands that move a session from the first bucket to the second.

Read-only, deliberately. Exporting writes committed evidence, and that belongs
at a command line where it is deliberate and reviewable - not behind a button
on a page that anything on the network could POST to.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from api.app import prediction
from api.app.db import ROOT, SessionRecord, get_session

router = APIRouter(tags=["collection"])

ANON_DIR = ROOT / "data" / "sessions" / "anon"

EXPORT_COMMAND = "python scripts/anonymise_sessions.py"
EVAL_COMMAND = "python scripts/eval.py"

# Mirrors web/src/capture/SessionGate.ts. Duplicated across the language
# boundary because the gate has to run in the browser at checkout - there is no
# server round trip there - and reported here because a rejection an operator
# cannot diagnose is one they will collect again. web/tests/sessionGate.test.ts
# pins the browser side; if these ever disagree, that is a real bug and this
# comment is where to start.
GATE = {
    "min_observed_slots": 6,
    "min_stations": 2,
    "min_interactions": 1,
    "min_fixation_coverage": 0.4,
}


def _committed_session_ids() -> set:
    """Session ids present in the exported corpus, by filename.

    `scripts/anonymise_sessions.py` writes one file per session named for its
    id, and deliberately does not rewrite the id - see that module's docstring
    on why re-keying would mean editing prediction locks after the fact.
    """
    if not ANON_DIR.exists():
        return set()
    return {path.stem for path in ANON_DIR.glob("*.json")}


@router.get("/collection/status")
def get_collection_status(session: Session = Depends(get_session)) -> Dict[str, Any]:
    """The state of the real panel: collected, committed, and why not accepted."""
    documents = [
        json.loads(record.data) for record in session.exec(select(SessionRecord)).all()
    ]

    accepted = 0
    rejected = 0
    undecided = 0
    no_consent = 0
    reject_reasons: Dict[str, int] = {}
    accepted_ids = set()

    for document in documents:
        # Counted first and excluded from everything else: a session that
        # declined consent is never exported in any form, so it can never enter
        # the panel and is not a rejection. The reject histogram therefore
        # under-reports `no_consent` by exactly this many, which is the same
        # trade `scripts/anonymise_sessions.py` documents.
        if document.get("consent") is not True:
            no_consent += 1
            continue

        if document.get("accepted") is True:
            accepted += 1
            accepted_ids.add(document["session_id"])
        elif document.get("accepted") is False:
            rejected += 1
            reason = document.get("reject_reason") or "unrecorded"
            reject_reasons[reason] = reject_reasons.get(reason, 0) + 1
        else:
            # Still shopping, or closed the tab. Not a rejection: nothing has
            # judged it yet, and reporting it as one would inflate the reject
            # histogram with people who simply have not finished.
            undecided += 1

    committed = _committed_session_ids()
    # `prediction.PREDICTIONS_DIR`, read at call time, and not a second constant
    # of this module's own: that is the directory the server actually writes to,
    # it is what the test suite redirects, and a private copy here counted the
    # repository's real locks during a test that believed it was isolated.
    # Non-recursive on purpose - `predictions/dev/` holds rehearsal locks, which
    # are gitignored and invisible to scripts/eval.py, so counting them here
    # would inflate the evidence.
    locks_dir = prediction.PREDICTIONS_DIR
    locks = len(list(locks_dir.glob("*.json"))) if locks_dir.exists() else 0

    return {
        "live": {
            "total": len(documents),
            "accepted": accepted,
            "rejected": rejected,
            "undecided": undecided,
            "no_consent": no_consent,
            "reject_reasons": reject_reasons,
        },
        "committed": {
            "sessions": len(committed),
            "directory": str(Path("data/sessions/anon")),
        },
        "locks": locks,
        # True when the database holds an accepted session the corpus does not.
        # Collected is not committed, and eval reads only the second.
        "export_needed": bool(accepted_ids - committed),
        "export_command": EXPORT_COMMAND,
        "eval_command": EVAL_COMMAND,
        "gate": GATE,
    }
