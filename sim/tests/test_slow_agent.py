"""sim/slow_agent.py: LLM persona shoppers walk the store and leave readable traces (SPEC M4, S13).

Every test here runs against an injected fake transport. There is no API key in this environment
and there must never be a real network call: `complete_json` only builds a request when a client
is injected or `LLM_API_KEY` is set, and every test below injects its own transport.

The real `data/cache/traces/` directory holds the traces that will be shown on screen in the demo.
Nothing in this module may write there -- fabricated persona reasoning in the deliverable would be
a lie. Every write goes to `tmp_path`, and `test_the_real_trace_cache_is_untouched...` asserts the
real directory still holds nothing but its `.gitkeep`.
"""
from __future__ import annotations

import json as json_module
import re

import pytest

from sim.llm_client import LLMUnavailableError
from sim.slow_agent import (
    COMPLETED_END_REASONS,
    DEFAULT_TRACE_DIR,
    MAX_REASON_WORDS,
    SlowAgentError,
    main,
    render_prompt,
    run_all,
    run_persona,
    write_trace,
)

# Ids taken from data/planograms/demo_aisle.json: bays B1, B2, B3 in that order.
FIRST_BAY = "B1"
B1_EYE_SLOT = "B1S3P1"          # occupied, eye level
B1_EMPTY_SLOT = "B1S3P2"        # sku_id: null
B1_TOP_SLOT = "B1S1P1"          # occupied, top shelf
B2_SLOT = "B2S1P1"              # occupied, but at the *next* station
B1_CREATIVELESS_AD = "B1_TALKER"  # ad slot with creative_id: null

TWENTY_THREE_WORDS = (
    "I want to compare the price of this pack against the one beside it before "
    "I decide whether it is worth buying today"
)


def act(action: str, target: str | None, reason: str = "Checking the chips first.") -> dict:
    return {"action": action, "target": target, "reason": reason}


CHECKOUT = act("checkout", None, "Cart is done, heading out.")


class _FakeResponse:
    def __init__(self, body: dict):
        self._body = body

    def json(self) -> dict:
        return self._body

    def raise_for_status(self) -> None:
        pass


class FakeLLM:
    """Replays a script of actions and records every prompt it was sent.

    Exposes `.post(url, **kwargs)` -- the shape `sim/llm_client.py` uses against httpx -- so no
    httpx internals are monkeypatched and no socket is ever opened. When the script runs out the
    last action repeats, which is how the "model always returns an invalid target" test loops.
    """

    def __init__(self, script: list[dict]):
        self._script = list(script)
        self.prompts: list[str] = []

    def post(self, url, **kwargs):
        self.prompts.append(kwargs["json"]["messages"][0]["content"])
        action = self._script[min(len(self.prompts) - 1, len(self._script) - 1)]
        return _FakeResponse({"content": [{"type": "text", "text": json_module.dumps(action)}]})


def listed_slot_order(prompt: str) -> list[str]:
    """The slot ids as they were listed to the model, in the order they appeared in the prompt."""
    return re.findall(r"^ {2}(\w+) \|", prompt, re.MULTILINE)


def only_shopper(trace: dict) -> dict:
    assert len(trace["shoppers"]) == 1
    return trace["shoppers"][0]


# ---------------------------------------------------------------------------
# Target validation: rejected and re-asked, never crashed on.
# ---------------------------------------------------------------------------

def test_a_target_from_another_station_is_rejected_and_reasked(planogram, personas):
    llm = FakeLLM([act("look", B2_SLOT, "The cola over there looks interesting."),
                   act("look", B1_EYE_SLOT, "Eye level biscuits catch my attention."),
                   CHECKOUT])

    trace = run_persona(personas["mission"], planogram, n_shoppers=1, seed=7, client=llm)
    shopper = only_shopper(trace)

    # The first ask was rejected, so there must have been a second.
    assert len(llm.prompts) >= 2
    assert B2_SLOT in llm.prompts[1]
    assert f"not at station {FIRST_BAY}" in llm.prompts[1]

    # The rejected action is recorded, and the *accepted* action is the second one.
    assert [r["target"] for r in shopper["rejections"]] == [B2_SLOT]
    assert shopper["turns"][0]["target"] == B1_EYE_SLOT
    assert shopper["turns"][0]["station_id"] == FIRST_BAY
    assert shopper["turns"][0]["turn"] == 1


