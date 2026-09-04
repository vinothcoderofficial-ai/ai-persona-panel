"""Persona policy generation and caching (SPEC M4, S12).

`get_policy` turns a persona archetype into a numeric decision policy (`schemas/policy.schema.json`)
via `prompts/persona_policy.md` at temperature 0, cached to `data/cache/policies/{persona}_{planogram}.json`.

`sim/llm_client.py.complete_json` only enforces the JSON *schema* -- it has no idea what brands or
categories exist in a given store, so a policy naming a brand or category outside the planogram
(or naming the wrong persona_id) would pass schema validation while still being wrong. This module
adds that semantic check on top, and it is the only place that knows about the on-disk cache:
`complete_json` raises `LLMUnavailableError` when offline rather than silently reading a file, so
that decision -- serve the cache -- is made here, deliberately, not buried in the LLM client.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sim.llm_client import complete_json

ROOT = Path(__file__).resolve().parent.parent
PROMPT_TEMPLATE_PATH = Path(__file__).resolve().parent / "prompts" / "persona_policy.md"
SCHEMA_PATH = ROOT / "schemas" / "policy.schema.json"
DEFAULT_CACHE_DIR = ROOT / "data" / "cache" / "policies"

DEFAULT_BASELINE_CONV = 0.25


class PolicyValidationError(Exception):
    """A generated policy is wrong in a way the JSON schema alone cannot catch.

    `policy.schema.json` allows any string key in `brand_affinity` (other than the required
    `_default`) and any string in `goal_categories`, so a policy naming a brand or category that
    does not exist in the target planogram -- or claiming the wrong `persona_id` -- passes schema
    validation but is still invalid. This is that semantic layer.
    """


def _load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _planogram_brands(planogram: dict) -> set[str]:
    return {sku["brand"] for sku in planogram["skus"]}


def _planogram_categories(planogram: dict) -> set[str]:
    return {sku["category"] for sku in planogram["skus"]}


def _render_prompt(persona: dict, planogram: dict, baseline_conv: float) -> str:
    template = PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")
    categories = ", ".join(sorted(_planogram_categories(planogram)))
    brands = ", ".join(sorted(_planogram_brands(planogram)))
    return template.format(
        description=persona["description"],
        categories=categories,
        brands=brands,
        baseline_conv=baseline_conv,
    )


def _check_known_references(policy: dict, persona: dict, planogram: dict) -> None:
    """Reject a schema-valid policy that names a persona, brand, or category outside its scope."""
    requested_id = persona["persona_id"]
    if policy["persona_id"] != requested_id:
        raise PolicyValidationError(
            f"policy persona_id {policy['persona_id']!r} does not match the requested "
            f"persona {requested_id!r}"
        )

    unknown_brands = set(policy["brand_affinity"]) - _planogram_brands(planogram) - {"_default"}
    if unknown_brands:
        raise PolicyValidationError(
            f"policy for {requested_id!r} names brand(s) not in planogram "
            f"{planogram['planogram_id']!r}: {sorted(unknown_brands)}"
        )

    unknown_categories = set(policy["goal_categories"]) - _planogram_categories(planogram)
    if unknown_categories:
        raise PolicyValidationError(
            f"policy for {requested_id!r} names categor(y/ies) not in planogram "
            f"{planogram['planogram_id']!r}: {sorted(unknown_categories)}"
        )


def get_policy(
    persona: dict,
    planogram: dict,
    *,
    cache_dir: Path | str = DEFAULT_CACHE_DIR,
    force: bool = False,
    client: Any = None,
    baseline_conv: float = DEFAULT_BASELINE_CONV,
) -> dict:
    """Return the decision policy for `persona` shopping `planogram`, generating it if needed.

    Cache path: `{cache_dir}/{persona_id}_{planogram_id}.json`. On a cache hit with
    `force=False` (the default), the file is loaded and returned as-is -- no LLM call, and the
    file is never rewritten. On a miss, or when `force=True`, `prompts/persona_policy.md` is
    rendered and sent to `sim.llm_client.complete_json` at temperature 0; the result is checked
    against `planogram`'s actual brands/categories/persona id (`PolicyValidationError` on
    failure) before it is written to the cache.
    """
    cache_dir = Path(cache_dir)
    persona_id = persona["persona_id"]
    planogram_id = planogram["planogram_id"]
    cache_path = cache_dir / f"{persona_id}_{planogram_id}.json"

    if cache_path.exists() and not force:
        return json.loads(cache_path.read_text(encoding="utf-8"))

    prompt = _render_prompt(persona, planogram, baseline_conv)
    schema = _load_schema()
    policy = complete_json(prompt, schema, temperature=0.0, client=client)

    _check_known_references(policy, persona, planogram)

    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")
    return policy
