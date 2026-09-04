"""The prediction lock: the synthetic prediction, fixed before the human shops.

This is the project's central scientific claim, so the ordering is enforced
structurally rather than by convention (CLAUDE.md):

  * `POST /sessions` calls `write_lock()` BEFORE it writes the session row, so
    a session that exists always has a lock. The file is written to a temp name
    and `os.replace`d into place, so a lock file is either complete or absent -
    never a half-written document that only looks like evidence.
  * `POST /sessions/{id}/events` and `ws/session/{id}` refuse a session with no
    lock (`lock_exists()`), so no event can ever be recorded ahead of the
    commitment it is going to be judged against.
  * A lock is never rewritten. Re-registering a session reuses the existing
    file, because re-timestamping a commitment after events had been recorded
    would destroy the very thing the lock is evidence of.

The document is schemas/prediction.schema.json (SPEC 4.6).


The hash recipe - scripts/eval.py (S19) must reproduce this exactly
------------------------------------------------------------------
`sha256` is the SHA-256 hexdigest of the UTF-8 encoding of

    json.dumps(
        {
            "population_fixation_prob": <the lock's population_fixation_prob>,
            "sim_run_id": <the lock's sim_run_id>,
            "created_at": <the lock's created_at>,
        },
        sort_keys=True,
        separators=(",", ":"),
    )

Three fields, no more: `prediction_id`, `session_id`, `variant_id` and
`git_commit` are metadata about the lock, not the prediction, and `sha256`
itself is of course not part of its own payload. `sort_keys=True` and the
compact separators are what "canonical" means here - the same recipe
`api/app/simcache.py:canonical()` uses - so the digest does not depend on dict
ordering or on how the file was serialised. Verification is therefore: load
the JSON, rebuild that payload from the stored values, hash it, compare.


created_at, and what it can honestly be compared against
--------------------------------------------------------
`created_at` is UTC ISO-8601 with milliseconds and a trailing `Z`
("2026-09-14T10:32:07.412Z"), matching SPEC 4.6.

Events carry `t_ms`, which is an offset from the start of the session, NOT a
wall clock. So `created_at` cannot be compared against any event's timestamp,
and eval.py must not try. What it CAN check:

  * every session has a lock file at `predictions/{session_id}.json`;
  * `lock.session_id`, `lock.variant_id` and `session.prediction_id` agree with
    the session document;
  * `created_at <= session.ended_at` for a finished session;
  * `sha256` recomputes from the stored fields.

`created_at` is *later* than `session.started_at` by construction - the browser
stamps `started_at` before it calls POST /sessions - so asserting the reverse
would fail on every honest session. The real guarantee that no event predates
the lock is the structural one above: the events endpoint and the ingest socket
both refuse a session with no lock.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from api.app import simcache
from api.app.db import ROOT

# Read at call time (never captured at import), so tests can redirect the whole
# module at a tmp directory the way conftest.py redirects db.engine. The real
# folder holds committed evidence and must never collect test artefacts.
PREDICTIONS_DIR = ROOT / "predictions"

# The locked prediction is the expensive, high-resolution one: 10,000 synthetic
# shoppers per persona at a fixed seed (docs/PLAN.md 11 - "N = 5,000 for
# what-if, 10,000 for locked predictions"). The seed matches the rest of the
# API so two sessions on one variant lock the identical prediction.
N_SYNTH = 10_000
SEED = 42

_GIT_DIR = ROOT / ".git"


# ---------------------------------------------------------------------------
# Paths and existence - the ordering check the events endpoint calls
# ---------------------------------------------------------------------------


def _directory(predictions_dir: Optional[Path]) -> Path:
    return Path(predictions_dir) if predictions_dir is not None else PREDICTIONS_DIR


def lock_path(session_id: str, *, predictions_dir: Optional[Path] = None) -> Path:
    return _directory(predictions_dir) / f"{session_id}.json"


def lock_exists(session_id: str, *, predictions_dir: Optional[Path] = None) -> bool:
    """Is this session allowed to record events yet?"""
    return lock_path(session_id, predictions_dir=predictions_dir).is_file()


def read_lock(session_id: str, *,
              predictions_dir: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """The locked prediction for this session, or None if it has none."""
    path = lock_path(session_id, predictions_dir=predictions_dir)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Writing the lock
# ---------------------------------------------------------------------------


def write_lock(session_id: str, variant_id: str, resolved_planogram: Dict[str, Any], *,
               predictions_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Snapshot the current population prediction for `variant_id` and lock it.

    Returns the SPEC 4.6 document, whether it was just written or already
    existed: a lock is written once and never revised, so calling this again
    for a session that already has one returns the original file untouched.
    """
    existing = read_lock(session_id, predictions_dir=predictions_dir)
    if existing is not None:
        return existing

    bundle = simcache.population(resolved_planogram, variant_id, n_synth=N_SYNTH, seed=SEED)
    population_fixation_prob = {
        slot_id: float(bundle.population["fixation_prob"].get(slot_id, 0.0))
        for slot_id in occupied_slot_ids(resolved_planogram)
    }
    sim_run_id = bundle.population["sim_run_id"]
    created_at = utc_now_iso()

    document = {
        "prediction_id": str(uuid.uuid4()),
        "session_id": session_id,
        "variant_id": variant_id,
        "sim_run_id": sim_run_id,
        "created_at": created_at,
        "population_fixation_prob": population_fixation_prob,
        "sha256": compute_sha256(population_fixation_prob, sim_run_id, created_at),
        "git_commit": git_commit(),
    }

    _write_atomically(lock_path(session_id, predictions_dir=predictions_dir), document)
    return document


