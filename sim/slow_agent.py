"""LLM persona shoppers walking the store, one action at a time (SPEC M4, S13).

This is the *slow* path. `sim/simulator.py` runs 10,000 shoppers in numpy because that is what
scales; this module runs 20 shoppers per persona through an actual language model so there is a
readable, first-person decision trace behind the numbers. It is what satisfies "personas capable
of autonomously navigating and making purchase decisions" -- the simulator is the fast path that
scales them, not a substitute. Nothing here feeds the metrics; the output is evidence a human
reads.

**Generating real traces.** There is no API key in this repository, so the committed
`data/cache/traces/` is empty on purpose: a trace generated against a test double would be
fabricated persona reasoning presented as real, which is not something the demo may show.

Two ways to get a real model. Either set `LLM_API_KEY` for Anthropic, or run a local model with
no key and no account:

    LLM_PROVIDER=ollama            # posts to http://localhost:11434/api/chat
    LLM_MODEL=llama3.1:8b          # whatever `ollama list` shows

Then, with either provider configured, run:

    python -m sim.slow_agent --all --n 20              # all four personas, 20 shoppers each
    python -m sim.slow_agent --persona mission --n 20  # one persona

Both write `data/cache/traces/{persona_id}_{planogram_id}.json`. No code change is needed.

**Design notes worth not re-deriving.**

- The slot list sent to the model is reshuffled *every turn* from a seeded
  `numpy.random.default_rng`. Language models favour whatever is listed first, and without this
  the traces are a ranking of the planogram file's own ordering. `test_slow_agent.py` asserts the
  order actually varies.
- `seed` governs that shuffling only. With a real model at `temperature > 0` the model's own
  sampling is not reproducible; a run is byte-identical only against a deterministic client.
- Two validation layers. `sim/llm_client.py` enforces the JSON *shape* (`ACTION_SCHEMA`) and
  retries on its own. This module enforces what a schema cannot: that the target exists at the
  shopper's current station, that it holds a product when the action is a pickup, and that the
  reason is at most 20 words. A failure here is re-asked with the reason fed back, capped by
  `max_reasks` so a stubborn model cannot loop forever.
- Traces carry no timestamp, so a run at a fixed seed against a deterministic client is exactly
  reproducible and diffable.
- `prompts/slow_agent.md` is rendered with `str.format`, so any *literal* brace in that file must
  be doubled (`{{` / `}}`). The action-schema example in it already is.
- The library functions do no I/O apart from the trace write, which needs an explicit `cache_dir`
  -- there is deliberately no default, so a test cannot write to the real cache by accident. Only
  `main()` reads data files, and only from paths it was given.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from sim import llm_client
from sim.llm_client import LLMUnavailableError, LLMValidationError, complete_json
from sim.saliency import compute_saliency

ROOT = Path(__file__).resolve().parent.parent
PROMPT_TEMPLATE_PATH = Path(__file__).resolve().parent / "prompts" / "slow_agent.md"
DEFAULT_TRACE_DIR = ROOT / "data" / "cache" / "traces"
DEFAULT_PLANOGRAM_PATH = ROOT / "data" / "planograms" / "demo_aisle.json"
DEFAULT_PERSONA_DIR = ROOT / "data" / "personas"
DEFAULT_POLICY_DIR = ROOT / "data" / "cache" / "policies"

PERSONA_IDS = ("mission", "browser", "loyalist", "switcher")

TRACE_VERSION = 1
DEFAULT_N_SHOPPERS = 20
DEFAULT_SEED = 42
DEFAULT_MAX_TURNS = 24
DEFAULT_MAX_REASKS = 3
DEFAULT_TEMPERATURE = 0.7  # 20 identical shoppers would be a useless trace; variety is the point
DEFAULT_TIME_BUDGET_S = 90.0
MAX_REASON_WORDS = 20

ACTIONS = ("look", "approach", "pickup", "add_to_cart", "next_station", "checkout")
TARGETED_ACTIONS = frozenset({"look", "approach", "pickup", "add_to_cart"})
PURCHASE_ACTIONS = frozenset({"pickup", "add_to_cart"})
UNTARGETED_ACTIONS = frozenset({"next_station", "checkout"})

# Seconds each action costs the shopper, so the trace carries a plausible time axis.
ACTION_COST_S: dict[str, float] = {
    "look": 3.0,
    "approach": 4.0,
    "pickup": 6.0,
    "add_to_cart": 4.0,
    "next_station": 8.0,
    "checkout": 5.0,
}

# The trip finished the way a shopping trip finishes. Anything else is a guard firing.
COMPLETED_END_REASONS = frozenset({"checkout", "checkout_past_last_bay"})

ACTION_SCHEMA: dict = {
    "type": "object",
    "required": ["action", "target", "reason"],
    "additionalProperties": False,
    "properties": {
        "action": {"type": "string", "enum": list(ACTIONS)},
        "target": {"type": ["string", "null"]},
        "reason": {"type": "string", "minLength": 1},
    },
}


class SlowAgentError(Exception):
    """Raised when a run cannot proceed at all (a bad planogram, an unknown persona)."""


@dataclass(frozen=True)
class Station:
    """One shelf bay as the shopper sees it. Built once per planogram, never mutated."""

    bay_id: str
    index: int          # 0-based position along the aisle
    bay_type: str
    lookable: tuple[str, ...]        # occupied slots + ad slots carrying a creative
    purchasable: Mapping[str, str]   # slot_id -> sku_id, occupied product slots only
    empty_slot_ids: tuple[str, ...]  # sku_id is null: real slots, but nothing to pick up
    ad_slot_ids: frozenset[str]
    all_ids: frozenset[str]          # everything physically at this bay, visible or not
    lines: Mapping[str, str]         # target id -> the one line the model is shown


# ---------------------------------------------------------------------------
# Building the store the way the shopper sees it
# ---------------------------------------------------------------------------

def build_stations(planogram: Mapping) -> tuple[Station, ...]:
    """One `Station` per bay, in planogram order. The camera is fixed per bay, so this is the
    shopper's whole world at any moment.

    Visibility follows `sim/saliency.py` exactly -- occupied slots plus ad slots that carry a
    creative -- so what the model is told is visible is what the deterministic layer says anyone
    would notice. Empty slots and creative-less ad slots stay known to the station (they are real
    objects, and "move the SKU to eye level" depends on that) but are never offered as targets.
    """
    saliency = compute_saliency(planogram)
    skus = {s["sku_id"]: s for s in planogram["skus"]}
    creatives = {c["creative_id"]: c for c in planogram.get("creatives", [])}

    stations: list[Station] = []
    for index, bay in enumerate(planogram["bays"]):
        bay_saliency = saliency[bay["bay_id"]]
        prominence = bay_saliency.p_by_id()
        slots = {sl["slot_id"]: sl for shelf in bay["shelves"] for sl in shelf["slots"]}
        levels = {sl["slot_id"]: shelf["level"]
                  for shelf in bay["shelves"] for sl in shelf["slots"]}
        ads = {ad["ad_slot_id"]: ad for ad in bay["ad_slots"]}

        purchasable = {sid: sl["sku_id"] for sid, sl in slots.items() if sl["sku_id"] is not None}
        empty_ids = tuple(sid for sid, sl in slots.items() if sl["sku_id"] is None)

        lines = {}
        for target_id in bay_saliency.target_ids:
            if target_id in slots:
                lines[target_id] = _product_line(
                    target_id, slots[target_id], skus[slots[target_id]["sku_id"]],
                    levels[target_id], prominence[target_id],
                )
            else:
                lines[target_id] = _ad_line(target_id, ads[target_id],
                                            creatives[ads[target_id]["creative_id"]],
                                            prominence[target_id])

        stations.append(Station(
            bay_id=bay["bay_id"],
            index=index,
            bay_type=bay["type"],
            lookable=tuple(bay_saliency.target_ids),
            purchasable=purchasable,
            empty_slot_ids=empty_ids,
            ad_slot_ids=frozenset(ads),
            all_ids=frozenset(slots) | frozenset(ads),
            lines=lines,
        ))
    return tuple(stations)


def _product_line(slot_id: str, slot: Mapping, sku: Mapping, level: str,
                  prominence: float) -> str:
    promo = " | on promotion" if sku["promo"] else ""
    return (
        f"  {slot_id} | {sku['name']} | brand {sku['brand']} | category {sku['category']} "
        f"| price {sku['price']:g} | {level} shelf | {slot['facings']} facings{promo} "
        f"| prominence {prominence:.2f}"
    )


def _ad_line(ad_slot_id: str, ad: Mapping, creative: Mapping, prominence: float) -> str:
    return (
        f"  {ad_slot_id} | advertising panel for {creative['brand']} | {ad['type']} "
        f"| prominence {prominence:.2f}"
    )


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------

def render_prompt(persona: Mapping, station: Station, ordered_targets: Sequence[str], *,
                  n_stations: int, cart: Sequence[Mapping], time_left_s: float, turn: int,
                  max_turns: int) -> str:
    """Render `prompts/slow_agent.md` for one turn. `ordered_targets` is already shuffled."""
    template = PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")
    return template.format(
        description=persona["description"],
        station_id=station.bay_id,
        station_index=station.index + 1,
        n_stations=n_stations,
        station_type=station.bay_type,
        slots="\n".join(station.lines[t] for t in ordered_targets),
        empty_slots=", ".join(station.empty_slot_ids) if station.empty_slot_ids else "none",
        cart=", ".join(f"{item['sku_id']} ({item['name']})" for item in cart) if cart else "empty",
        time_left_s=f"{max(time_left_s, 0.0):.0f}",
        turn=turn,
        max_turns=max_turns,
    )


def _with_rejections(prompt: str, rejections: Sequence[Mapping]) -> str:
    if not rejections:
        return prompt
    lines = [
        f"- {json.dumps({k: r[k] for k in ('action', 'target', 'reason')})} "
        f"was rejected because {r['rejection']}"
        for r in rejections
    ]
    return (
        f"{prompt}\n\n"
        "Your previous action this turn was rejected:\n"
        + "\n".join(lines)
        + "\nReply with ONE corrected JSON action for this same turn."
    )


# ---------------------------------------------------------------------------
# Semantic validation -- what the JSON Schema cannot express
# ---------------------------------------------------------------------------

def reject_reason(action: Mapping, station: Station) -> str | None:
    """Why this action is not legal for this shopper right now, or None if it is.

    The returned sentence is fed straight back to the model, so it names the offending id and
    what to do instead.
    """
    name = action["action"]
    target = action["target"]

    n_words = len(action["reason"].split())
    if n_words == 0:
        return f"the reason was blank; say why in at most {MAX_REASON_WORDS} words."
    if n_words > MAX_REASON_WORDS:
        return f"the reason was {n_words} words; the limit is {MAX_REASON_WORDS} words."

    if name in UNTARGETED_ACTIONS:
        if target is not None:
            return f"{name!r} takes no target; set target to null."
        return None

    if name in TARGETED_ACTIONS and target is None:
        return f"{name!r} requires a target slot id listed this turn, not null."

    if target not in station.all_ids:
        return (f"{target!r} is not at station {station.bay_id}; "
                f"choose one of the ids listed this turn.")

    if name in PURCHASE_ACTIONS and target not in station.purchasable:
        if target in station.ad_slot_ids:
            return (f"{target!r} is an advertising panel, not a product; "
                    f"choose a product listed this turn.")
        return (f"{target!r} is an empty shelf gap and holds no product; "
                f"choose a product listed this turn.")

    if target not in station.lookable:
        return (f"{target!r} is not visible at station {station.bay_id} this turn; "
                f"choose one of the ids listed.")

    return None


# ---------------------------------------------------------------------------
# One shopper's trip
# ---------------------------------------------------------------------------

def run_shopper(persona: Mapping, stations: Sequence[Station], skus: Mapping[str, Mapping], *,
                rng: np.random.Generator, client: Any = None, model: str | None = None,
                temperature: float = DEFAULT_TEMPERATURE, max_turns: int = DEFAULT_MAX_TURNS,
                max_reasks: int = DEFAULT_MAX_REASKS,
                time_budget_s: float = DEFAULT_TIME_BUDGET_S) -> dict:
    """Walk one shopper through the store, returning their trace record.

    Every accepted action lands in `turns`; every rejected one lands in `rejections` with the
    sentence the model was sent back. The trip always ends for a stated reason -- `end_reason` is
    one of `COMPLETED_END_REASONS` when it finished like a shopping trip, and otherwise names the
    guard that stopped it.
    """
    turns: list[dict] = []
    rejections: list[dict] = []
    cart: list[dict] = []
    visited = [stations[0].bay_id]

    station_index = 0
    time_left = float(time_budget_s)

    for turn in range(1, max_turns + 1):
        station = stations[station_index]
        order = list(station.lookable)
        rng.shuffle(order)
        base_prompt = render_prompt(
            persona, station, order, n_stations=len(stations), cart=cart,
            time_left_s=time_left, turn=turn, max_turns=max_turns,
        )

        turn_rejections: list[dict] = []
        action: dict | None = None
        for _ in range(max_reasks + 1):
            try:
                candidate = complete_json(
                    _with_rejections(base_prompt, turn_rejections), ACTION_SCHEMA,
                    model=model, temperature=temperature, client=client,
                )
            except LLMValidationError as exc:
                # The model never produced schema-valid JSON. End this shopper with the reason
                # on the record rather than losing the other 19 to one bad response.
                rejections.extend(turn_rejections)
                return _shopper_record(turns, rejections, cart, visited,
                                       "llm_validation_failed", str(exc))

            why = reject_reason(candidate, station)
            if why is None:
                action = candidate
                break
            turn_rejections.append({
                "turn": turn,
                "station_id": station.bay_id,
                "action": candidate["action"],
                "target": candidate["target"],
                "reason": candidate["reason"],
                "rejection": why,
            })

        rejections.extend(turn_rejections)
        if action is None:
            return _shopper_record(turns, rejections, cart, visited, "reask_limit", None)

        time_left -= ACTION_COST_S[action["action"]]
        turns.append({
            "turn": turn,
            "station_id": station.bay_id,
            "action": action["action"],
            "target": action["target"],
            "reason": action["reason"],
            "time_left_s": round(max(time_left, 0.0), 1),
        })

        if action["action"] == "add_to_cart":
            sku = skus[station.purchasable[action["target"]]]
            cart.append({"sku_id": sku["sku_id"], "name": sku["name"], "brand": sku["brand"],
                         "category": sku["category"], "price": sku["price"],
                         "slot_id": action["target"]})

        if action["action"] == "checkout":
            return _shopper_record(turns, rejections, cart, visited, "checkout", None)

        if action["action"] == "next_station":
            station_index += 1
            if station_index >= len(stations):
                # Walking past the last bay is a completed trip, not an error.
                return _shopper_record(turns, rejections, cart, visited,
                                       "checkout_past_last_bay", None)
            visited.append(stations[station_index].bay_id)

        if time_left <= 0.0:
            return _shopper_record(turns, rejections, cart, visited, "out_of_time", None)

    return _shopper_record(turns, rejections, cart, visited, "max_turns", None)


def _shopper_record(turns: list[dict], rejections: list[dict], cart: list[dict],
                    visited: list[str], end_reason: str, note: str | None) -> dict:
    return {
        "turns": turns,
        "rejections": rejections,
        "cart": [item["sku_id"] for item in cart],
        "cart_detail": cart,
        "stations_visited": visited,
        "end_reason": end_reason,
        "end_note": note,
        "n_turns": len(turns),
        "n_rejections": len(rejections),
    }


# ---------------------------------------------------------------------------
# A persona's whole panel, and the trace document
# ---------------------------------------------------------------------------

def run_persona(persona: Mapping, planogram: Mapping, *,
                n_shoppers: int = DEFAULT_N_SHOPPERS, seed: int = DEFAULT_SEED,
                policy: Mapping | None = None, client: Any = None, model: str | None = None,
                temperature: float = DEFAULT_TEMPERATURE, max_turns: int = DEFAULT_MAX_TURNS,
                max_reasks: int = DEFAULT_MAX_REASKS) -> dict:
    """Run `n_shoppers` LLM shoppers of one persona through `planogram`; return the trace document.

    Pure: no file is read except the prompt template, and nothing is written. Pass the trace to
    `write_trace` to cache it. Each shopper gets its own RNG stream seeded from
    `(seed, shopper_index)`, so a single shopper can be reproduced without replaying the panel.

    `policy` is optional and only supplies the shopper's time budget
    (`time_budget_s.mean`); the LLM decides everything else.
    """
    if n_shoppers < 1:
        raise SlowAgentError(f"n_shoppers must be at least 1, got {n_shoppers}")
    stations = build_stations(planogram)
    if not stations:
        raise SlowAgentError(f"planogram {planogram['planogram_id']!r} has no bays to shop")

    skus = {s["sku_id"]: s for s in planogram["skus"]}
    time_budget = (float(policy["time_budget_s"]["mean"]) if policy is not None
                   else DEFAULT_TIME_BUDGET_S)

    shoppers: list[dict] = []
    for index in range(n_shoppers):
        record = run_shopper(
            persona, stations, skus,
            rng=np.random.default_rng([seed, index]), client=client, model=model,
            temperature=temperature, max_turns=max_turns, max_reasks=max_reasks,
            time_budget_s=time_budget,
        )
        shoppers.append({"shopper_index": index, **record})

    return {
        "trace_version": TRACE_VERSION,
        "persona_id": persona["persona_id"],
        "archetype": persona.get("archetype", persona["persona_id"]),
        "description": persona["description"],
        "planogram_id": planogram["planogram_id"],
        "n_shoppers": n_shoppers,
        "seed": seed,
        "temperature": temperature,
        # The model that actually answered, not the caller's override. The
        # override is None whenever the model comes from LLM_MODEL, which is
        # the normal case, and these traces are shown on screen as evidence of
        # persona reasoning -- one that cannot name its model is weaker
        # evidence than it looks.
        "model": llm_client.resolve_model(model),
        "time_budget_s": time_budget,
        "max_turns": max_turns,
        "max_reasks": max_reasks,
        "generated_by": "sim/slow_agent.py",
        "shoppers": shoppers,
        "n_turns": sum(s["n_turns"] for s in shoppers),
        "n_rejections": sum(s["n_rejections"] for s in shoppers),
        "end_reasons": dict(Counter(s["end_reason"] for s in shoppers)),
        "carts": dict(Counter(sku for s in shoppers for sku in s["cart"])),
    }


def write_trace(trace: Mapping, *, cache_dir: Path | str) -> Path:
    """Write `trace` to `{cache_dir}/{persona_id}_{planogram_id}.json` and return that path.

    `cache_dir` has no default on purpose. `data/cache/traces/` is demo evidence, and a trace
    produced against a test double would be fabricated persona reasoning; making the destination
    explicit means no test can write there by accident.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{trace['persona_id']}_{trace['planogram_id']}.json"
    path.write_text(json.dumps(trace, indent=2) + "\n", encoding="utf-8")
    return path


