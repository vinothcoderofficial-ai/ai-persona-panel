"""RESULTS.md: every number substituted from computed values, one sentence
written by a language model.

PLAN section 13 overrides SPEC M7 here:

    | `report.py` regex number-grounding | **Template report; LLM writes the
      headline only** |

So the body of this report is not generated text at all -- it is a template
with computed values dropped into it, which makes "the report contains no
invented number" a property of the code rather than a check bolted onto
generated prose. The LLM's whole surface area is one headline sentence.

Why there is still a grounding check on that sentence
-----------------------------------------------------
The cut thing is the narrative-wide regex post-check: extracting every number
from six generated bullets and re-validating them, then regenerating. That is
gone with the bullets. What remains is a single sentence, and this module
still refuses it if it carries a number that is not in the report input,
falling back to the template headline. The policy is **reject and fall back**,
never "repair" and never "use it anyway with a warning" -- a headline is the
one line of RESULTS.md a reader is guaranteed to read, and a plausible wrong
number there is worse than a flat template sentence.

The check is deliberately generous about *form* and strict about *value*:
0.87 may be written "0.87", "0.9" or "87%", and a magnitude may be quoted
without its sign (a headline says "fell by 50%", not "-0.5"), because a check
that rejects natural phrasing would make the LLM path dead code. It is not
generous about digits: 0.97 for 0.87 is rejected, which is the mistake that
actually happens.

Offline is a first-class path
-----------------------------
`sim/llm_client.complete_json` raises `LLMUnavailableError` when there is no
API key and `LLM_OFFLINE=1` likewise. `make eval` must work on a reviewer's
machine with neither, so every failure to obtain a headline -- unavailable,
unparseable, schema-invalid, network error -- lands on the template headline
and the report renders. A report generator that can fail the build because a
model was unreachable would be a bad trade for one sentence.

The report input
----------------
`scripts/eval.py` builds a single plain-data dict and hands it here. It holds
numbers, ids and fixed labels; no prose. Its shape is documented on `render`
and is the same object that is serialised into the prompt, so the sentence is
grounded against exactly what the tables show.

Pure apart from reading the prompt template: no wall-clock, no RNG, no
figures. `render()` on the same input always returns the same string, which
is what lets `make eval` regenerate RESULTS.md byte-identically (SPEC M8).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional, Sequence

import httpx

from sim.llm_client import LLMClientError, complete_json

ROOT = Path(__file__).resolve().parents[1]
PROMPT_PATH = ROOT / "sim" / "prompts" / "narrative.md"

# What a real-panel cell says when there is no real panel. Never "0.00": a
# zero is a measurement, and this is the absence of one.
NOT_COLLECTED = "not yet collected"

# Where the headline came from, reported by `headline()` and printed by
# scripts/eval.py so the provenance of the one generated sentence is visible.
SOURCE_LLM = "llm"
SOURCE_TEMPLATE = "template"
SOURCE_TEMPLATE_UNGROUNDED = "template (llm headline rejected: ungrounded number)"

HEADLINE_SCHEMA = {
    "type": "object",
    "required": ["headline"],
    "additionalProperties": False,
    "properties": {"headline": {"type": "string", "minLength": 1, "maxLength": 300}},
}

# A number as it appears in a sentence: optional sign, digits with optional
# thousands separators, optional decimal part, optional percent sign. The
# lookarounds keep it out of identifiers -- "B1S5P1", "SKU_008" and "AD_1" are
# slot, sku and creative ids, not quantities, and a headline naming one must
# not be rejected for it.
_NUMBER_IN_TEXT = re.compile(
    r"(?<![A-Za-z0-9_.])[-+]?\d[\d,]*(?:\.\d+)?%?(?![A-Za-z0-9_])"
)


# ---------------------------------------------------------------------------
# The headline
# ---------------------------------------------------------------------------


def render_prompt(data: Mapping[str, Any]) -> str:
    """`sim/prompts/narrative.md` with the report input substituted in.

    Substituted with `str.replace`, not `str.format`: the template shows the
    model the JSON envelope it must answer in, so it contains literal braces,
    and the payload is JSON and contains many more. `format` would have to
    escape both. The placeholder is still spelled `{results_json}` so the file
    reads like the project's other prompt templates.

    The payload is `sort_keys=True`, so the same report input always produces
    the same prompt -- which matters for a cached or temperature-0 model.
    """
    template = PROMPT_PATH.read_text(encoding="utf-8")
    payload = json.dumps(data, sort_keys=True, indent=2)
    return template.replace("{results_json}", payload)


def request_headline(
    data: Mapping[str, Any], *, client: Any = None, model: Optional[str] = None
) -> str:
    """Ask the LLM for a headline. Raises rather than falling back.

    This is the raw call: `LLMUnavailableError` when there is no key,
    `LLMValidationError` when the model never produced `{"headline": ...}`.
    `headline()` is the one that catches those; this stays sharp so the tests
    can assert the failure really happens instead of assuming it.
    """
    return str(
        complete_json(
            render_prompt(data),
            HEADLINE_SCHEMA,
            temperature=0.0,
            client=client,
            model=model,
        )["headline"]
    ).strip()


def headline(
    data: Mapping[str, Any], *, client: Any = None, model: Optional[str] = None
) -> tuple[str, str]:
    """`(sentence, source)` -- the headline for this report, and where it came from.

    Three outcomes, and only the first involves the model's words:

      * `SOURCE_LLM` -- the model answered and every number in its sentence
        is in `data`.
      * `SOURCE_TEMPLATE_UNGROUNDED` -- the model answered and it was not, so
        the sentence is discarded.
      * `SOURCE_TEMPLATE` -- there was no usable answer at all (no API key,
        `LLM_OFFLINE=1`, a network error, or no schema-valid JSON within the
        retry budget).

    Never raises. A report that cannot be produced because a model was
    unreachable is worse than a report with a plain sentence at the top.
    """
    try:
        candidate = request_headline(data, client=client, model=model)
    except (LLMClientError, httpx.HTTPError, OSError):
        return template_headline(data), SOURCE_TEMPLATE

    if not candidate:
        return template_headline(data), SOURCE_TEMPLATE
    if not is_grounded(candidate, data):
        return template_headline(data), SOURCE_TEMPLATE_UNGROUNDED
    return candidate, SOURCE_LLM


def template_headline(data: Mapping[str, Any]) -> str:
    """The fallback sentence, built by substitution from the same numbers.

    Says the strongest thing the numbers actually support, in the same order
    of preference the prompt gives the model, and says nothing when nothing
    supports it. It is itself grounded -- every figure in it comes out of
    `data` -- which is asserted by the tests, because a fallback that could
    not pass the check it stands in for would make the check arbitrary.
    """
    panel = data["panel"]
    experiment = data["experiment"]
    known = data.get("known_effect") or {}
    decision = data.get("decision_agreement")
    relative = data.get("relative_agreement")

    if relative is not None:
        # The ceiling is quoted alongside the ratio, always. `relative_agreement`
        # is clamped to 1.0, so a panel that barely repeats at all produces a
        # ratio of 1.00 -- a headline claiming that on its own would be the
        # single most misleading sentence this report could open with. Naming
        # the ceiling in the same breath makes the claim self-limiting, and it
        # is what every accuracy number here is quoted against anyway.
        ceiling = (data.get("noise_ceiling") or {}).get("spearman_mean")
        return (
            f"The synthetic panel reached {_fmt(relative)} of the real panel's own "
            f"repeatability (ceiling {_fmt(ceiling)}) across "
            f"{_fmt_int(panel['n_real_accepted'])} accepted sessions."
        )
    if known.get("same_direction") is True:
        return (
            f"Both panels moved {experiment['focal_sku']} the same way at eye level: "
            f"real {_fmt(known.get('real_uplift'))}, synthetic {_fmt(known.get('synth_uplift'))}."
        )
    if decision is not None:
        verdict = "the same" if decision["agree"] else "a different"
        return (
            f"The synthetic panel picked {verdict} winning variant as the real panel "
            f"on {decision['kpi']}."
        )
    return (
        f"The synthetic panel ran {_fmt_int(panel['n_synth'])} shoppers per variant; "
        f"the real panel is {NOT_COLLECTED}."
    )


# ---------------------------------------------------------------------------
# Number grounding, for the headline only
# ---------------------------------------------------------------------------


def numbers_in(text: str) -> list[str]:
    """Every numeric token in `text`, normalised (commas and `%` stripped)."""
    return [
        match.group(0).replace(",", "").rstrip("%")
        for match in _NUMBER_IN_TEXT.finditer(text)
    ]


def allowed_number_strings(data: Mapping[str, Any]) -> set[str]:
    """Every rendering of every number in `data` a headline may legitimately use.

    For each numeric leaf: the value at 0-3 decimal places, its integer form
    when it is integral, and its percentage form -- 0.87 covers "0.87", "0.9",
    "1" and "87". Magnitudes are also allowed without their sign, so "fell by
    50%" grounds against -0.5; the direction of a change is carried by the
    words around the number, and the sentence is checked for invented
    quantities, not parsed for meaning.
    """
    allowed: set[str] = set()
    for value in _numeric_leaves(data):
        allowed.update(_renderings(value))
        if value < 0:
            allowed.update(_renderings(-value))
    return allowed


def is_grounded(text: str, data: Mapping[str, Any]) -> bool:
    """Is every number in `text` present in `data`?

    A sentence with no numbers at all is grounded -- "the synthetic panel
    picked the same winning variant" is a claim about `decision_agreement`,
    and the model was given that field.
    """
    allowed = allowed_number_strings(data)
    return all(token in allowed for token in numbers_in(text))


def _renderings(value: float) -> Iterator[str]:
    yield f"{value:.0f}"
    yield f"{value:.1f}"
    yield f"{value:.2f}"
    yield f"{value:.3f}"
    if float(value).is_integer():
        yield str(int(value))
        yield f"{int(value):,}".replace(",", "")
    percent = value * 100.0
    yield f"{percent:.0f}"
    yield f"{percent:.1f}"
    yield f"{percent:.2f}"


def _numeric_leaves(node: Any) -> Iterator[float]:
    """Every int/float anywhere in the report input. `bool` is not a number
    here -- `True` is a finding, not a quantity, and `1` must not become
    quotable because a flag happened to be set."""
    if isinstance(node, bool):
        return
    if isinstance(node, (int, float)):
        yield float(node)
    elif isinstance(node, Mapping):
        for value in node.values():
            yield from _numeric_leaves(value)
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from _numeric_leaves(value)


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------


def render(
    data: Mapping[str, Any],
    *,
    client: Any = None,
    model: Optional[str] = None,
    headline_text: Optional[str] = None,
) -> str:
    """The whole of RESULTS.md, as a string.

    `data` is the report input `scripts/eval.py` builds:

        experiment        ids and constants: experiment_id, fit_variant,
                          holdout_variants, focal sku/category/creative/brand,
                          kpi, n_synth, seed, n_splits, n_boot, ci_percent
        panel             n_real_accepted / n_real_rejected / n_synth,
                          mode_split, reject_reasons, fusion_mode,
                          has_real_panel
        pre_registration  n_locks_found / n_locks_verified /
                          n_ordering_checked, notes
        per_variant       one row per variant: n_real and every per-variant
                          metric, real entries None when uncollected
        noise_ceiling     block + the variant it was measured on, or None
        relative_agreement  float or None
        calibration       {fit, holdout[]} or None
        known_effect      uplifts, the two focal slots, the four attentions
        ad_to_purchase_lift  {variant_id, rows[]}
        decision_agreement   block or None
        figures           {written[], skipped[{name, reason}]}
        unavailable       plain reasons a reader is owed, e.g. "no accepted
                          real sessions were found"

    Pass `headline_text` to render a sentence already obtained (so a caller
    can report its provenance); otherwise `headline()` is called here.

    Every real-panel value that is None renders as `NOT_COLLECTED`. That is
    the single most important formatting rule in this module: an omitted row
    or a `0.00` in a real column would be read as a measured zero, and the
    project's honesty claim dies on exactly that kind of cell.
    """
    if headline_text is None:
        headline_text, _source = headline(data, client=client, model=model)

    lines: list[str] = []
    lines.append("# Results")
    lines.append("")
    lines.append(
        "> Generated by `make eval` from the committed sessions and prediction locks. "
        "Do not edit by hand."
    )
    lines.append(
        "> Every number below is substituted from computed values. Only the headline "
        "sentence may be written by a language model, and it is rejected if it "
        "contains a number that is not in the report input."
    )
    lines.append("")
    lines.append(headline_text)
    lines.append("")

    _panel_section(lines, data)
    _pre_registration_section(lines, data)
    _agreement_section(lines, data)
    _ceiling_section(lines, data)
    _calibration_section(lines, data)
    _known_effect_section(lines, data)
    _lift_section(lines, data)
    _decision_section(lines, data)
    _synthetic_section(lines, data)
    _figures_section(lines, data)
    _unavailable_section(lines, data)

    return "\n".join(lines).rstrip("\n") + "\n"


def _panel_section(lines: list[str], data: Mapping[str, Any]) -> None:
    panel = data["panel"]
    experiment = data["experiment"]
    accepted = panel["n_real_accepted"]

    lines.append("## Panel")
    lines.append("")
    lines.append(f"**Real panel: n = {_fmt_int(accepted)} accepted**, "
                 f"{_fmt_int(panel['n_real_rejected'])} rejected.")
    lines.append("")
    lines.append(f"**Synthetic panel: n = {_fmt_int(panel['n_synth'])} shoppers per variant** "
                 f"across 4 personas, seed {_fmt_int(experiment['seed'])}.")
    lines.append("")
    lines.append(f"- Experiment id: `{experiment['experiment_id']}`")
    lines.append(f"- Fit variant: `{experiment['fit_variant']}`; "
                 f"holdout: {_id_list(experiment['holdout_variants'])}")
    lines.append(f"- Focal SKU `{experiment['focal_sku']}` "
                 f"(category `{experiment['focal_category']}`); "
                 f"focal creative `{experiment['focal_creative']}` "
                 f"(brand `{experiment['focal_brand']}`)")
    lines.append(f"- Synthetic attention fused in `{panel['fusion_mode']}` mode")

    if panel["mode_split"]:
        split = ", ".join(
            f"{mode} {_fmt_int(count)}" for mode, count in sorted(panel["mode_split"].items())
        )
        lines.append(f"- Capture mode of the accepted panel: {split}")
    else:
        lines.append(f"- Capture mode of the accepted panel: {NOT_COLLECTED}")

    if panel["reject_reasons"]:
        reasons = ", ".join(
            f"{row['reason']} {_fmt_int(row['n'])}" for row in panel["reject_reasons"]
        )
        lines.append(f"- Reject reasons: {reasons}")
    elif accepted or panel["n_real_rejected"]:
        lines.append("- Reject reasons: none recorded")
    else:
        lines.append(f"- Reject reasons: {NOT_COLLECTED}")
    lines.append("")


def _pre_registration_section(lines: list[str], data: Mapping[str, Any]) -> None:
    pre = data["pre_registration"]
    lines.append("## Pre-registration")
    lines.append("")
    lines.append(
        "Each session's synthetic prediction was locked and hashed on `POST /sessions`, "
        "before any event could be accepted. `scripts/eval.py` re-verifies that from the "
        "committed files and fails the build if it does not hold."
    )
    lines.append("")
    lines.append(f"- Prediction locks found: {_fmt_int(pre['n_locks_found'])}")
    lines.append(f"- `sha256` recomputed and matched: {_fmt_int(pre['n_locks_verified'])}")
    lines.append(
        f"- Locks verified to predate their session's first event: "
        f"{_fmt_int(pre['n_ordering_checked'])}"
    )
    for note in pre["notes"]:
        lines.append(f"- Note: {note}")
    lines.append("")


def _agreement_section(lines: list[str], data: Mapping[str, Any]) -> None:
    lines.append("## Real vs synthetic, per variant")
    lines.append("")
    lines.append(
        "| Variant | Real n | Attention Spearman | Heatmap KL | Purchase-share MAE "
        "| MAE (focal category) | Ad Slot Index Spearman |"
    )
    lines.append("|---|---|---|---|---|---|---|")
    for row in data["per_variant"]:
        lines.append(
            f"| {row['variant_id']} — {row['name']} "
            f"| {_fmt_int(row['n_real'])} "
            f"| {_fmt(row['attention_spearman'])} "
            f"| {_fmt(row['heatmap_kl'], 3)} "
            f"| {_fmt(row['purchase_share_mae'], 3)} "
            f"| {_fmt(row['purchase_share_mae_focal_category'], 3)} "
            f"| {_fmt(row['ad_slot_index_spearman'])} |"
        )
    lines.append("")


def _ceiling_section(lines: list[str], data: Mapping[str, Any]) -> None:
    ceiling = data.get("noise_ceiling")
    relative = data.get("relative_agreement")

    lines.append("## Noise ceiling and relative agreement")
    lines.append("")
    if ceiling is None:
        lines.append(
            f"Split-half repeatability of the real panel: {NOT_COLLECTED}. "
            "Every accuracy number in this report is quoted against this ceiling, "
            "so none of them can be interpreted until it exists."
        )
        lines.append("")
        lines.append(f"- Relative agreement: {NOT_COLLECTED}")
        lines.append("")
        return

    lines.append(
        f"Measured on variant `{ceiling['variant_id']}` over "
        f"{_fmt_int(ceiling['n_splits'])} half-splits."
    )
    lines.append("")
    lines.append(
        f"- Real panel vs itself: {_fmt(ceiling['spearman_mean'])} "
        f"({_fmt_int(data['experiment']['ci_percent'])}% interval "
        f"{_fmt(ceiling['ci95'][0])} to {_fmt(ceiling['ci95'][1])})"
    )
    lines.append(f"- Relative agreement (synthetic / ceiling): {_fmt(relative)}")
    lines.append("")


def _calibration_section(lines: list[str], data: Mapping[str, Any]) -> None:
    calibration = data.get("calibration")
    experiment = data["experiment"]

    lines.append("## Calibration — fit and holdout")
    lines.append("")
    if calibration is None:
        lines.append(
            f"Persona shares are fitted on variant `{experiment['fit_variant']}` only, with "
            f"{_id_list(experiment['holdout_variants'])} held out. Not run: {NOT_COLLECTED}."
        )
        lines.append("")
        return

    fit = calibration["fit"]
    shares = ", ".join(
        f"`{persona}` {_fmt(share)}" for persona, share in sorted(fit["shares"].items())
    )
    lines.append(
        f"Grid search over the 4 persona shares on variant `{fit['variant_id']}` only "
        f"({_fmt_int(fit['n_candidates'])} candidates). "
        f"{_id_list(experiment['holdout_variants'])} were never fitted."
    )
    lines.append("")
    lines.append(f"- Fitted shares: {shares}")
    lines.append("")
    lines.append("| Variant | Role | Objective | Attention Spearman | Purchase-share MAE |")
    lines.append("|---|---|---|---|---|")
    lines.append(
        f"| {fit['variant_id']} | fit | {_fmt(fit['objective'], 3)} "
        f"| {_fmt(fit['attention_spearman'])} | {_fmt(fit['purchase_share_mae'], 3)} |"
    )
    for row in calibration["holdout"]:
        lines.append(
            f"| {row['variant_id']} | holdout | {_fmt(row['objective'], 3)} "
            f"| {_fmt(row['attention_spearman'])} | {_fmt(row['purchase_share_mae'], 3)} |"
        )
    lines.append("")


def _known_effect_section(lines: list[str], data: Mapping[str, Any]) -> None:
    known = data["known_effect"]
    experiment = data["experiment"]

    lines.append("## Known effect — the focal SKU at eye level")
    lines.append("")
    lines.append(
        f"Variant B moves `{experiment['focal_sku']}` from `{known['focal_slot_a']}` "
        f"(bottom shelf) to `{known['focal_slot_b']}` (eye level). The slot is looked up "
        "in each variant's own resolved planogram, so both sides measure the SKU rather "
        "than a fixed shelf position."
    )
    lines.append("")
    lines.append("| Panel | Attention under A | Attention under B | Uplift |")
    lines.append("|---|---|---|---|")
    lines.append(
        f"| real | {_fmt(known['real_att_a'], 4)} | {_fmt(known['real_att_b'], 4)} "
        f"| {_fmt(known['real_uplift'])} |"
    )
    lines.append(
        f"| synthetic | {_fmt(known['synth_att_a'], 4)} | {_fmt(known['synth_att_b'], 4)} "
        f"| {_fmt(known['synth_uplift'])} |"
    )
    lines.append("")
    lines.append(f"- Same direction: {_fmt_flag(known['same_direction'])}")
    lines.append("")


def _lift_section(lines: list[str], data: Mapping[str, Any]) -> None:
    block = data["ad_to_purchase_lift"]
    experiment = data["experiment"]

    lines.append("## Ad-to-Purchase Lift")
    lines.append("")
    lines.append(
        f"Purchase share of `{experiment['focal_brand']}` among shoppers exposed to "
        f"`{experiment['focal_creative']}` versus not, on variant `{block['variant_id']}`. "
        f"The {_fmt_int(experiment['ci_percent'])}% CI is a bootstrap over "
        f"{_fmt_int(experiment['n_boot'])} resamples of the real panel's shoppers."
    )
    lines.append("")
    lines.append(
        "The last column is the synthetic panel's **Monte Carlo spread**, from resampling that "
        f"run's own purchase events {_fmt_int(experiment['n_boot'])} times. It is **not a "
        "confidence interval**: the synthetic panel is not a sample drawn from a population, so "
        "the spread says only whether the synthetic number is resolved at this run size. A wide "
        "one means too few synthetic purchase events, not a disagreement with the real panel."
    )
    lines.append("")
    lines.append(
        f"| Segment | Real lift | {_fmt_int(experiment['ci_percent'])}% CI | Synthetic lift "
        "| Synthetic MC spread |"
    )
    lines.append("|---|---|---|---|---|")
    for row in block["rows"]:
        lines.append(
            f"| {row['row']} | {_fmt(row.get('real'))} | {_fmt_interval(row.get('ci95'))} "
            f"| {_fmt(row.get('synth'))} | {_fmt_interval(row.get('synth_mc95'))} |"
        )
    lines.append("")


def _decision_section(lines: list[str], data: Mapping[str, Any]) -> None:
    decision = data.get("decision_agreement")
    experiment = data["experiment"]

    lines.append("## Decision agreement")
    lines.append("")
    if decision is None:
        lines.append(
            f"Would both panels recommend the same variant on `{experiment['kpi']}`? "
            f"Real winner: {NOT_COLLECTED}."
        )
        lines.append("")
        return
    lines.append(f"- KPI: `{decision['kpi']}`")
    lines.append(f"- Real panel's winner: `{decision['winner_real']}`")
    lines.append(f"- Synthetic panel's winner: `{decision['winner_synth']}`")
    lines.append(f"- Agree: {_fmt_flag(decision['agree'])}")
    lines.append("")


def _synthetic_section(lines: list[str], data: Mapping[str, Any]) -> None:
    """The synthetic panel alone -- the part of the study that does not need a
    real panel, and therefore the part that exists today."""
    experiment = data["experiment"]

    lines.append("## Synthetic panel on its own")
    lines.append("")
    lines.append(
        f"Computed from the committed planogram and variants with no real panel involved: "
        f"{_fmt_int(experiment['n_synth'])} shoppers per variant at seed "
        f"{_fmt_int(experiment['seed'])}."
    )
    lines.append("")
    lines.append(
        f"| Variant | Focal slot | Focal attention | Focal purchase share "
        f"| Ad-to-Purchase Lift ({experiment['focal_brand']}) |"
    )
    lines.append("|---|---|---|---|---|")
    for row in data["per_variant"]:
        lines.append(
            f"| {row['variant_id']} | `{row['focal_slot']}` "
            f"| {_fmt(row['synth_focal_attention'], 4)} "
            f"| {_fmt(row['synth_focal_purchase_share'], 4)} "
            f"| {_fmt(row['synth_lift'])} |"
        )
    lines.append("")


def _figures_section(lines: list[str], data: Mapping[str, Any]) -> None:
    figures = data["figures"]
    lines.append("## Figures")
    lines.append("")
    if figures["written"]:
        for name in figures["written"]:
            lines.append(f"- `docs/figures/{name}`")
    else:
        lines.append("- none")
    for skipped in figures["skipped"]:
        lines.append(f"- `docs/figures/{skipped['name']}` — not drawn: {skipped['reason']}")
    lines.append("")


def _unavailable_section(lines: list[str], data: Mapping[str, Any]) -> None:
    if not data["unavailable"]:
        return
    lines.append("## Not yet measured")
    lines.append("")
    lines.append(
        "Every item below is missing, not zero. No number in this report was substituted "
        "for one of them."
    )
    lines.append("")
    for reason in data["unavailable"]:
        lines.append(f"- {reason}")
    lines.append("")


# ---------------------------------------------------------------------------
# Formatting -- fixed width, no locale, no wall clock
# ---------------------------------------------------------------------------


def _fmt(value: Optional[float], decimals: int = 2) -> str:
    """A number at fixed precision, or `NOT_COLLECTED` when there is none.

    `-0.00` is normalised to `0.00`: the minus sign on a rounded-away negative
    is noise, and it changes between platforms, which would break the
    byte-identical regeneration SPEC M8 asks for.
    """
    if value is None:
        return NOT_COLLECTED
    text = f"{float(value):.{decimals}f}"
    if float(text) == 0.0:
        text = f"{0.0:.{decimals}f}"
    return text


def _fmt_interval(pair: Optional[Sequence[float]]) -> str:
    """`[low, high]` as `low to high`, or `NOT_COLLECTED` when there is none.

    An absent interval is an absence -- a real panel too thin to bootstrap, or
    a SimResult predating the purchase-event counts -- and must never render
    as a number.
    """
    if not pair:
        return NOT_COLLECTED
    return f"{_fmt(pair[0])} to {_fmt(pair[1])}"


def _fmt_int(value: Optional[int]) -> str:
    return NOT_COLLECTED if value is None else str(int(value))


def _fmt_flag(value: Optional[bool]) -> str:
    if value is None:
        return NOT_COLLECTED
    return "yes" if value else "no"


def _id_list(ids: Sequence[str]) -> str:
    return ", ".join(f"`{value}`" for value in ids) if ids else "none"


__all__ = [
    "HEADLINE_SCHEMA",
    "NOT_COLLECTED",
    "PROMPT_PATH",
    "SOURCE_LLM",
    "SOURCE_TEMPLATE",
    "SOURCE_TEMPLATE_UNGROUNDED",
    "allowed_number_strings",
    "headline",
    "is_grounded",
    "numbers_in",
    "render",
    "render_prompt",
    "request_headline",
    "template_headline",
]
