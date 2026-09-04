/**
 * Pure data shaping for the Experiment dashboard chart. No React, no fetch --
 * this is a plain function so it can be unit tested without rendering
 * anything (web/tests/chartRows.test.ts).
 */
export interface ChartRow {
  slot_id: string;
  real: number;
  synth: number;
}

/**
 * One row per id in `slotIds`, in that order, pairing each slot's real and
 * synthetic attention. A slot missing from `real` or `synth` becomes 0 for
 * that series (never `undefined` or `NaN`). A key present in `real` or
 * `synth` but absent from `slotIds` is dropped -- `slotIds` is the shared
 * vocabulary the two attention vectors were built over.
 */
export function toChartRows(
  real: Record<string, number>,
  synth: Record<string, number>,
  slotIds: string[],
): ChartRow[] {
  return slotIds.map((slot_id) => ({
    slot_id,
    real: real[slot_id] ?? 0,
    synth: synth[slot_id] ?? 0,
  }));
}
