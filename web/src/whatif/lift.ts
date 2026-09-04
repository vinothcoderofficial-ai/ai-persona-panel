import type { FocalSlots } from "@/whatif/patches";

/**
 * Lift, and the one rule that matters about it: **a lift that could not be
 * computed is never drawn as 0%.**
 *
 * `POST /whatif` is deliberate about this. `lift_vs_baseline` is `{}` when no
 * SKU was focal and no patch named one - an ad-only change, say - and a key is
 * `null` when the baseline value was exactly 0 and the ratio `(after - before)
 * / before` is undefined. Both mean "there is no number here". Rendering either
 * as 0% would put a fabricated figure on screen in a recorded demo, which is
 * the one failure this module exists to prevent.
 */

/** The words shown wherever a lift could not be computed. */
export const NOT_APPLICABLE = "not applicable";

/**
 * `(after - before) / before`, or null when `before` is 0.
 *
 * The same rule as `api/app/routers/whatif.py:_relative_change`, deliberately:
 * the population figures on this page come from the server and the per-persona
 * bars are worked out in the browser, and the two must not disagree about what
 * an undefined ratio looks like.
 */
export function relativeChange(before: number, after: number): number | null {
  if (before === 0) return null;
  return (after - before) / before;
}

/** The one field of a `SimResult` the per-persona bars read. */
export interface FixationProbSource {
  fixation_prob: Record<string, number>;
}

export interface PersonaLiftRow {
  personaId: string;
  /** That persona's probability of fixating the focal SKU in the baseline run. */
  baseline: number;
  /** The same probability in the patched run. */
  patched: number;
  /** The relative change between the two, or null when the baseline was 0. */
  lift: number | null;
}

function attentionAt(source: FixationProbSource | undefined, slotId: string | null): number {
  if (source === undefined || slotId === null) return 0;
  return source.fixation_prob[slotId] ?? 0;
}

/**
 * One row per persona: **the relative change in that persona's probability of
 * fixating the focal SKU**, patched run against the baseline run.
 *
 * That is the quantity, in full: `fixation_prob` at the slot the focal SKU
 * occupies, looked up separately in each run because a `move_sku` patch is
 * precisely the case where the two slots differ - comparing one fixed slot id
 * would measure the old shelf position against whoever now stands in it.
 *
 * `per_persona` in a what-if response is only ever one run, so the baseline
 * side comes from the page's own opening request (`patches: []`, the endpoint's
 * exactly-neutral baseline). With no baseline run, or no focal SKU, there is
 * nothing to compare and the list is empty rather than full of zeros.
 */
export function personaLiftRows(
  baseline: Record<string, FixationProbSource>,
  patched: Record<string, FixationProbSource>,
  slots: FocalSlots,
): PersonaLiftRow[] {
  if (slots.baseline === null && slots.patched === null) return [];

  const personaIds = [...new Set(Object.keys(baseline))]
    .filter((personaId) => personaId in patched)
    .sort();

  return personaIds.map((personaId) => {
    const before = attentionAt(baseline[personaId], slots.baseline);
    const after = attentionAt(patched[personaId], slots.patched);
    return { personaId, baseline: before, patched: after, lift: relativeChange(before, after) };
  });
}

/**
 * A lift as a signed percentage, or the words when there is no number.
 *
 * `formatLift(0)` is "0.0%" and that is correct: a measured change of nothing
 * is a result. It is only an *uncomputed* lift - null, undefined, or a
 * non-finite number - that must never be printed as a figure.
 */
export function formatLift(lift: number | null | undefined): string {
  if (lift === null || lift === undefined || !Number.isFinite(lift)) return NOT_APPLICABLE;
  const percent = lift * 100;
  return `${percent > 0 ? "+" : ""}${percent.toFixed(1)}%`;
}

/** Why a lift is missing, in the words an operator needs to see. */
export function liftExplanation(lift: Record<string, number | null>, key: string): string | null {
  if (!(key in lift)) {
    return "No focal SKU was named or inferable from the patches, so the endpoint reported no lift for it.";
  }
  if (lift[key] === null) {
    return "The baseline value for this figure was exactly 0, so the relative change is undefined.";
  }
  return null;
}
