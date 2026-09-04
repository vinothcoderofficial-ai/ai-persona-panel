"""The known effect: move the focal SKU to eye level and see whether both
panels move the same way (SPEC M5, PLAN S19).

    uplift = (att_focal_B - att_focal_A) / att_focal_A

for the real panel and for the synthetic one, plus a `same_direction` flag.
This is the experiment's positive control. Spearman and KL say the personas
rank a shelf plausibly; this says they respond to an intervention whose sign
is known in advance from the retail literature, which is a much harder thing
to get right by accident.

The focal slot is looked up PER VARIANT
---------------------------------------
data/variants/B.json is a single `move_sku` patch: SKU_008 leaves B1S5P1 (the
bottom shelf) for B1S3P2 (eye level). B1S3P2 is an *empty shelf position*
under variant A -- `sku_id: null`, `facings: 0` -- and B1S5P1 becomes one
under variant B. So a fixed slot id does not merely measure the wrong slot; it
measures a slot that holds no product on one side of the subtraction, and the
resulting number is a confident answer to a question nobody asked:

    per-variant lookup   (0.08 - 0.02) / 0.02 = +3.0   <- the known effect
    fixed at B1S5P1      (0.00 - 0.02) / 0.02 = -1.0   <- "the effect reversed"
    fixed at B1S3P2      (0.08 - 0.00) / 0.00 = undefined

`focal_slot()` therefore takes a RESOLVED planogram and asks it where the SKU
actually is, and `known_effect()` takes one slot id per variant rather than
one for both. It is the same rule `analytics/fusion.py:purchase_slot_matrix`
follows for credit assignment, for the same reason.

Guards
------
Every division is guarded and nothing here can emit `inf`, `nan`, or a
fabricated `0.0`:

  * a zero baseline (`att_focal_A == 0`) makes the ratio undefined -> None;
  * a focal slot of None (the SKU is on no shelf in that variant) -> None;
  * a panel absent from the input entirely -- which is exactly the state of
    the real panel before any sessions are collected -- -> None;
  * `same_direction` is None whenever either uplift is None, because "we do
    not know" and "they disagreed" are different findings and False would
    read as the second.

A slot missing from an attention vector counts as 0.0, the same convention
`analytics/metrics.py` and `analytics/fusion.py` use: the vectors are built
over a variant's full slot vocabulary, so an absent key means the slot drew
nothing, which is a measurement.

Pure: stdlib only, no I/O, no globals, no RNG.
"""

from typing import Any, Mapping, Optional

# The two panels, keyed the way schemas/metrics.schema.json names them in
# `known_effect`: `real_uplift` and `synth_uplift`.
REAL = "real"
SYNTH = "synth"

_PANEL_FIELD = {REAL: "real_uplift", SYNTH: "synth_uplift"}


def focal_slot(planogram: Mapping[str, Any], sku_id: str) -> Optional[str]:
    """The slot `sku_id` occupies in this RESOLVED planogram, or None.

    Pass the planogram for the variant whose attention vector you are about
    to index -- that is the whole point of this function. Returns None when
    the SKU is in no slot (a variant may leave it unplaced, and it is also
    what an unknown sku id gives); callers turn that into an undefined uplift
    rather than into an attention of 0.0.

    Slots are scanned in planogram order and the first holder wins. The
    planogram model gives a SKU at most one slot -- `api/app/resolve.py`'s
    `move_sku` relies on it -- so there is never a second one to choose
    between.
    """
    for bay in planogram["bays"]:
        for shelf in bay["shelves"]:
            for slot in shelf["slots"]:
                if slot.get("sku_id") == sku_id:
                    return slot["slot_id"]
    return None


def panel_uplift(
    attention_a: Optional[Mapping[str, float]],
    attention_b: Optional[Mapping[str, float]],
    focal_slot_a: Optional[str],
    focal_slot_b: Optional[str],
) -> Optional[float]:
    """`(att_focal_B - att_focal_A) / att_focal_A` for ONE panel.

    `attention_a` / `attention_b` are that panel's per-slot attention under
    variants A and B -- `fusion.trimmed_mean` output for the real panel,
    `fusion.fuse_synthetic` output for the synthetic one. `focal_slot_a` and
    `focal_slot_b` come from `focal_slot()` called on each variant's own
    resolved planogram.

    Returns None -- never `inf`, never `nan`, never 0.0 -- when the panel is
    missing, when either focal slot is unknown, or when the baseline
    attention under A is zero. A zero baseline is not an infinite uplift; it
    is a ratio with nothing to be a ratio of.
    """
    if attention_a is None or attention_b is None:
        return None
    if focal_slot_a is None or focal_slot_b is None:
        return None

    baseline = float(attention_a.get(focal_slot_a, 0.0))
    if baseline == 0.0:
        return None

    treated = float(attention_b.get(focal_slot_b, 0.0))
    return (treated - baseline) / baseline


def same_direction(
    real_uplift: Optional[float], synth_uplift: Optional[float]
) -> Optional[bool]:
    """Did both panels move the same way? None when either side is undefined.

    Compares signs, so two uplifts of very different size still agree as long
    as they point the same way -- the flag is about direction, and magnitude
    is already reported separately. Two zero uplifts agree (both "no
    change"); a zero against a non-zero does not.
    """
    if real_uplift is None or synth_uplift is None:
        return None
    return _sign(real_uplift) == _sign(synth_uplift)


def known_effect(
    att_a: Mapping[str, Mapping[str, float]],
    att_b: Mapping[str, Mapping[str, float]],
    focal_slot_a: Optional[str],
    focal_slot_b: Optional[str],
) -> dict:
    """The focal-SKU uplift for both panels, and whether they agree in sign.

    `att_a` and `att_b` are the two variants' attention, keyed by panel:
    `{"real": {slot_id: att, ...}, "synth": {...}}`. A panel key that is
    absent (the real panel before any sessions exist) yields None for its
    uplift rather than a zero that would read as a measured no-effect.

    `focal_slot_a` and `focal_slot_b` are that variant's slot for the focal
    SKU -- `focal_slot(resolved_a, sku)` and `focal_slot(resolved_b, sku)`.
    Two arguments, not one, because variant B moves the SKU.

    Returns `{"real_uplift": float|None, "synth_uplift": float|None,
    "same_direction": bool|None}` -- always all three keys, so a caller
    cannot mistake "undefined" for "not computed". `to_metrics_block()`
    converts that into the schema's shape, where undefined means absent.
    """
    uplifts = {
        panel: panel_uplift(att_a.get(panel), att_b.get(panel), focal_slot_a, focal_slot_b)
        for panel in (REAL, SYNTH)
    }
    return {
        "real_uplift": uplifts[REAL],
        "synth_uplift": uplifts[SYNTH],
        "same_direction": same_direction(uplifts[REAL], uplifts[SYNTH]),
    }


def to_metrics_block(result: Mapping[str, Any]) -> dict:
    """`known_effect()`'s output as the `known_effect` block of
    schemas/metrics.schema.json.

    That block types `real_uplift` and `synth_uplift` as plain numbers and
    `same_direction` as a plain boolean, and requires none of them. So an
    undefined value is an ABSENT key -- it cannot be null (the schema would
    reject it) and it must not be 0.0 or False (both are legitimate measured
    values and would be read as results). Dropping the key is the only
    honest encoding the contract allows, and it is the same rule
    `analytics/lift.py` applies to an undefined `synth`.
    """
    return {key: value for key, value in result.items() if value is not None}


def _sign(value: float) -> int:
    if value > 0.0:
        return 1
    if value < 0.0:
        return -1
    return 0
