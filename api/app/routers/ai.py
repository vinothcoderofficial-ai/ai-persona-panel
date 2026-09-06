"""The AI layer, made visible and triggerable (S26).

`sim/policy.py` and `sim/slow_agent.py` have called a language model since S12
and S13. Nothing under `api/` or `web/` imported either of them, so from a
running instance the "AI persona panel" looked like a numpy simulator reading
four pre-baked JSON files: the model's work was real, committed and invisible.
This router is the seam that puts it on screen.

Four things it serves, and one thing it refuses to do.

- **`GET /ai/status`** - what model is configured, whether it can be called at
  all, and what each persona already has on disk. The reason a call would fail
  is reported as a sentence, because "AI unavailable" with no cause is the
  single most useless thing this screen could say.
- **`GET /ai/personas/{id}/policy`** - the committed policy *and the rendered
  prompt that produced it*. Provenance is the point: a policy is eleven numbers,
  and without the instruction beside it a viewer has no way to judge them.
- **`POST /ai/personas/{id}/policy`** - ask the model again, now. This is the
  trigger; it is what makes the AI observable rather than historical.
- **`GET /ai/personas/{id}/trace`** - the slow agent's shopping trips, turn by
  turn, each with the model's own stated reason. `data/cache/traces/` has held
  these since S13 and nothing has ever rendered them.

**What it refuses: POST never overwrites `data/cache/policies/`.** Those files
are pre-registered inputs. `predictions/` locks are hashed against the
simulation they drive, and `scripts/eval.py` re-verifies that a lock predates
its session's first event. A button that quietly rewrote a policy mid-demo would
change what the simulator does while every committed lock went on claiming
otherwise - the exact failure this project is built to be immune to. So a
re-ask writes to `data/cache/policies/preview/`, returns the fresh answer beside
the committed one, and names the fields that moved. Adopting it is a deliberate
act outside this router: copy the file.

Every directory below is read as a module global at call time, the pattern
`api/app/prediction.py` uses for `PREDICTIONS_DIR`, so tests can redirect them
without the repository's own caches ever being written to.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from api.app.db import ROOT, PlanogramRecord, get_session
from sim import policy as policy_module
from sim.llm_client import (
    LLMConfigError,
    LLMHttpError,
    LLMUnavailableError,
    LLMValidationError,
    PROVIDER_ANTHROPIC,
    resolve_model,
    resolve_provider,
)
from sim.policy import PolicyValidationError, get_policy, render_prompt

router = APIRouter(tags=["ai"], prefix="/ai")

PERSONAS_DIR = ROOT / "data" / "personas"
POLICIES_DIR = ROOT / "data" / "cache" / "policies"
TRACES_DIR = ROOT / "data" / "cache" / "traces"

# Where a re-ask lands. Deliberately *not* POLICIES_DIR: see the module docstring.
POLICY_PREVIEW_DIR = POLICIES_DIR / "preview"

# The store persona policies are written against. Only used when the database
# holds no planogram of that name; otherwise whatever is seeded wins, so this
# constant can never quietly disagree with what is actually being simulated.
DEFAULT_PLANOGRAM_ID = "demo_aisle"

# The command that fills TRACES_DIR. Named in the 404 so a missing trace reads
# as "this run has not happened yet" rather than "a file is absent".
TRACE_COMMAND = "python -m sim.slow_agent --all"


def get_llm_client() -> Any:
    """The transport `complete_json` should use. `None` means "the real one".

    A FastAPI dependency purely so tests can override it the way they already
    override `get_session`, rather than monkeypatching httpx internals.
    """
    return None


# ---------------------------------------------------------------------------
# Loading what is on disk
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _personas() -> List[Dict[str, Any]]:
    """Every persona document, in id order."""
    if not PERSONAS_DIR.exists():
        return []
    return [_load_json(path) for path in sorted(PERSONAS_DIR.glob("*.json"))]


def _persona_or_404(persona_id: str) -> Dict[str, Any]:
    for persona in _personas():
        if persona["persona_id"] == persona_id:
            return persona
    known = ", ".join(p["persona_id"] for p in _personas()) or "none on disk"
    raise HTTPException(
        status_code=404,
        detail=f"no persona {persona_id!r} in {PERSONAS_DIR.name}/ (have: {known})",
    )


def _base_planogram(db: Session) -> Dict[str, Any]:
    """The store every policy and trace on this screen is about.

    Prefers the seeded `demo_aisle`, falls back to whatever single planogram the
    database holds, and 503s rather than guessing when it holds none - an empty
    database is a server that has not started properly, not a client mistake.
    """
    record = db.get(PlanogramRecord, DEFAULT_PLANOGRAM_ID)
    if record is None:
        record = db.exec(select(PlanogramRecord)).first()
    if record is None:
        raise HTTPException(
            status_code=503,
            detail="no planogram is loaded; the API seeds one from data/planograms/ at startup",
        )
    return json.loads(record.data)


def _policy_path(persona_id: str, planogram_id: str) -> Path:
    return POLICIES_DIR / f"{persona_id}_{planogram_id}.json"


def _trace_path(persona_id: str, planogram_id: str) -> Path:
    return TRACES_DIR / f"{persona_id}_{planogram_id}.json"


def _trace_summary(persona_id: str, planogram_id: str) -> Dict[str, Any]:
    """Which model produced this persona's trace, and how big it is.

    Missing fields are `None`, never 0: a trace that has not been generated is
    not a trace of zero shoppers, and RESULTS.md follows the same rule.
    """
    path = _trace_path(persona_id, planogram_id)
    if not path.exists():
        return {
            "trace_cached": False,
            "trace_model": None,
            "trace_n_shoppers": None,
            "trace_n_turns": None,
            "trace_temperature": None,
        }
    trace = _load_json(path)
    return {
        "trace_cached": True,
        "trace_model": trace.get("model"),
        "trace_n_shoppers": trace.get("n_shoppers"),
        "trace_n_turns": trace.get("n_turns"),
        "trace_temperature": trace.get("temperature"),
    }


# ---------------------------------------------------------------------------
# GET /ai/status
# ---------------------------------------------------------------------------

def _callability() -> Dict[str, Any]:
    """Whether a real call would go out, and in plain words why not.

    This mirrors the guard clauses at the top of `complete_json` rather than
    reimplementing a judgement: offline first, then the Anthropic-only key
    requirement. Getting this wrong in the optimistic direction is the worse
    error - a panel that offers a button which cannot work.
    """
    import os

    offline = os.environ.get("LLM_OFFLINE", "0").strip() == "1"

    try:
        provider = resolve_provider()
    except LLMConfigError as exc:
        return {
            "provider": (os.environ.get("LLM_PROVIDER") or "").strip().lower(),
            "model": None,
            "offline": offline,
            "api_key_set": bool(os.environ.get("LLM_API_KEY", "")),
            "can_call": False,
            "reason": str(exc),
        }

    api_key_set = bool(os.environ.get("LLM_API_KEY", ""))
    base_url = os.environ.get("LLM_BASE_URL") or None

    reason: Optional[str] = None
    if offline:
        reason = (
            "LLM_OFFLINE=1: no request will be sent. Cached policies and traces are "
            "served instead. Set LLM_OFFLINE=0 in .env to ask the model again."
        )
    elif provider == PROVIDER_ANTHROPIC and not api_key_set:
        reason = (
            "LLM_API_KEY is not set. Set it in .env, or set LLM_PROVIDER=ollama to "
            "use a local model."
        )

    return {
        "provider": provider,
        "model": resolve_model(),
        "base_url": base_url,
        "offline": offline,
        "api_key_set": api_key_set,
        "can_call": reason is None,
        "reason": reason,
    }


@router.get("/status")
def get_ai_status(db: Session = Depends(get_session)) -> Dict[str, Any]:
    """What the AI layer is configured to do, and what it has already done.

    Never echoes `LLM_API_KEY`, only whether one is set. The panel is filmed.
    """
    planogram = _base_planogram(db)
    planogram_id = planogram["planogram_id"]

    personas = []
    for persona in _personas():
        persona_id = persona["persona_id"]
        personas.append(
            {
                "persona_id": persona_id,
                "archetype": persona.get("archetype"),
                "share_of_population": persona.get("share_of_population"),
                "description": persona.get("description"),
                "policy_cached": _policy_path(persona_id, planogram_id).exists(),
                **_trace_summary(persona_id, planogram_id),
            }
        )

    return {
        **_callability(),
        "planogram_id": planogram_id,
        "prompt_template": str(
            policy_module.PROMPT_TEMPLATE_PATH.relative_to(ROOT).as_posix()
        ),
        "personas": personas,
    }


# ---------------------------------------------------------------------------
# GET /ai/personas/{persona_id}/policy
# ---------------------------------------------------------------------------

@router.get("/personas/{persona_id}/policy")
def get_persona_policy(
    persona_id: str, db: Session = Depends(get_session)
) -> Dict[str, Any]:
    """The committed policy, beside the exact prompt that produced it."""
    persona = _persona_or_404(persona_id)
    planogram = _base_planogram(db)
    planogram_id = planogram["planogram_id"]

    path = _policy_path(persona_id, planogram_id)
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=(
                f"no cached policy for persona {persona_id!r} on planogram "
                f"{planogram_id!r}. POST this path to ask the model for one."
            ),
        )

    return {
        "persona_id": persona_id,
        "planogram_id": planogram_id,
        "source": "cache",
        "policy": _load_json(path),
        "prompt": render_prompt(persona, planogram),
    }


# ---------------------------------------------------------------------------
# POST /ai/personas/{persona_id}/policy - the trigger
# ---------------------------------------------------------------------------

def _changed_fields(committed: Optional[Dict[str, Any]], fresh: Dict[str, Any]) -> List[str]:
    """Top-level keys on which the two answers disagree, in schema-ish order.

    Compared whole rather than walked: `brand_affinity` moving on one brand is
    one thing a viewer needs to look at, not four.
    """
    if committed is None:
        return sorted(fresh)
    keys = sorted(set(committed) | set(fresh))
    return [key for key in keys if committed.get(key) != fresh.get(key)]


@router.post("/personas/{persona_id}/policy")
def regenerate_persona_policy(
    persona_id: str,
    db: Session = Depends(get_session),
    client: Any = Depends(get_llm_client),
) -> Dict[str, Any]:
    """Ask the model for this persona's policy again, and report what it said.

    Writes to `POLICY_PREVIEW_DIR`. The committed policy is returned untouched
    alongside it, so the screen can show both. Nothing here adopts the new
    answer - see the module docstring for why that is deliberate.

    Failures are distinguished rather than collapsed, because each one has a
    different fix and the person looking at this screen is the person who has to
    apply it:

    - **503** the model was never reached (offline, or no key configured).
    - **422** it answered, and named a brand or category this store does not
      stock - `sim/policy.py`'s semantic layer, which is the only thing standing
      between a confident hallucination and the simulator.
    - **502** the provider refused the request (a stale key, an unknown model,
      a spent quota - `LLMHttpError` carries which, and the provider's own
      words), or it answered three times and never produced schema-valid JSON.

    That first 502 case is the one a real run hits first. Before it was handled,
    an expired key surfaced here as a bare `500 Internal Server Error` with an
    httpx traceback in the server log and nothing at all on screen - the exact
    failure mode this endpoint exists to prevent.
    """
    persona = _persona_or_404(persona_id)
    planogram = _base_planogram(db)
    planogram_id = planogram["planogram_id"]

    committed_path = _policy_path(persona_id, planogram_id)
    committed = _load_json(committed_path) if committed_path.exists() else None

    started = time.perf_counter()
    try:
        fresh = get_policy(
            persona,
            planogram,
            cache_dir=POLICY_PREVIEW_DIR,
            force=True,
            client=client,
        )
    except LLMUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PolicyValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (LLMHttpError, LLMValidationError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    elapsed_s = time.perf_counter() - started

    return {
        "persona_id": persona_id,
        "planogram_id": planogram_id,
        "source": "llm",
        "provider": resolve_provider(),
        "model": resolve_model(),
        "elapsed_s": elapsed_s,
        "prompt": render_prompt(persona, planogram),
        "policy": fresh,
        "committed": committed,
        "differs": committed != fresh,
        "changed_fields": _changed_fields(committed, fresh),
        "written_to": str(
            (POLICY_PREVIEW_DIR / f"{persona_id}_{planogram_id}.json").as_posix()
        ),
        "adopted": False,
    }


# ---------------------------------------------------------------------------
# GET /ai/personas/{persona_id}/trace
# ---------------------------------------------------------------------------

@router.get("/personas/{persona_id}/trace")
def get_persona_trace(
    persona_id: str, db: Session = Depends(get_session)
) -> Dict[str, Any]:
    """The slow agent's shopping trips for this persona, as generated.

    Returned whole, including every turn's stated reason and every cart's
    `cart_detail`. The reasons are the model's own words and are the reason this
    endpoint exists; `cart_detail` carries product names, so a screen built on
    this never has to show a bare `SKU_008`.
    """
    _persona_or_404(persona_id)
    planogram = _base_planogram(db)
    planogram_id = planogram["planogram_id"]

    path = _trace_path(persona_id, planogram_id)
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=(
                f"no trace for persona {persona_id!r} on planogram {planogram_id!r}. "
                f"Generate one with: {TRACE_COMMAND}"
            ),
        )
    return _load_json(path)