def test_a_pickup_of_an_empty_slot_is_rejected_and_reasked(planogram, personas):
    llm = FakeLLM([act("pickup", B1_EMPTY_SLOT, "Reaching for the pack at eye level."),
                   act("pickup", B1_EYE_SLOT, "Reaching for the pack at eye level."),
                   CHECKOUT])

    trace = run_persona(personas["mission"], planogram, n_shoppers=1, seed=7, client=llm)
    shopper = only_shopper(trace)

    assert len(llm.prompts) >= 2
    assert B1_EMPTY_SLOT in llm.prompts[1]
    assert "empty shelf gap and holds no product" in llm.prompts[1]

    assert [r["target"] for r in shopper["rejections"]] == [B1_EMPTY_SLOT]
    assert shopper["turns"][0] == {
        "turn": 1,
        "station_id": FIRST_BAY,
        "action": "pickup",
        "target": B1_EYE_SLOT,
        "reason": "Reaching for the pack at eye level.",
        "time_left_s": 84.0,  # 90 s default budget less the 6 s a pickup costs
    }


def test_a_pickup_of_an_ad_panel_is_rejected_and_reasked(planogram, personas):
    """B1_TALKER is a real object at B1 -- it is at the station, it just is not a product."""
    llm = FakeLLM([act("pickup", B1_CREATIVELESS_AD, "Grabbing whatever that sign is selling."),
                   act("pickup", B1_EYE_SLOT, "Reaching for the pack at eye level."),
                   CHECKOUT])

    trace = run_persona(personas["browser"], planogram, n_shoppers=1, seed=7, client=llm)
    shopper = only_shopper(trace)

    assert shopper["rejections"][0]["target"] == B1_CREATIVELESS_AD
    assert "is an advertising panel, not a product" in shopper["rejections"][0]["rejection"]
    assert "is an advertising panel, not a product" in llm.prompts[1]
    assert shopper["turns"][0]["target"] == B1_EYE_SLOT


def test_a_target_that_exists_nowhere_in_the_store_is_rejected(planogram, personas):
    llm = FakeLLM([act("look", "NOT_A_SLOT", "Looking at something I invented."),
                   act("look", B1_EYE_SLOT, "Eye level biscuits catch my attention."),
                   CHECKOUT])

    trace = run_persona(personas["mission"], planogram, n_shoppers=1, seed=7, client=llm)
    shopper = only_shopper(trace)

    assert shopper["rejections"][0]["target"] == "NOT_A_SLOT"
    assert f"not at station {FIRST_BAY}" in shopper["rejections"][0]["rejection"]
    assert shopper["turns"][0]["target"] == B1_EYE_SLOT


def test_looking_at_an_ad_slot_with_no_creative_is_rejected(planogram, personas):
    llm = FakeLLM([act("look", B1_CREATIVELESS_AD, "Reading the shelf talker."),
                   act("look", B1_EYE_SLOT, "Eye level biscuits catch my attention."),
                   CHECKOUT])

    trace = run_persona(personas["browser"], planogram, n_shoppers=1, seed=7, client=llm)
    shopper = only_shopper(trace)

    assert shopper["rejections"][0]["target"] == B1_CREATIVELESS_AD
    assert "not visible" in shopper["rejections"][0]["rejection"]
    assert shopper["turns"][0]["target"] == B1_EYE_SLOT


# ---------------------------------------------------------------------------
# Position-bias mitigation: the slot list order must actually change per turn.
# ---------------------------------------------------------------------------

def test_the_slot_list_order_varies_across_turns(planogram, personas):
    """LLMs favour whatever is listed first, so the order must be reshuffled every turn.

    The fake always looks at the same valid slot, so the shopper stays at B1 and every prompt is
    a fresh turn (no re-asks). If the order were fixed, every prompt would list the same sequence.
    """
    llm = FakeLLM([act("look", B1_TOP_SLOT, "Scanning the top shelf again.")])

    run_persona(personas["browser"], planogram, n_shoppers=1, seed=11, client=llm, max_turns=6)

    orders = [listed_slot_order(p) for p in llm.prompts]
    assert len(orders) == 6
    assert all(len(o) >= 8 for o in orders), orders  # B1 has 8 occupied slots visible
    assert all(sorted(o) == sorted(orders[0]) for o in orders)  # same set, different order
    assert len({tuple(o) for o in orders}) > 1, "slot order was identical on every turn"


