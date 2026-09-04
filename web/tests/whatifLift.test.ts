import { describe, expect, it } from "vitest";
import {
  NOT_APPLICABLE,
  formatLift,
  personaLiftRows,
  relativeChange,
  type FixationProbSource,
} from "@/whatif/lift";

/**
 * `lift_vs_baseline` is deliberately allowed to say nothing: `{}` when there is
 * no focal SKU, and a `null` value when the baseline was exactly 0 and the
 * ratio is undefined. Both must read as "not applicable" and never as 0% - a
 * fabricated number on a recorded demo is the failure mode this whole module is
 * shaped around.
 */

function persona(fixation_prob: Record<string, number>): FixationProbSource {
  return { fixation_prob };
}

const BASELINE = {
  browser: persona({ B1S5P1: 0.02, B1S3P2: 0 }),
  loyalist: persona({ B1S5P1: 0.04 }),
  mission: persona({ B1S5P1: 0 }),
  switcher: persona({ B1S5P1: 0.05 }),
};

const PATCHED = {
  browser: persona({ B1S3P2: 0.03 }),
  loyalist: persona({ B1S3P2: 0.06 }),
  mission: persona({ B1S3P2: 0.01 }),
  switcher: persona({ B1S3P2: 0.05 }),
};

const MOVED = { baseline: "B1S5P1", patched: "B1S3P2" };

describe("relativeChange", () => {
  it("is (after - before) / before, exactly as the endpoint computes it", () => {
    expect(relativeChange(0.2, 0.3)).toBeCloseTo(0.5, 12);
    expect(relativeChange(0.4, 0.2)).toBeCloseTo(-0.5, 12);
  });

  it("is null - not 0 - when the baseline was exactly 0", () => {
    expect(relativeChange(0, 0.3)).toBeNull();
    expect(relativeChange(0, 0)).toBeNull();
  });

  it("reports a real 0 change as 0, because that one is measured", () => {
    expect(relativeChange(0.3, 0.3)).toBe(0);
  });
});

describe("personaLiftRows", () => {
  it("compares each persona's fixation probability at the focal SKU's own slot", () => {
    const rows = personaLiftRows(BASELINE, PATCHED, MOVED);
    expect(rows.map((row) => row.personaId)).toEqual([
      "browser",
      "loyalist",
      "mission",
      "switcher",
    ]);
    expect(rows[0].personaId).toBe("browser");
    expect(rows[0].baseline).toBe(0.02);
    expect(rows[0].patched).toBe(0.03);
    expect(rows[0].lift).toBeCloseTo(0.5, 12);
  });

  it("says null for a persona whose baseline attention was exactly 0", () => {
    const rows = personaLiftRows(BASELINE, PATCHED, MOVED);
    const mission = rows.find((row) => row.personaId === "mission");
    expect(mission?.baseline).toBe(0);
    expect(mission?.lift).toBeNull();
  });

  it("is empty when there is no focal SKU to measure", () => {
    expect(personaLiftRows(BASELINE, PATCHED, { baseline: null, patched: null })).toEqual([]);
  });

  it("is empty with no baseline run to compare against", () => {
    expect(personaLiftRows({}, PATCHED, MOVED)).toEqual([]);
  });

  it("treats a slot a persona never fixated as 0 attention, not as missing", () => {
    const rows = personaLiftRows(
      { solo: persona({ B1S5P1: 0.02 }) },
      { solo: persona({}) },
      MOVED,
    );
    expect(rows).toEqual([{ personaId: "solo", baseline: 0.02, patched: 0, lift: -1 }]);
  });
});

describe("formatLift", () => {
  it("shows a computed lift as a signed percentage", () => {
    expect(formatLift(0.777)).toBe("+77.7%");
    expect(formatLift(-0.25)).toBe("-25.0%");
    expect(formatLift(0)).toBe("0.0%");
  });

  it("shows an uncomputed lift as 'not applicable', with no percentage at all", () => {
    for (const uncomputed of [null, undefined]) {
      expect(formatLift(uncomputed)).toBe(NOT_APPLICABLE);
      expect(formatLift(uncomputed)).not.toContain("%");
      expect(formatLift(uncomputed)).not.toContain("0");
    }
  });

  it("refuses to print a non-finite number as a figure", () => {
    expect(formatLift(Number.NaN)).toBe(NOT_APPLICABLE);
    expect(formatLift(Number.POSITIVE_INFINITY)).toBe(NOT_APPLICABLE);
  });
});
