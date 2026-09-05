"""S19 -- `analytics/report.py`: a template report with an LLM-written headline.

PLAN section 13 replaced SPEC M7's regex number-grounding over a whole
LLM-written narrative with "template report; LLM writes the headline only".
That makes "the report contains no invented number" structural for the body
-- every figure in it is substituted from the numbers dict -- and leaves
exactly one free-text sentence to defend. These tests pin that sentence's
three behaviours: it falls back to a template when there is no LLM, it uses a
real model's headline when there is one, and it refuses a headline carrying a
number that is not in the input.
"""

import json
from typing import Any

import pytest

from analytics import report
from sim.llm_client import LLMClientError, LLMUnavailableError


# ---------------------------------------------------------------------------
# A fake transport for sim/llm_client.py: `.post(...)` -> `.json()`/.raise_for_status()
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: Any):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


class _FakeLLM:
    """Returns `headline` every time, in the Messages API envelope."""

    def __init__(self, headline: str):
        self.headline = headline
        self.calls = 0

    def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls += 1
        return _FakeResponse(
            {"content": [{"type": "text", "text": json.dumps({"headline": self.headline})}]}
        )


def _report_input(*, has_real_panel: bool = True) -> dict:
    """A complete report input, shaped the way scripts/eval.py builds it."""
    per_variant = [
        {
            "variant_id": "A",
            "name": "Baseline",
            "n_real": 6 if has_real_panel else 0,
            "attention_spearman": 0.62 if has_real_panel else None,
            "heatmap_kl": 0.14 if has_real_panel else None,
            "purchase_share_mae": 0.021 if has_real_panel else None,
            "purchase_share_mae_focal_category": 0.018 if has_real_panel else None,
            "ad_slot_index_spearman": 0.5 if has_real_panel else None,
            "real_focal_attention": 0.031 if has_real_panel else None,
            "real_focal_purchase_share": 0.04 if has_real_panel else None,
            "synth_focal_attention": 0.025,
            "synth_focal_purchase_share": 0.038,
            "synth_lift": 0.11,
            "focal_slot": "B1S5P1",
        },
        {
            "variant_id": "B",
            "name": "Focal SKU moved to eye level (known effect)",
            "n_real": 5 if has_real_panel else 0,
            "attention_spearman": 0.58 if has_real_panel else None,
            "heatmap_kl": 0.17 if has_real_panel else None,
            "purchase_share_mae": 0.024 if has_real_panel else None,
            "purchase_share_mae_focal_category": 0.02 if has_real_panel else None,
            "ad_slot_index_spearman": None,
            "real_focal_attention": 0.062 if has_real_panel else None,
            "real_focal_purchase_share": 0.07 if has_real_panel else None,
            "synth_focal_attention": 0.075,
            "synth_focal_purchase_share": 0.081,
            "synth_lift": 0.12,
            "focal_slot": "B1S3P2",
        },
    ]
    return {
        "experiment": {
            "experiment_id": "eval-0123456789ab",
            "fit_variant": "A",
            "holdout_variants": ["B"],
            "focal_sku": "SKU_008",
            "focal_category": "nuts",
            "focal_creative": "AD_1",
            "focal_brand": "Crunch",
            "kpi": "focal_sku_purchase_share",
            "n_synth": 10000,
            "seed": 42,
            "n_splits": 200,
            "n_boot": 1000,
            "ci_percent": 95,
        },
        "panel": {
            "n_real_accepted": 11 if has_real_panel else 0,
            "n_real_rejected": 2 if has_real_panel else 0,
            "n_synth": 10000,
            "mode_split": {"webcam": 7, "cursor_only": 4} if has_real_panel else {},
            "reject_reasons": [{"reason": "too_short", "n": 2}] if has_real_panel else [],
            "fusion_mode": "webcam" if has_real_panel else "cursor_only",
            "has_real_panel": has_real_panel,
        },
        "pre_registration": {
            "n_locks_found": 13 if has_real_panel else 0,
            "n_locks_verified": 13 if has_real_panel else 0,
            "n_ordering_checked": 11 if has_real_panel else 0,
            "notes": [],
        },
        "per_variant": per_variant,
        "noise_ceiling": (
            {"variant_id": "A", "spearman_mean": 0.71, "ci95": [0.55, 0.84], "n_splits": 200}
            if has_real_panel
            else None
        ),
        "relative_agreement": 0.87 if has_real_panel else None,
        "calibration": (
            {
                "fit": {
                    "variant_id": "A",
                    "shares": {"browser": 0.25, "loyalist": 0.2, "mission": 0.4, "switcher": 0.15},
                    "objective": 0.44,
                    "attention_spearman": 0.66,
                    "purchase_share_mae": 0.02,
                    "n_candidates": 1771,
                },
                "holdout": [
                    {
                        "variant_id": "B",
                        "objective": 0.52,
                        "attention_spearman": 0.58,
                        "purchase_share_mae": 0.024,
                    }
                ],
            }
            if has_real_panel
            else None
        ),
        "known_effect": {
            "focal_slot_a": "B1S5P1",
            "focal_slot_b": "B1S3P2",
            "real_att_a": 0.031 if has_real_panel else None,
            "real_att_b": 0.062 if has_real_panel else None,
            "synth_att_a": 0.025,
            "synth_att_b": 0.075,
            "real_uplift": 1.0 if has_real_panel else None,
            "synth_uplift": 2.0,
            "same_direction": True if has_real_panel else None,
        },
        "ad_to_purchase_lift": {
            "variant_id": "A",
            "rows": [
                {
                    "row": "population",
                    "real": 0.19 if has_real_panel else None,
                    "ci95": [0.08, 0.31] if has_real_panel else None,
                    "synth": 0.11,
                }
            ],
        },
        "decision_agreement": (
            {"kpi": "focal_sku_purchase_share", "winner_real": "B", "winner_synth": "B", "agree": True}
            if has_real_panel
            else None
        ),
        "figures": {"written": ["heatmap_A.png"], "skipped": []},
        "unavailable": [] if has_real_panel else ["no accepted real sessions were found"],
    }