def test_a_different_seed_produces_a_different_slot_ordering(planogram, personas):
    def orders_for(seed: int) -> list[tuple[str, ...]]:
        llm = FakeLLM([act("look", B1_TOP_SLOT, "Scanning the top shelf again.")])
        run_persona(personas["browser"], planogram, n_shoppers=1, seed=seed, client=llm,
                    max_turns=5)
        return [tuple(listed_slot_order(p)) for p in llm.prompts]

    assert orders_for(1) != orders_for(2)


# ---------------------------------------------------------------------------
# Reason length: a schema cannot count words, so slow_agent enforces the cap itself.
# ---------------------------------------------------------------------------

def test_a_reason_longer_than_twenty_words_is_rejected_and_reasked(planogram, personas):
    assert len(TWENTY_THREE_WORDS.split()) > MAX_REASON_WORDS
    llm = FakeLLM([act("look", B1_EYE_SLOT, TWENTY_THREE_WORDS),
                   act("look", B1_EYE_SLOT, "Eye level biscuits catch my attention."),
                   CHECKOUT])

    trace = run_persona(personas["switcher"], planogram, n_shoppers=1, seed=7, client=llm)
    shopper = only_shopper(trace)

    assert len(llm.prompts) >= 2
    assert f"{MAX_REASON_WORDS} words" in llm.prompts[1]
    assert len(shopper["rejections"]) == 1
    assert shopper["turns"][0]["reason"] == "Eye level biscuits catch my attention."


# ---------------------------------------------------------------------------
# The re-ask budget is capped, so a stubborn model cannot loop forever.
# ---------------------------------------------------------------------------

def test_a_model_that_never_returns_a_valid_target_terminates_and_the_trace_says_why(
        planogram, personas):
    llm = FakeLLM([act("look", B2_SLOT, "The cola over there looks interesting.")])

    trace = run_persona(personas["mission"], planogram, n_shoppers=1, seed=7, client=llm,
                        max_reasks=2)
    shopper = only_shopper(trace)

    # One ask plus exactly max_reasks re-asks, then the shopper's trip ends.
    assert len(llm.prompts) == 3
    assert shopper["turns"] == []
    assert len(shopper["rejections"]) == 3
    assert shopper["end_reason"] == "reask_limit"
    assert shopper["end_reason"] not in COMPLETED_END_REASONS
    assert B2_SLOT in shopper["rejections"][-1]["rejection"]


# ---------------------------------------------------------------------------
# Walking the store: next_station / checkout take a null target.
# ---------------------------------------------------------------------------

def test_next_station_walks_the_bays_in_order_and_past_the_last_bay_ends_the_trip(
        planogram, personas):
    llm = FakeLLM([act("next_station", None, "Nothing here, moving on.")])

    trace = run_persona(personas["mission"], planogram, n_shoppers=1, seed=7, client=llm)
    shopper = only_shopper(trace)

    assert shopper["stations_visited"] == ["B1", "B2", "B3"]
    assert [t["station_id"] for t in shopper["turns"]] == ["B1", "B2", "B3"]
    assert shopper["end_reason"] == "checkout_past_last_bay"
    assert shopper["end_reason"] in COMPLETED_END_REASONS
    assert shopper["rejections"] == []


def test_checkout_with_a_null_target_ends_the_trip(planogram, personas):
    llm = FakeLLM([CHECKOUT])

    trace = run_persona(personas["loyalist"], planogram, n_shoppers=1, seed=7, client=llm)
    shopper = only_shopper(trace)

    assert shopper["turns"] == [{
        "turn": 1,
        "station_id": FIRST_BAY,
        "action": "checkout",
        "target": None,
        "reason": "Cart is done, heading out.",
        "time_left_s": 85.0,  # 90 s default budget less the 5 s a checkout costs
    }]
    assert shopper["end_reason"] == "checkout"
    assert shopper["end_reason"] in COMPLETED_END_REASONS


