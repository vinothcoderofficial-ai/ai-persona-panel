"""Tests for scripts/optimize.py, the optimizer's recording entry point.

The CLI owns no maths -- optimizer.py and slot_value.py do. What it owns, and
what these tests pin, is the one property that could quietly turn an honest
module into a dishonest demo: **the commercial assumptions are all-or-nothing.**
`slot_value.Assumptions` has no defaults precisely because margin and store
traffic exist nowhere in this repository. A CLI that let a caller pass four of
the six flags and filled the rest in would put that back.
"""
import subprocess
import sys
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts import optimize  # noqa: E402


PRICING_ARGS = [
    "--baseline-units", "120", "--margin-per-unit", "7.5",
    "--stores", "4", "--weeks", "13",
    "--currency", "INR", "--basis", "test basis",
]


def parse(argv):
    return optimize.build_parser().parse_args(argv)


def test_no_commercial_flags_means_no_pricing():
    """The default path prices nothing, so it cannot assume anything."""
    assert optimize.assumptions_from(parse(["--creative", "AD_1"])) is None


def test_every_commercial_flag_present_builds_assumptions():
    got = optimize.assumptions_from(parse(["--creative", "AD_1", *PRICING_ARGS]))
    assert got is not None
    assert got.baseline_brand_units_per_store_week == 120.0
    assert got.margin_per_unit == 7.5
    assert got.n_stores == 4
    assert got.n_weeks == 13
    assert got.currency == "INR"
    assert got.basis == "test basis"


@pytest.mark.parametrize("dropped", range(0, len(PRICING_ARGS), 2))
def test_any_missing_commercial_flag_is_refused(dropped):
    """Drop each flag in turn; every one of the six must be fatal.

    A partial price is worse than no price: it looks like a result.
    """
    partial = [a for i, a in enumerate(PRICING_ARGS) if i not in (dropped, dropped + 1)]
    with pytest.raises(SystemExit) as excinfo:
        optimize.assumptions_from(parse(["--creative", "AD_1", *partial]))
    message = str(excinfo.value)
    assert "every commercial input or none" in message
    assert PRICING_ARGS[dropped].lstrip("-") in message.replace("--", "")


def test_missing_flags_exit_non_zero_end_to_end():
    """A partial set must fail the process, not just a function."""
    done = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "optimize.py"),
         "--creative", "AD_1", "--margin-per-unit", "7.5"],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert done.returncode != 0
    assert "every commercial input or none" in (done.stderr + done.stdout)


def test_commercial_flags_list_matches_the_assumptions_dataclass():
    """If Assumptions grows a field, this CLI must grow a flag.

    Without this, a new required assumption would reach the printed table with
    no way to set it -- or worse, with a default invented here.
    """
    import dataclasses
    from analytics.slot_value import Assumptions

    fields = {f.name for f in dataclasses.fields(Assumptions)}
    assert len(optimize.COMMERCIAL_FLAGS) == len(fields), (
        f"Assumptions has {len(fields)} fields but the CLI exposes "
        f"{len(optimize.COMMERCIAL_FLAGS)} commercial flags"
    )


# --- the skip line ----------------------------------------------------------
#
# `Skipped` carries candidate_id / kind / reason / detail and has no `label`.
# main() printed `skipped.label`, so the CLI raised AttributeError the moment a
# candidate was skipped. The default space on the committed aisle skips nothing,
# so the loop body never ran in any earlier test and the bug shipped.


def test_format_skipped_uses_fields_that_exist_on_skipped():
    from analytics.optimizer import Skipped

    skipped = Skipped(candidate_id="sku:SKU_008@above_eye", kind="sku",
                      reason="bay B1 has no shelf at level above_eye", detail={})
    line = optimize.format_skipped(skipped)

    assert "sku:SKU_008@above_eye" in line
    assert "no shelf at level above_eye" in line


def test_format_skipped_names_every_public_field_it_can():
    """A skip line that omits the reason is useless; that is the whole point of
    reporting skips rather than dropping them silently."""
    from analytics.optimizer import Skipped

    skipped = Skipped(candidate_id="c1", kind="sku", reason="nowhere to send it",
                      detail={"level": "top"})
    line = optimize.format_skipped(skipped)
    assert "c1" in line and "nowhere to send it" in line


def test_format_skipped_survives_every_field_of_a_real_skipped_object():
    """Guards the actual failure mode: touching an attribute that is not there."""
    import dataclasses
    from analytics.optimizer import Skipped

    names = {f.name for f in dataclasses.fields(Skipped)}
    assert "label" not in names, "if Skipped gains a label, revisit format_skipped"
    skipped = Skipped(candidate_id="c", kind="k", reason="r", detail={})
    assert isinstance(optimize.format_skipped(skipped), str)
