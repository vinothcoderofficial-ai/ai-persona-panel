"""Re-apply the session gate to one session rejected by the removed duration floor.

    python scripts/regate_session.py <session_id> --dry-run
    python scripts/regate_session.py <session_id>

**This rewrites a verdict that has already been recorded.** That is the most
dangerous operation in this repository short of editing a prediction lock, and
the design below is almost entirely about making it impossible to do quietly.

## Why it exists

The gate used to reject a session shorter than 45 seconds. That floor was a
proxy for "saw enough shelf", and a biased one: a shopper with a list who knows
the brand finishes in half a minute, so the floor discarded the `mission`
archetype preferentially - one of the four personas the panel exists to
validate. It was replaced by a rule that counts the shelf actually looked at
(see `web/src/capture/SessionGate.ts` and METHODOLOGY.md 2.3).

## Why it is uncomfortable

The session that exposed the flaw is also the session the new rule accepts. So
running this is applying a new rule to re-admit the very datapoint that
motivated writing it. That is the shape of a p-hack even when the reasoning is
sound, and no amount of care in this file makes it not that shape. What care
can do is make it visible: the `regated` block this stamps into the session
travels with the evidence into `data/sessions/anon/`, `scripts/eval.py` counts
it, and RESULTS.md prints it next to the panel size. A reader who wants to
discount the session can see that they need to.

## Why it is a script and not an endpoint

`POST /sessions/{id}/finish` is called by a browser. A browser must never be
able to claim its own session was re-gated, so `regated` is not in
`_FINISH_FIELDS` and this writes to the database directly - the same posture as
`scripts/anonymise_sessions.py`, and for the same reason: it produces committed
evidence, so it belongs at a terminal where it is deliberate and reviewable.

## The duplicated clause

`slots_observed` below re-implements one clause of the browser's gate, and that
duplication is a real cost. It is here because sessions finished before the
rule existed have no `slots_observed` in their quality block, so the number has
to be recovered from the events. It is confined to `scripts/` deliberately: it
is not on any production path, `api/app/` and `analytics/` do not import it,
and the live gate remains the browser's alone. `scripts/tests/` pins it against
the same cases `web/tests/sessionGate.test.ts` pins the original against; if
the two ever disagree, the browser is right.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from sqlmodel import Session, select

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.app.db import EventRecord, SessionRecord, get_validator  # noqa: E402

# The only verdict this script will correct. Every other reject reason was
# produced by a rule that still stands.
CORRECTABLE_REASON = "too_short"

# Mirrors MIN_OBSERVED_SLOTS in web/src/capture/SessionGate.ts.
MIN_OBSERVED_SLOTS = 6

# Mirrors LOOKING_EVENT_TYPES, which mirrors the non-zero channels in
# analytics/fusion.py's _MODE_WEIGHTS. Fixations are weighted 0 in cursor_only,
# so a cursor-only session must not be re-admitted on evidence the attention
# formula will then discard.
LOOKING_EVENT_TYPES: Dict[str, tuple] = {
    "cursor_only": ("cursor_dwell",),
    "webcam": ("fixation", "cursor_dwell"),
}

NOTE = (
    "Re-gated after the duration floor was removed. This session was rejected "
    "as too_short (28.9 s against a 45 s minimum); that floor was a biased "
    "proxy for shelf coverage which preferentially discarded the mission "
    "archetype, and was replaced by a rule counting slots actually looked at. "
    "Disclosed because this session is what motivated the rule change: see "
    "METHODOLOGY.md 2.3."
)


class RefuseToRegate(Exception):
    """Raised rather than rewriting a verdict this script should not touch."""


@dataclass
class Outcome:
    session_id: str
    slots_observed: int
    accepted: bool
    written: bool


def slots_observed(events: Iterable[Mapping[str, Any]], mode: str) -> int:
    """Distinct slots that produced a looking observation, per mode.

    The duplicated clause; see the module docstring. A null or missing
    `slot_id` is skipped exactly as `analytics/fusion.py` skips it - a look at
    bare shelf between products belongs to no slot and enters no denominator,
    so it is not evidence that a slot was seen.
    """
    looking = LOOKING_EVENT_TYPES.get(mode)
    if looking is None:
        raise RefuseToRegate(f"unknown capture mode {mode!r}")

    seen = set()
    for event in events:
        if event.get("type") not in looking:
            continue
        slot_id = (event.get("payload") or {}).get("slot_id")
        if isinstance(slot_id, str) and slot_id:
            seen.add(slot_id)
    return len(seen)


def _load(engine, session_id: str) -> tuple[SessionRecord, Dict[str, Any], List[Dict]]:
    with Session(engine) as db:
        record = db.exec(
            select(SessionRecord).where(SessionRecord.session_id == session_id)
        ).one_or_none()
        if record is None:
            raise RefuseToRegate(f"no session {session_id!r} in the database")
        events = [
            json.loads(row.data)
            for row in db.exec(
                select(EventRecord).where(EventRecord.session_id == session_id)
            ).all()
        ]
        return record, json.loads(record.data), events


def _rule_commit() -> str:
    """The commit the current gate came from, recorded in the disclosure.

    Best effort: a repository without git history still re-gates, it just says
    so less precisely. Lying about provenance would be worse than omitting it.
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def regate(
    engine,
    session_id: str,
    *,
    rule_commit: Optional[str] = None,
    dry_run: bool = False,
) -> Outcome:
    """Re-apply the current gate to one session, or refuse and say why."""
    record, stored, events = _load(engine, session_id)

    if stored.get("regated") is not None:
        raise RefuseToRegate(
            f"{session_id} has already been re-gated "
            f"(at {stored['regated'].get('at')}); it will not be done twice"
        )
    if stored.get("accepted"):
        raise RefuseToRegate(f"{session_id} was already accepted; nothing to correct")

    reason = stored.get("reject_reason")
    if reason != CORRECTABLE_REASON:
        raise RefuseToRegate(
            f"{session_id} was rejected as {reason!r}, not {CORRECTABLE_REASON!r}. "
            "Only the removed duration floor is corrected here - every other "
            "reason came from a rule that still stands, and re-running the gate "
            "over it would be re-litigating a verdict rather than correcting one."
        )

    observed = slots_observed(events, stored.get("mode", ""))
    if observed < MIN_OBSERVED_SLOTS:
        raise RefuseToRegate(
            f"{session_id} looked at {observed} slot(s), under the {MIN_OBSERVED_SLOTS} "
            "the current gate requires. Removing the duration floor does not make "
            "every short session evidence; this one stays rejected."
        )

    if dry_run:
        return Outcome(session_id, observed, accepted=True, written=False)

    stored["accepted"] = True
    stored["reject_reason"] = None
    quality = dict(stored.get("quality") or {})
    quality["slots_observed"] = observed
    stored["quality"] = quality
    stored["regated"] = {
        "at": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
            "+00:00", "Z"
        ),
        "from_accepted": False,
        "from_reject_reason": CORRECTABLE_REASON,
        "rule_commit": rule_commit or _rule_commit(),
        "note": NOTE,
    }

    # The corpus exporter treats a session it cannot validate as a build
    # failure, so a document this writes that the schema refuses would break
    # `make collect` later rather than here.
    errors = sorted(get_validator("session.schema.json").iter_errors(stored), key=str)
    if errors:
        raise RefuseToRegate(
            f"the re-gated session does not match session.schema.json: {errors[0].message}"
        )

    with Session(engine) as db:
        row = db.exec(
            select(SessionRecord).where(SessionRecord.session_id == session_id)
        ).one()
        row.data = json.dumps(stored)
        db.add(row)
        db.commit()

    return Outcome(session_id, observed, accepted=True, written=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("session_id")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would change without writing it",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    from api.app import db as db_module

    try:
        outcome = regate(db_module.engine, args.session_id, dry_run=args.dry_run)
    except RefuseToRegate as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1

    verb = "would accept" if args.dry_run else "accepted"
    print(f"{verb} {outcome.session_id}: {outcome.slots_observed} slots observed")
    if outcome.written:
        print("A `regated` block was stamped into the session; it will travel with")
        print("the evidence into data/sessions/anon/ and be counted in RESULTS.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