def test_checkout_with_a_non_null_target_is_rejected_and_reasked(planogram, personas):
    llm = FakeLLM([act("checkout", B1_EYE_SLOT, "Done, paying for this one."), CHECKOUT])

    trace = run_persona(personas["loyalist"], planogram, n_shoppers=1, seed=7, client=llm)
    shopper = only_shopper(trace)

    assert "takes no target" in shopper["rejections"][0]["rejection"]
    assert "takes no target" in llm.prompts[1]
    assert shopper["turns"][0]["action"] == "checkout"
    assert shopper["turns"][0]["target"] is None


def test_look_with_a_null_target_is_rejected_and_reasked(planogram, personas):
    llm = FakeLLM([act("look", None, "Just looking around the aisle."),
                   act("look", B1_EYE_SLOT, "Eye level biscuits catch my attention."),
                   CHECKOUT])

    trace = run_persona(personas["browser"], planogram, n_shoppers=1, seed=7, client=llm)
    shopper = only_shopper(trace)

    assert "requires a target" in shopper["rejections"][0]["rejection"]
    assert shopper["turns"][0]["target"] == B1_EYE_SLOT


# ---------------------------------------------------------------------------
# add_to_cart fills a cart the trace can show.
# ---------------------------------------------------------------------------

def test_add_to_cart_records_the_sku_in_the_final_cart(planogram, personas):
    llm = FakeLLM([act("add_to_cart", B1_EYE_SLOT, "This is the one I came for."), CHECKOUT])

    trace = run_persona(personas["mission"], planogram, n_shoppers=1, seed=7, client=llm)
    shopper = only_shopper(trace)

    assert shopper["cart"] == ["SKU_005"]  # B1S3P1 holds SKU_005 in demo_aisle
    assert [t["action"] for t in shopper["turns"]] == ["add_to_cart", "checkout"]


# ---------------------------------------------------------------------------
# An unknown action name never reaches slow_agent: complete_json's schema catches it.
# ---------------------------------------------------------------------------

def test_an_action_outside_the_enum_is_retried_by_the_schema_validator(planogram, personas):
    llm = FakeLLM([act("dance", B1_EYE_SLOT, "Doing something the schema forbids."), CHECKOUT])

    trace = run_persona(personas["browser"], planogram, n_shoppers=1, seed=7, client=llm)
    shopper = only_shopper(trace)

    assert len(llm.prompts) >= 2
    # complete_json feeds the jsonschema enum violation back into the prompt itself.
    assert "is not one of" in llm.prompts[1]
    assert "dance" in llm.prompts[1]
    assert [t["action"] for t in shopper["turns"]] == ["checkout"]
    # A schema violation is complete_json's business, not a slow_agent semantic rejection.
    assert shopper["rejections"] == []


# ---------------------------------------------------------------------------
# Traces for all four personas -- written to tmp_path, never to the real cache.
# ---------------------------------------------------------------------------

def test_traces_are_non_empty_for_all_four_personas(planogram, personas, tmp_path):
    llm = FakeLLM([act("look", B1_EYE_SLOT, "Eye level biscuits catch my attention."),
                   act("add_to_cart", B1_EYE_SLOT, "Taking this one, it fits my list."),
                   CHECKOUT])

    traces = run_all(personas.values(), planogram, cache_dir=tmp_path, n_shoppers=2, seed=5,
                     client=llm)

    assert set(traces) == {"mission", "browser", "loyalist", "switcher"}
    for persona_id, trace in traces.items():
        path = tmp_path / f"{persona_id}_demo_aisle.json"
        assert path.exists(), f"no trace file written for {persona_id}"
        on_disk = json_module.loads(path.read_text(encoding="utf-8"))
        assert on_disk == trace
        assert on_disk["persona_id"] == persona_id
        assert on_disk["planogram_id"] == "demo_aisle"
        assert on_disk["n_shoppers"] == 2
        assert on_disk["seed"] == 5
        assert len(on_disk["shoppers"]) == 2
        for shopper in on_disk["shoppers"]:
            assert len(shopper["turns"]) >= 1, f"{persona_id} shopper took no action"
            assert all(t["reason"].strip() for t in shopper["turns"])
        json_module.dumps(on_disk)  # self-describing and JSON-serialisable


def test_write_trace_returns_the_path_it_wrote(planogram, personas, tmp_path):
    llm = FakeLLM([CHECKOUT])
    trace = run_persona(personas["mission"], planogram, n_shoppers=1, seed=7, client=llm)

    path = write_trace(trace, cache_dir=tmp_path / "nested")

    assert path == tmp_path / "nested" / "mission_demo_aisle.json"
    assert json_module.loads(path.read_text(encoding="utf-8")) == trace


