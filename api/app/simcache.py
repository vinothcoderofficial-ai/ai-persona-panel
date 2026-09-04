"""The one place a *population* SimResult is computed and cached.

Two callers need the same thing - "run all four personas over this resolved
planogram and combine them into the population result":

* `api/app/routers/whatif.py` (S15) needs it for the unpatched baseline every
  lift is measured against, and again for each patched candidate.
* `api/app/prediction.py` (S14) needs it to snapshot the prediction that gets
  locked before a real shopper starts.

It lived in whatif.py first; S14 moved it here rather than growing a second
copy, because two implementations of "the current prediction for a variant"
would eventually disagree and the lock would stop meaning anything.

Nothing here is a formula. `sim/saliency.py` (via `build_store`) and
`sim/simulator.py` own the maths; this module only loads the persona
documents and their cached policies, calls `run()` once per persona, and
hands the results to `combine()`. It is deliberately free of FastAPI and of
the database so both routers - and, later, `scripts/eval.py` - can use it.

Caching
-------
`load_personas` and `load_policy` cache the JSON documents for the life of
the process. `population` caches whole simulations, keyed on

    (variant_id, canonical hash of the resolved planogram, n_synth, seed)

The planogram's *content* is in the key, not just its id: POST /planograms
can replace a planogram under the same id, and a stale cache entry would
silently mis-state every prediction locked afterwards. `variant_id` is in the
key because `combine()` folds it into `sim_run_id`, so two variants that
happen to resolve to the same planogram must still get their own run ids.
"""
from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from api.app.db import ROOT
from sim.simulator import build_store, combine, run

PERSONAS_DIR = ROOT / "data" / "personas"
POLICIES_DIR = ROOT / "data" / "cache" / "policies"

# One lock guards all three module caches. It is only ever held around the dict
# access itself, never across a simulation - a plain Lock is not reentrant and
# holding it through `run()` would serialise every concurrent request. Two cold
# callers can therefore both compute the same result; the setdefault in
# `population()` keeps whichever landed first, so the cached object is stable.
_cache_lock = threading.Lock()
_personas: Optional[List[Dict[str, Any]]] = None
_policies: Dict[Tuple[str, str], Dict[str, Any]] = {}
_simulations: Dict[Tuple[str, str, int, int], "SimBundle"] = {}


@dataclass(frozen=True)
class SimBundle:
    """One simulation of one resolved planogram: the four persona SimResults
    and the share-weighted population result `combine()` made from them."""

    per_persona: Dict[str, Dict[str, Any]]
    population: Dict[str, Any]


def load_personas() -> List[Dict[str, Any]]:
    """The persona documents, in filename order, carrying `share_of_population`."""
    global _personas
    with _cache_lock:
        if _personas is None:
            _personas = [
                json.loads(path.read_text(encoding="utf-8"))
                for path in sorted(PERSONAS_DIR.glob("*.json"))
            ]
        return _personas


def load_policy(persona_id: str, planogram_id: str) -> Dict[str, Any]:
    """One cached persona policy. Raises FileNotFoundError if this planogram has
    no policy for this persona - callers turn that into a 404 or a warning."""
    key = (persona_id, planogram_id)
    with _cache_lock:
        policy = _policies.get(key)
        if policy is None:
            path = POLICIES_DIR / f"{persona_id}_{planogram_id}.json"
            if not path.exists():
                raise FileNotFoundError(
                    f"no cached policy for persona {persona_id!r} on planogram {planogram_id!r}"
                )
            policy = json.loads(path.read_text(encoding="utf-8"))
            _policies[key] = policy
        return policy


def simulate(resolved: Dict[str, Any], variant_id: str, n_synth: int, seed: int) -> SimBundle:
    """Run every persona over `resolved` and combine them into the population result.

    Uncached: `population()` is the cached entry point. `build_store()` computes
    the saliency layer, the policy reweighting and the Monte Carlo live in
    sim/simulator.py, and `combine()` does the share weighting. No maths here.
    """
    store = build_store(resolved)
    per_persona: Dict[str, Dict[str, Any]] = {}
    results: List[Dict[str, Any]] = []
    shares: List[float] = []

    for persona in load_personas():
        persona_id = persona["persona_id"]
        policy = load_policy(persona_id, resolved["planogram_id"])
        result = run(store, policy, n_runs=n_synth, seed=seed, variant_id=variant_id,
                     archetype=persona["archetype"])
        per_persona[persona_id] = result
        results.append(result)
        shares.append(float(persona["share_of_population"]))

    return SimBundle(per_persona=per_persona, population=combine(results, shares))


def population(resolved: Dict[str, Any], variant_id: str, *, n_synth: int,
               seed: int) -> SimBundle:
    """`simulate()`, computed once per (variant, planogram content, n_synth, seed).

    This is what makes a prediction lock cheap after the first one: two
    shoppers registered on the same variant get the same cached SimBundle and
    therefore the same `sim_run_id`, which is exactly the determinism
    `scripts/eval.py` relies on when it re-verifies a committed lock.
    """
    key = (variant_id, document_hash(resolved), int(n_synth), int(seed))
    with _cache_lock:
        cached = _simulations.get(key)
    if cached is not None:
        return cached

    bundle = simulate(resolved, variant_id, int(n_synth), int(seed))
    with _cache_lock:
        return _simulations.setdefault(key, bundle)


def canonical(document: Any) -> str:
    """Deterministic JSON: sorted keys, no insignificant whitespace.

    The same recipe `api/app/prediction.py` hashes the lock payload with, so
    "canonical" means one thing across the project.
    """
    return json.dumps(document, sort_keys=True, separators=(",", ":"))


def document_hash(document: Any) -> str:
    """SHA-256 of `canonical(document)`."""
    return hashlib.sha256(canonical(document).encode("utf-8")).hexdigest()
