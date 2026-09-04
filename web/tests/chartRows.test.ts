import { describe, expect, it } from "vitest";
import { toChartRows } from "@/dashboard/chartRows";

describe("toChartRows", () => {
  it("returns rows in slotIds order with both series populated", () => {
    const real = { A: 0.5, B: 0.2, C: 0.3 };
    const synth = { A: 0.4, B: 0.1, C: 0.5 };

    const rows = toChartRows(real, synth, ["C", "A", "B"]);

    expect(rows).toEqual([
      { slot_id: "C", real: 0.3, synth: 0.5 },
      { slot_id: "A", real: 0.5, synth: 0.4 },
      { slot_id: "B", real: 0.2, synth: 0.1 },
    ]);
  });

  it("turns a slot missing from either mapping into 0, not undefined or NaN", () => {
    const real = { A: 0.5 };
    const synth = { B: 0.7 };

    const rows = toChartRows(real, synth, ["A", "B", "C"]);

    expect(rows).toEqual([
      { slot_id: "A", real: 0.5, synth: 0 },
      { slot_id: "B", real: 0, synth: 0.7 },
      { slot_id: "C", real: 0, synth: 0 },
    ]);
    for (const row of rows) {
      expect(Number.isNaN(row.real)).toBe(false);
      expect(Number.isNaN(row.synth)).toBe(false);
      expect(row.real).not.toBeUndefined();
      expect(row.synth).not.toBeUndefined();
    }
  });

  it("excludes a key present in the mappings but absent from slotIds", () => {
    const real = { A: 1, B: 2, EXTRA: 99 };
    const synth = { A: 3, B: 4, EXTRA_SYNTH: 55 };

    const rows = toChartRows(real, synth, ["A", "B"]);

    expect(rows).toEqual([
      { slot_id: "A", real: 1, synth: 3 },
      { slot_id: "B", real: 2, synth: 4 },
    ]);
    expect(rows.some((row) => row.slot_id === "EXTRA")).toBe(false);
    expect(rows.some((row) => row.slot_id === "EXTRA_SYNTH")).toBe(false);
  });

  it("returns an empty array for an empty slotIds list", () => {
    expect(toChartRows({ A: 1 }, { A: 2 }, [])).toEqual([]);
  });
});