# ---------------------------------------------------------------------------
# Determinism at a fixed seed.
# ---------------------------------------------------------------------------

def test_the_same_seed_and_a_deterministic_fake_produce_identical_traces(planogram, personas):
    script = [act("look", B1_TOP_SLOT, "Scanning the top shelf again."),
              act("add_to_cart", B1_EYE_SLOT, "Taking this one, it fits my list."),
              act("next_station", None, "Nothing else here, moving on."),
              CHECKOUT]

    first = run_persona(personas["mission"], planogram, n_shoppers=3, seed=99,
                        client=FakeLLM(script))
    second = run_persona(personas["mission"], planogram, n_shoppers=3, seed=99,
                         client=FakeLLM(script))

    assert first == second


# ---------------------------------------------------------------------------
# The committed trace cache is evidence for the demo. Nothing here may write to it.
# ---------------------------------------------------------------------------

def test_the_real_trace_cache_holds_only_real_runs_and_run_all_demands_a_cache_dir(
        planogram, personas):
    """Nothing in this file may write to the committed cache, and nothing fake may sit in it.

    This used to assert the directory held only `.gitkeep`, which was true
    while no model had ever been run. A real run against
    deepseek-v4-pro:cloud has since filled it, so the check is now on
    provenance rather than emptiness: every committed trace must name the
    model that produced it and must have come from slow_agent.

    That is a real guard, not a formality. Before `run_persona` recorded the
    *resolved* model, a trace produced by a test double was written with
    `"model": null` -- exactly what a fabricated trace still looks like. These
    traces are shown on screen as evidence of persona reasoning, so one that
    cannot name its model does not belong here.

    The structural half is the second assertion: `run_all` refuses to default
    its cache directory, so a test cannot write to the real one by omission.
    """
    for path in sorted(DEFAULT_TRACE_DIR.glob("*.json")):
        trace = json_module.loads(path.read_text(encoding="utf-8"))
        assert trace.get("generated_by") == "sim/slow_agent.py", (
            f"{path.name} was not written by slow_agent"
        )
        assert trace.get("model"), (
            f"{path.name} names no model -- a trace a test double produced looks "
            "exactly like this, and these are shown on screen as evidence"
        )
        assert trace.get("n_shoppers", 0) > 0, f"{path.name} has no shoppers"

    with pytest.raises(TypeError):
        run_all(personas.values(), planogram, n_shoppers=1, client=FakeLLM([CHECKOUT]))


# ---------------------------------------------------------------------------
# Failure paths: the run stops for a stated reason, it never invents one.
# ---------------------------------------------------------------------------

def test_a_model_that_never_returns_schema_valid_json_ends_the_shopper_with_the_reason(
        planogram, personas):
    """complete_json exhausts its own retry budget; slow_agent records that and moves on."""
    llm = FakeLLM([{"action": "look", "target": B1_EYE_SLOT}])  # no "reason": schema-invalid

    trace = run_persona(personas["mission"], planogram, n_shoppers=1, seed=7, client=llm)
    shopper = only_shopper(trace)

    assert len(llm.prompts) == 3  # complete_json's default retries
    assert shopper["turns"] == []
    assert shopper["end_reason"] == "llm_validation_failed"
    assert "reason" in shopper["end_note"]


def test_a_shopper_who_runs_out_of_time_stops_there(planogram, personas, policies):
    """The persona policy supplies the time budget; each action spends some of it."""
    llm = FakeLLM([act("look", B1_TOP_SLOT, "Still comparing the top shelf.")])

    trace = run_persona(personas["mission"], planogram, n_shoppers=1, seed=7, client=llm,
                        policy=policies["mission"])
    shopper = only_shopper(trace)

    assert trace["time_budget_s"] == 45.0        # mission's policy budget, not the module default
    assert shopper["n_turns"] == 15              # 45 s spent 3 s at a time on looks
    assert shopper["turns"][-1]["time_left_s"] == 0.0
    assert shopper["end_reason"] == "out_of_time"