def run_all(personas: Iterable[Mapping], planogram: Mapping, *, cache_dir: Path | str,
            policies: Mapping[str, Mapping] | None = None,
            n_shoppers: int = DEFAULT_N_SHOPPERS, seed: int = DEFAULT_SEED, client: Any = None,
            model: str | None = None, temperature: float = DEFAULT_TEMPERATURE,
            max_turns: int = DEFAULT_MAX_TURNS,
            max_reasks: int = DEFAULT_MAX_REASKS) -> dict[str, dict]:
    """Run every persona and cache each trace under `cache_dir`. Returns persona_id -> trace."""
    traces: dict[str, dict] = {}
    for persona in personas:
        policy = None if policies is None else policies.get(persona["persona_id"])
        trace = run_persona(persona, planogram, n_shoppers=n_shoppers, seed=seed, policy=policy,
                            client=client, model=model, temperature=temperature,
                            max_turns=max_turns, max_reasks=max_reasks)
        write_trace(trace, cache_dir=cache_dir)
        traces[trace["persona_id"]] = trace
    return traces


# ---------------------------------------------------------------------------
# Entry point: `python -m sim.slow_agent --all --n 20`
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m sim.slow_agent",
        description="Run LLM persona shoppers through the store and cache their decision traces. "
                    "Requires a configured provider (LLM_API_KEY, or LLM_PROVIDER=ollama for a "
                    "local model); there is no offline fallback, because a trace that "
                    "did not come from a model is not evidence of anything.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--persona", action="append", choices=PERSONA_IDS,
                       help="persona to run; repeat for several")
    group.add_argument("--all", action="store_true", help=f"run all of: {', '.join(PERSONA_IDS)}")
    parser.add_argument("--n", type=int, default=DEFAULT_N_SHOPPERS,
                        help=f"shoppers per persona (default {DEFAULT_N_SHOPPERS})")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED,
                        help=f"seed for slot-order shuffling (default {DEFAULT_SEED})")
    parser.add_argument("--planogram", type=Path, default=DEFAULT_PLANOGRAM_PATH)
    parser.add_argument("--personas-dir", type=Path, default=DEFAULT_PERSONA_DIR)
    parser.add_argument("--policy-dir", type=Path, default=DEFAULT_POLICY_DIR)
    parser.add_argument("--out", type=Path, default=DEFAULT_TRACE_DIR,
                        help="trace cache directory (default data/cache/traces)")
    parser.add_argument("--model", default=None, help="override LLM_MODEL for this run")
    parser.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE)
    parser.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS)
    parser.add_argument("--max-reasks", type=int, default=DEFAULT_MAX_REASKS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    persona_ids = list(PERSONA_IDS) if args.all else list(dict.fromkeys(args.persona))

    planogram = _load_json(args.planogram)
    personas = [_load_json(args.personas_dir / f"{pid}.json") for pid in persona_ids]
    policies = {}
    for pid in persona_ids:
        policy_path = args.policy_dir / f"{pid}_{planogram['planogram_id']}.json"
        if policy_path.exists():
            policies[pid] = _load_json(policy_path)

    try:
        traces = run_all(personas, planogram, cache_dir=args.out, policies=policies,
                         n_shoppers=args.n, seed=args.seed, model=args.model,
                         temperature=args.temperature, max_turns=args.max_turns,
                         max_reasks=args.max_reasks)
    except LLMUnavailableError as exc:
        # Deliberately no cached fallback: LLM_OFFLINE=1 serves cached *policies*, but a persona
        # trace that did not come from a model is fabricated reasoning, and these traces are shown
        # on screen. Nothing is written; the cache stays empty until a real run fills it.
        print(f"slow_agent: {exc}", file=sys.stderr)
        print("No traces were written. Persona traces have no offline fallback -- they are only "
              "meaningful if a model actually produced them.", file=sys.stderr)
        return 2

    for persona_id, trace in traces.items():
        completed = sum(1 for s in trace["shoppers"] if s["end_reason"] in COMPLETED_END_REASONS)
        path = Path(args.out) / f"{persona_id}_{trace['planogram_id']}.json"
        print(f"{persona_id}: {trace['n_turns']} actions, {trace['n_rejections']} rejected, "
              f"{completed}/{trace['n_shoppers']} completed trips -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