def occupied_slot_ids(planogram: Dict[str, Any]) -> List[str]:
    """The slot vocabulary the lock commits to, in planogram order.

    Occupied product slots only. Two things are deliberately left out:

    * Empty slots (`sku_id: null`) are never a fixation target, so a prediction
      for them would be a constant zero on both sides and would only dilute the
      Spearman.
    * Ad slots appear in a SimResult's `fixation_prob` too, but they are a
      different question with their own metric (`ad_slot_index_spearman`) and
      their own SimResult field (`ad_slot_attention`). The browser reports a
      look at an ad as `ad_slot_id`, not `slot_id`, so `analytics/fusion.py`
      never scores one - including them here would put a permanently-zero real
      entry against a non-zero synthetic one and quietly bias every live
      agreement number downwards.

    This is the same vocabulary POST /experiments compares over, so a live
    Spearman and an offline one are measured on the same slots.
    """
    return [
        slot["slot_id"]
        for bay in planogram["bays"]
        for shelf in bay["shelves"]
        for slot in shelf["slots"]
        if slot["sku_id"] is not None
    ]


def compute_sha256(population_fixation_prob: Dict[str, float], sim_run_id: str,
                   created_at: str) -> str:
    """The lock's `sha256`. See the module docstring for the exact recipe."""
    payload = simcache.canonical({
        "population_fixation_prob": population_fixation_prob,
        "sim_run_id": sim_run_id,
        "created_at": created_at,
    })
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def utc_now_iso() -> str:
    """Now, as UTC ISO-8601 with exactly three decimal places and a `Z`.

    `datetime.isoformat()` gives microseconds (or drops the fraction entirely
    when it happens to be zero) and writes the offset as `+00:00`, neither of
    which matches the SPEC 4.6 example, so the format is written out here.
    """
    now = datetime.now(timezone.utc)
    return f"{now.strftime('%Y-%m-%dT%H:%M:%S')}.{now.microsecond // 1000:03d}Z"


def git_commit() -> Optional[str]:
    """The repository's current commit, short form, for provenance.

    Read straight off `.git` rather than shelled out to, so writing a lock
    costs no subprocess. Returns None - never a placeholder - when the commit
    cannot be determined (no `.git`, a fresh repo with an unborn HEAD, an
    exported source tree); schemas/prediction.schema.json allows null for
    exactly that case, and a fabricated value would make the lock's provenance
    a lie.
    """
    try:
        head = (_GIT_DIR / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None

    if not head.startswith("ref:"):
        # Detached HEAD: the file already holds the commit.
        return head[:7] if _is_sha(head) else None

    ref = head[4:].strip()
    try:
        return (_GIT_DIR / ref).read_text(encoding="utf-8").strip()[:7]
    except OSError:
        pass

    # The ref may only exist in .git/packed-refs.
    try:
        packed = (_GIT_DIR / "packed-refs").read_text(encoding="utf-8")
    except OSError:
        return None
    for line in packed.splitlines():
        if line.startswith("#") or line.startswith("^"):
            continue
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[1].strip() == ref and _is_sha(parts[0]):
            return parts[0][:7]
    return None


def _is_sha(value: str) -> bool:
    return len(value) == 40 and all(c in "0123456789abcdef" for c in value.lower())


def _write_atomically(path: Path, document: Dict[str, Any]) -> None:
    """Write the lock so it is either complete or absent.

    `lock_exists()` is the gate the events endpoint uses, so a partially
    written file would be read as "this session may record events" while the
    commitment it names is unreadable. Writing to a sibling temp file and
    renaming makes that state unreachable.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)