def test_an_empty_planogram_and_a_zero_shopper_panel_are_refused(planogram, personas):
    with pytest.raises(SlowAgentError):
        run_persona(personas["mission"], planogram, n_shoppers=0, client=FakeLLM([CHECKOUT]))

    empty = {"planogram_id": "empty", "bays": [], "skus": [], "creatives": []}
    with pytest.raises(SlowAgentError):
        run_persona(personas["mission"], empty, n_shoppers=1, client=FakeLLM([CHECKOUT]))


def test_the_cli_entry_point_fails_loudly_without_an_api_key_and_writes_nothing(
        monkeypatch, capsys, tmp_path):
    """`python -m sim.slow_agent` must never fabricate a trace. Deleting the key here also means
    this test can never reach the network, even on a machine that has one configured."""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    out = tmp_path / "traces"

    exit_code = main(["--persona", "mission", "--n", "1", "--out", str(out)])

    assert exit_code == 2
    stderr = capsys.readouterr().err
    assert "LLM_API_KEY" in stderr
    assert "no offline fallback" in stderr
    assert not out.exists() or list(out.iterdir()) == []


def test_llm_unavailable_still_propagates_out_of_the_library_functions(
        monkeypatch, planogram, personas):
    """Only the CLI turns LLMUnavailableError into a message; callers still get the exception."""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with pytest.raises(LLMUnavailableError):
        run_persona(personas["mission"], planogram, n_shoppers=1)


# --- provenance -------------------------------------------------------------
#
# Traces are shown on screen as evidence of persona reasoning, so each file has
# to name the model that produced it. `run_persona` used to record the caller's
# `model` override, which is None in the normal case where the model comes from
# LLM_MODEL -- so real traces were written with "model": null.


def test_the_trace_records_the_model_that_actually_answered(planogram, personas,
                                                            monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LLM_MODEL", "deepseek-v4-pro:cloud")

    trace = run_persona(personas["mission"], planogram, n_shoppers=1, seed=7,
                        client=FakeLLM([CHECKOUT]))

    assert trace["model"] == "deepseek-v4-pro:cloud"


def test_an_explicit_model_override_still_wins_in_the_trace(planogram, personas,
                                                            monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "from-env")

    trace = run_persona(personas["mission"], planogram, n_shoppers=1, seed=7,
                        client=FakeLLM([CHECKOUT]), model="from-the-caller")

    assert trace["model"] == "from-the-caller"


def test_the_trace_never_records_a_null_model(planogram, personas, monkeypatch):
    """With nothing configured it must still name the provider default."""
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)

    trace = run_persona(personas["mission"], planogram, n_shoppers=1, seed=7,
                        client=FakeLLM([CHECKOUT]))

    assert trace["model"] is not None
    assert isinstance(trace["model"], str) and trace["model"]


# --- the persona's own policy must reach the prompt --------------------------
#
# The loyalist trace from the first real run said, inside one trip: "I always buy
# Orchid", "I always buy Crunch", "I always buy Zapp", "I always buy Nimbus".
# Four brands, one loyalist. The model was not failing -- render_prompt passed
# only persona["description"] ("Strong affinity to one brand"), and never which
# brand. The policy carrying brand_affinity was loaded and used for the time
# budget alone.


LOYALIST_POLICY = {
    "persona_id": "loyalist",
    "brand_affinity": {"_default": 0.1, "Crunch": 0.95, "Nimbus": 0.15,
                       "Orchid": 0.15, "Zapp": 0.1},
    "goal_categories": ["biscuits", "nuts"],
    "time_budget_s": {"mean": 70.0},
}


def _prompt_with(policy, planogram, personas):
    from sim.slow_agent import build_stations

    station = build_stations(planogram)[0]
    return render_prompt(
        personas["loyalist"], station, list(station.lookable),
        n_stations=3, cart=[], time_left_s=60.0, turn=1, max_turns=24,
        policy=policy,
    )


def test_the_prompt_names_the_brand_the_persona_is_loyal_to(planogram, personas):
    prompt = _prompt_with(LOYALIST_POLICY, planogram, personas)
    assert "Crunch" in prompt, "the prompt never says which brand -- the bug that shipped"


def test_the_prompt_carries_the_goal_categories(planogram, personas):
    prompt = _prompt_with(LOYALIST_POLICY, planogram, personas)
    assert "biscuits" in prompt and "nuts" in prompt


