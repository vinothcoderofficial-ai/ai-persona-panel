"""Export the live session database into `data/sessions/anon/` for `make eval`.

S21's half of the collection loop. `scripts/collect_link.py` hands out the
links; this turns what came back into the committed corpus `scripts/eval.py`
reads.

    python scripts/anonymise_sessions.py                 # to data/sessions/anon/
    python scripts/anonymise_sessions.py --out /tmp/x --dry-run

The binding contract is `scripts/eval.py:load_sessions`. eval treats a session
file it cannot read as a **build failure**, not a skipped file, so everything
written here is validated against `schemas/session.schema.json` and
`schemas/event.schema.json` before it reaches the disk. A session that fails
raises rather than being quietly dropped: silently shrinking the panel would
change every metric in RESULTS.md without telling anyone.

What this does and does not anonymise, because the honest answer is "less than
the filename suggests":

  * **Dropped:** every field not in `session.schema.json`. The `sessions` table
    stores raw JSON, so anything an operator or a future patch adds to a live
    row -- a note, an address, a header -- is left behind rather than carried
    into a committed file. The schema is the whitelist.
  * **Withheld entirely:** any session with `consent: false`. Not exported,
    not counted in the panel, not present as a reject reason. This means the
    reject-reason histogram under-reports `no_consent` by construction, which
    is the right trade: a person who declined does not get their behaviour
    committed to a public repository so that a bar chart can be complete.
  * **NOT rewritten: `session_id`.** It is `crypto.randomUUID()` from the
    browser (web/src/main.tsx) and carries nothing about the person. Re-keying
    it would also mean rewriting `predictions/{session_id}.json`, and those
    locks are the evidence the whole project rests on -- editing them after the
    fact to tidy a filename is exactly the move the pre-registration exists to
    make impossible.
  * **NOT coarsened: `started_at` and `ended_at`.** eval's ordering check
    compares the lock's `created_at` against `started_at` plus the first
    event's `t_ms`. Rounding those timestamps would either destroy that check
    or make it lie, so they stay exact. They are a genuine quasi-identifier
    when read next to an office calendar; that residual risk is recorded here
    rather than papered over.

So this is a whitelisting exporter with a consent gate, not a de-identifier.
The reason the corpus is safe to commit is that nothing identifying is ever
collected: the intake is three booleans, and per CLAUDE.md only `{x,y,conf,t}`
ever leaves the browser -- no frames, ever.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlmodel import Session, select  # noqa: E402

from api.app.db import EventRecord, SessionRecord, get_validator  # noqa: E402

DEFAULT_OUT = ROOT / "data" / "sessions" / "anon"

# A session id becomes a filename and the key of a prediction lock. Real ones
# are UUIDs; this refuses the shapes that would mean a human typed something.
OPAQUE_ID = re.compile(r"^[A-Za-z0-9._-]+$")


class AnonymiseError(Exception):
    """Base for every refusal. Nothing is written when one is raised."""


class NotAnonymous(AnonymiseError):
    """A session id that could identify the shopper."""


class InvalidSession(AnonymiseError):
    """A session or event that does not match its schema."""


@dataclass(frozen=True)
class Report:
    exported: int
    accepted: int
    rejected: int
    undecided: int
    withheld_no_consent: int
    events: int

    def summary(self) -> str:
        lines = [
            f"exported {self.exported} session(s): {self.accepted} accepted, "
            f"{self.rejected} rejected, {self.undecided} undecided",
            f"events written: {self.events}",
        ]
        if self.withheld_no_consent:
            lines.append(
                f"withheld {self.withheld_no_consent} session(s) with consent: false "
                "-- not exported in any form, so the no_consent reject reason is "
                "under-reported by exactly this many"
            )
        return "\n".join(lines)


def _schema_fields() -> tuple:
    """The session schema's property names -- the whitelist, read not retyped."""
    schema = json.loads(
        (ROOT / "schemas" / "session.schema.json").read_text(encoding="utf-8")
    )
    return tuple(schema["properties"])


def _errors(validator, document: Mapping) -> list:
    return [
        f"{'/'.join(str(p) for p in error.path)}: {error.message}"
        for error in sorted(validator.iter_errors(document), key=str)
    ]


def anonymise_session(raw: Mapping, fields: Iterable) -> dict:
    """The row reduced to schema fields only. Everything else is left behind."""
    return {key: raw[key] for key in fields if key in raw}


def _read_rows(engine):
    with Session(engine) as db:
        sessions = [json.loads(row.data) for row in db.exec(select(SessionRecord)).all()]
        events: dict = {}
        for row in db.exec(select(EventRecord)).all():
            events.setdefault(row.session_id, []).append(json.loads(row.data))
    return sessions, events


def export(engine, out_dir, *, dry_run: bool = False) -> Report:
    """Write every consented session in `engine` to `out_dir`, one file each.

    Everything is validated before anything is written, so a corpus is never
    left half-updated by a session that turns out to be malformed.
    """
    out_dir = pathlib.Path(out_dir)
    fields = _schema_fields()
    session_validator = get_validator("session.schema.json")
    event_validator = get_validator("event.schema.json")

    raw_sessions, raw_events = _read_rows(engine)

    withheld = 0
    documents = []

    for raw in sorted(raw_sessions, key=lambda doc: str(doc.get("session_id", ""))):
        session_id = str(raw.get("session_id", ""))
        if not session_id:
            raise InvalidSession("a session row has no session_id")
        if not OPAQUE_ID.match(session_id):
            raise NotAnonymous(
                f"session_id {session_id!r} is not an opaque token. Real ids are "
                "crypto.randomUUID() from the browser; this one looks typed, and it "
                "would become a committed filename and a prediction-lock key."
            )

        if raw.get("consent") is not True:
            withheld += 1
            continue

        session = anonymise_session(raw, fields)
        problems = _errors(session_validator, session)
        if problems:
            raise InvalidSession(
                f"{session_id}: does not match session.schema.json: "
                + "; ".join(problems)
            )

        events = sorted(raw_events.get(session_id, []), key=lambda e: e.get("t_ms", 0))
        for index, event in enumerate(events):
            problems = _errors(event_validator, event)
            if problems:
                raise InvalidSession(
                    f"{session_id}: event {index} does not match event.schema.json: "
                    + "; ".join(problems)
                )

        documents.append((session_id, {"session": session, "events": events}))

    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        for session_id, document in documents:
            path = out_dir / f"{session_id}.json"
            path.write_text(
                json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                encoding="utf-8",
                newline="\n",
            )

    accepted = sum(1 for _, doc in documents if doc["session"].get("accepted") is True)
    rejected = sum(1 for _, doc in documents if doc["session"].get("accepted") is False)
    return Report(
        exported=len(documents),
        accepted=accepted,
        rejected=rejected,
        undecided=len(documents) - accepted - rejected,
        withheld_no_consent=withheld,
        events=sum(len(doc["events"]) for _, doc in documents),
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT)
    parser.add_argument("--dry-run", action="store_true",
                        help="validate and report, write nothing")
    args = parser.parse_args(argv)

    from api.app import db as db_module

    try:
        report = export(db_module.engine, args.out, dry_run=args.dry_run)
    except AnonymiseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(report.summary())
    if args.dry_run:
        print(f"(dry run -- nothing written to {args.out})")
    else:
        print(f"written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
