import { describeSlot, type MappedBay } from "@/panel/aisleMap";

/**
 * Pure data shaping for the Experiment dashboard chart. No React, no fetch --
 * this is a plain function so it can be unit tested without rendering
 * anything (web/tests/chartRows.test.ts).
 */
export interface ChartRow {
  slot_id: string;
  /**
   * What goes on the axis: the product in this slot, or the slot id when there
   * is no product to name it with.
   *
   * The chart used to plot `slot_id` directly, so the headline comparison of
   * this entire project - real attention against synthetic, bar by bar - was
   * labelled `B1S3P2`, `B2S1P1`, `B3S4P2`. Nobody outside the repository can
   * read that, and after a week nobody inside it can either. The names were one
   * join away the whole time, in `planogram.skus`.
   */
  label: string;
  /** "bay 1, eye level", or "" when the slot could not be located. */
  position: string;
  real: number;
  synth: number;
}

/**
 * One row per id in `slotIds`, in that order, pairing each slot's real and
 * synthetic attention. A slot missing from `real` or `synth` becomes 0 for
 * that series (never `undefined` or `NaN`). A key present in `real` or
 * `synth` but absent from `slotIds` is dropped -- `slotIds` is the shared
 * vocabulary the two attention vectors were built over.
 *
 * `aisle` names the bars. It is optional and defaults to empty because the
 * dashboard renders as soon as the experiment resolves, and the planogram it
 * needs for names is a second request that may not have landed yet: a chart
 * labelled with slot ids is worse than one labelled with products, and both are
 * better than a page that waits.
 */
export function toChartRows(
  real: Record<string, number>,
  synth: Record<string, number>,
  slotIds: string[],
  aisle: MappedBay[] = [],
): ChartRow[] {
  const described = slotIds.map((slot_id) => ({
    slot_id,
    described: describeSlot(aisle, slot_id),
  }));

  // Two shelf positions can hold the same product. Left alone that puts two
  // bars under one axis label and a reader cannot tell which shelf is which,
  // so a repeated name earns its position back.
  const nameCounts = new Map<string, number>();
  for (const { described: entry } of described) {
    if (entry === null) continue;
    nameCounts.set(entry.product, (nameCounts.get(entry.product) ?? 0) + 1);
  }

  return described.map(({ slot_id, described: entry }) => ({
    slot_id,
    label:
      entry === null
        ? slot_id
        : (nameCounts.get(entry.product) ?? 0) > 1
          ? `${entry.product} (${entry.position})`
          : entry.product,
    position: entry?.position ?? "",
    real: real[slot_id] ?? 0,
    synth: synth[slot_id] ?? 0,
  }));
}