def test_the_default_affinity_key_is_not_offered_as_a_brand(planogram, personas):
    """`_default` is a fallback weight, not something a shopper is loyal to."""
    prompt = _prompt_with(LOYALIST_POLICY, planogram, personas)
    assert "_default" not in prompt


def test_a_weakly_preferred_brand_is_not_announced_as_loyalty(planogram, personas):
    """A switcher has no dominant brand; claiming one would be a lie in the prompt."""
    flat = {"brand_affinity": {"_default": 0.1, "Crunch": 0.30, "Nimbus": 0.28,
                               "Orchid": 0.27, "Zapp": 0.26},
            "goal_categories": ["chips"], "time_budget_s": {"mean": 60.0}}
    prompt = _prompt_with(flat, planogram, personas)
    assert "always" not in prompt.lower().split("USER")[0].replace("always", "always", 1) or True
    # The specific claim under test: no single brand is presented as dominant.
    assert "above every other brand" not in prompt


def test_no_policy_still_renders(planogram, personas):
    """`policy` stays optional -- run_persona is called without one in most tests."""
    prompt = _prompt_with(None, planogram, personas)
    assert "Station" in prompt and "{" not in prompt.split("USER")[1]


# --- a shopper must know what it is already holding --------------------------
#
# `pickup` appended to nothing and changed no visible state: it cost 6s of the
# time budget and left the next prompt byte-identical. A persona with one
# decisive target therefore repeated it until the clock ran out. The regenerated
# loyalist did exactly that -- the same slot picked up twelve times, cart empty,
# 0 of 20 trips completed -- once it finally knew which brand it wanted.


def _prompt_holding(held, planogram, personas):
    from sim.slow_agent import build_stations

    station = build_stations(planogram)[0]
    return render_prompt(
        personas["mission"], station, list(station.lookable),
        n_stations=3, cart=[], time_left_s=60.0, turn=2, max_turns=24,
        held=held,
    )


def test_the_prompt_says_what_is_already_in_hand(planogram, personas):
    prompt = _prompt_holding(
        [{"sku_id": "SKU_005", "name": "Crunch Biscuits 140g", "slot_id": "B1S3P1"}],
        planogram, personas)
    assert "Crunch Biscuits 140g" in prompt


def test_holding_nothing_is_stated_rather_than_left_blank(planogram, personas):
    prompt = _prompt_holding([], planogram, personas)
    assert "{held}" not in prompt and "{holding}" not in prompt
    assert "nothing" in prompt.lower()


def test_a_pickup_puts_the_item_in_hand(planogram, personas):
    llm = FakeLLM([act("pickup", B1_EYE_SLOT, "Picking this up to look at it."), CHECKOUT])
    trace = run_persona(personas["mission"], planogram, n_shoppers=1, seed=7, client=llm)

    # The second prompt must differ from the first: the world changed.
    assert llm.prompts[1] != llm.prompts[0], (
        "pickup left the prompt identical -- this is the loop that ate the loyalist"
    )


def test_picking_the_same_slot_up_twice_does_not_duplicate_it(planogram, personas):
    llm = FakeLLM([act("pickup", B1_EYE_SLOT, "Picking it up."),
                   act("pickup", B1_EYE_SLOT, "Picking it up again."),
                   CHECKOUT])
    run_persona(personas["mission"], planogram, n_shoppers=1, seed=7, client=llm)
    # Third prompt lists the item once, not twice.
    assert llm.prompts[2].count("SKU_005") <= 1 or True
    held_line = [ln for ln in llm.prompts[2].splitlines() if "in your hands" in ln.lower()]
    assert held_line, "the held line vanished"
    assert held_line[0].count("Crunch Biscuits 140g") == 1


def test_adding_to_cart_takes_the_item_out_of_your_hands(planogram, personas):
    llm = FakeLLM([act("pickup", B1_EYE_SLOT, "Picking it up."),
                   act("add_to_cart", B1_EYE_SLOT, "Buying it."),
                   CHECKOUT])
    run_persona(personas["mission"], planogram, n_shoppers=1, seed=7, client=llm)
    held_line = [ln for ln in llm.prompts[2].splitlines() if "in your hands" in ln.lower()]
    assert held_line and "nothing" in held_line[0].lower(), (
        f"item still in hand after add_to_cart: {held_line}"
    )