# ---------------------------------------------------------------------------
# Offline: the LLM is unavailable and the report still renders
# ---------------------------------------------------------------------------


def test_no_llm_falls_back_to_the_template_headline(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_OFFLINE", raising=False)

    data = _report_input()

    # The precondition this test exists for: complete_json really does raise.
    with pytest.raises(LLMUnavailableError):
        report.request_headline(data, client=None)

    headline, source = report.headline(data)

    assert source == report.SOURCE_TEMPLATE
    assert headline == report.template_headline(data)
    assert headline.strip() != ""


def test_offline_env_flag_also_falls_back(monkeypatch):
    monkeypatch.setenv("LLM_OFFLINE", "1")
    monkeypatch.setenv("LLM_API_KEY", "not-used-when-offline")

    headline, source = report.headline(_report_input())

    assert source == report.SOURCE_TEMPLATE
    assert headline == report.template_headline(_report_input())


def test_the_whole_report_renders_offline(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_OFFLINE", raising=False)

    markdown = report.render(_report_input())

    assert markdown.startswith("# Results")
    assert "Do not edit by hand" in markdown
    assert report.template_headline(_report_input()) in markdown


# ---------------------------------------------------------------------------
# With an LLM: its headline is used, unless it invents a number
# ---------------------------------------------------------------------------


def test_a_grounded_llm_headline_is_used(monkeypatch):
    monkeypatch.delenv("LLM_OFFLINE", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    data = _report_input()
    client = _FakeLLM("Synthetic shoppers reach 0.87 of the real panel's own repeatability.")

    headline, source = report.headline(data, client=client)

    assert client.calls == 1
    assert source == report.SOURCE_LLM
    assert headline == "Synthetic shoppers reach 0.87 of the real panel's own repeatability."
    assert headline in report.render(data, client=client)


def test_a_headline_with_no_numbers_at_all_is_accepted(monkeypatch):
    monkeypatch.delenv("LLM_OFFLINE", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    client = _FakeLLM("The synthetic panel picked the same winning variant as the real one.")

    headline, source = report.headline(_report_input(), client=client)

    assert source == report.SOURCE_LLM
    assert headline == "The synthetic panel picked the same winning variant as the real one."


def test_a_headline_containing_a_foreign_number_is_rejected(monkeypatch):
    """The documented policy: reject and fall back to the template.

    The body of the report is template-filled, so this one sentence is the
    only place an invented number could enter the document. 4242 appears
    nowhere in the input, so the headline is not grounded and is dropped.
    """
    monkeypatch.delenv("LLM_OFFLINE", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    data = _report_input()
    client = _FakeLLM("The synthetic panel matched the real one across 4242 shoppers.")

    headline, source = report.headline(data, client=client)

    assert source == report.SOURCE_TEMPLATE_UNGROUNDED
    assert headline == report.template_headline(data)
    assert "4242" not in report.render(data, client=client)


def test_a_headline_that_bends_a_real_number_is_rejected(monkeypatch):
    """0.87 is in the input; 0.97 is not. A single mistyped digit is exactly
    the failure this guard exists for, and it must not survive."""
    monkeypatch.delenv("LLM_OFFLINE", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    data = _report_input()
    client = _FakeLLM("Synthetic shoppers reach 0.97 of the real panel's own repeatability.")

    headline, source = report.headline(data, client=client)

    assert source == report.SOURCE_TEMPLATE_UNGROUNDED
    assert headline == report.template_headline(data)


def test_percentage_renderings_of_an_input_number_are_grounded(monkeypatch):
    """relative_agreement is stored as 0.87; "87%" is the same number, and a
    headline for a brand manager will say it that way."""
    monkeypatch.delenv("LLM_OFFLINE", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    client = _FakeLLM("The synthetic panel reaches 87% of the real panel's own repeatability.")

    headline, source = report.headline(_report_input(), client=client)

    assert source == report.SOURCE_LLM


def test_the_template_headline_quotes_the_ceiling_beside_the_ratio():
    """`relative_agreement` is clamped to 1.0, so a panel that barely repeats
    still produces 1.00. The headline must not make that claim naked."""
    data = _report_input()
    data["relative_agreement"] = 1.0
    data["noise_ceiling"] = {
        "variant_id": "A", "spearman_mean": 0.03, "ci95": [-0.18, 0.28], "n_splits": 200
    }

    sentence = report.template_headline(data)

    assert "1.00" in sentence
    assert "0.03" in sentence
    assert report.is_grounded(sentence, data)


def test_the_template_headline_is_itself_grounded():
    """It is built by substitution from the same numbers, so it must pass the
    check it is the fallback for -- otherwise the guard is arbitrary."""
    data = _report_input()
    assert report.is_grounded(report.template_headline(data), data)

    empty = _report_input(has_real_panel=False)
    assert report.is_grounded(report.template_headline(empty), empty)


def test_a_broken_llm_response_falls_back_rather_than_raising(monkeypatch):
    """`complete_json` gives up with LLMValidationError after its retries; a
    report generator must not turn that into a failed build."""
    monkeypatch.delenv("LLM_OFFLINE", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    class _Garbage:
        def post(self, url: str, **kwargs: Any) -> _FakeResponse:
            return _FakeResponse({"content": [{"type": "text", "text": "not json at all"}]})

    with pytest.raises(LLMClientError):
        report.request_headline(_report_input(), client=_Garbage())

    headline, source = report.headline(_report_input(), client=_Garbage())

    assert source == report.SOURCE_TEMPLATE
    assert headline == report.template_headline(_report_input())


# ---------------------------------------------------------------------------
# The empty panel is never rendered as a zero
# ---------------------------------------------------------------------------


def test_an_empty_panel_renders_not_yet_collected_instead_of_zero(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    data = _report_input(has_real_panel=False)

    markdown = report.render(data)

    assert report.NOT_COLLECTED in markdown
    assert "n = 0 accepted" in markdown
    assert "no accepted real sessions were found" in markdown


def test_the_prompt_template_has_a_user_side_and_no_skeleton_marker():
    template = report.PROMPT_PATH.read_text(encoding="utf-8")

    assert "Skeleton only" not in template
    assert "SYSTEM" in template
    assert "USER" in template
    assert "{results_json}" in template
    # SPEC M7's instruction, which is what makes the headline check enforceable.
    assert "Use only numbers present in the input JSON" in template


def test_the_prompt_carries_the_numbers_it_is_grounded_against():
    data = _report_input()
    prompt = report.render_prompt(data)

    body = prompt.split("USER", 1)[1]
    payload = json.loads(body[body.index("{") : body.rindex("}") + 1])

    assert payload["relative_agreement"] == 0.87
    assert payload["panel"]["n_real_accepted"] == 11


# ---------------------------------------------------------------------------
# The lift table carries two intervals, and never confuses them
# ---------------------------------------------------------------------------


def test_the_lift_table_prints_the_real_and_synthetic_intervals_in_separate_columns():
    """`ci95` is the real panel's bootstrap over shoppers; `synth_mc95` is the
    simulator's Monte Carlo spread at this run size. They are different kinds
    of object and a reader must not be able to mistake one for the other."""
    data = _report_input()
    data["ad_to_purchase_lift"]["rows"][0]["synth_mc95"] = [0.06, 0.17]

    markdown = report.render(data, headline_text="Headline.")
    section = markdown.split("## Ad-to-Purchase Lift", 1)[1].split("\n## ", 1)[0]

    assert "0.08 to 0.31" in section  # the real panel's ci95
    assert "0.06 to 0.17" in section  # the synthetic Monte Carlo spread
    assert "not a confidence interval" in section.lower()
    assert "monte carlo" in section.lower()


def test_a_row_without_a_synthetic_interval_renders_not_collected():
    """A SimResult predating the purchase-event counts carries no interval.
    That is an absence, and the report's one formatting rule is that an
    absence never renders as a number."""
    data = _report_input()
    assert "synth_mc95" not in data["ad_to_purchase_lift"]["rows"][0]

    markdown = report.render(data, headline_text="Headline.")
    section = markdown.split("## Ad-to-Purchase Lift", 1)[1].split("\n## ", 1)[0]
    row = [line for line in section.splitlines() if line.startswith("| population")]

    assert len(row) == 1
    assert row[0].count(report.NOT_COLLECTED) == 1
