import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { Planogram } from "@/contracts/planogram.schema";
import { buildAisle } from "@/panel/aisleMap";
import { toChartRows } from "@/dashboard/chartRows";

describe("toChartRows", () => {
  it("returns rows in slotIds order with both series populated", () => {
    const real = { A: 0.5, B: 0.2, C: 0.3 };
    const synth = { A: 0.4, B: 0.1, C: 0.5 };

    const rows = toChartRows(real, synth, ["C", "A", "B"]);

    expect(rows).toEqual([
      { slot_id: "C", label: "C", position: "", real: 0.3, synth: 0.5 },
      { slot_id: "A", label: "A", position: "", real: 0.5, synth: 0.4 },
      { slot_id: "B", label: "B", position: "", real: 0.2, synth: 0.1 },
    ]);
  });

  it("turns a slot missing from either mapping into 0, not undefined or NaN", () => {
    const real = { A: 0.5 };
    const synth = { B: 0.7 };

    const rows = toChartRows(real, synth, ["A", "B", "C"]);

    expect(rows).toEqual([
      { slot_id: "A", label: "A", position: "", real: 0.5, synth: 0 },
      { slot_id: "B", label: "B", position: "", real: 0, synth: 0.7 },
      { slot_id: "C", label: "C", position: "", real: 0, synth: 0 },
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
      { slot_id: "A", label: "A", position: "", real: 1, synth: 3 },
      { slot_id: "B", label: "B", position: "", real: 2, synth: 4 },
    ]);
    expect(rows.some((row) => row.slot_id === "EXTRA")).toBe(false);
    expect(rows.some((row) => row.slot_id === "EXTRA_SYNTH")).toBe(false);
  });

  it("returns an empty array for an empty slotIds list", () => {
    expect(toChartRows({ A: 1 }, { A: 2 }, [])).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// Naming the bars (S28)
// ---------------------------------------------------------------------------

describe("a chart row is labelled with the product, not the shelf position", () => {
  const AISLE = buildAisle(
    JSON.parse(
      readFileSync(resolve(__dirname, "../../data/planograms/demo_aisle.json"), "utf-8"),
    ) as Planogram,
  );

  it("labels a slot with the product that is in it", () => {
    const [row] = toChartRows({ B1S1P1: 0.1 }, { B1S1P1: 0.2 }, ["B1S1P1"], AISLE);

    expect(row.label).toBe("Crunch Chips 100g");
    expect(row.position).toContain("bay 1");
    expect(row.position).toContain("top shelf");
    // The id stays on the row: it is the key everything upstream is built over,
    // and the export and the tooltip both still need it.
    expect(row.slot_id).toBe("B1S1P1");
  });

  it("falls back to the slot id rather than showing a blank bar", () => {
    // An id the planogram does not have. Dropping the row would silently
    // shorten the chart; a blank label would leave an unattributable bar.
    const [row] = toChartRows({ ZZZ: 0.1 }, {}, ["ZZZ"], AISLE);

    expect(row.label).toBe("ZZZ");
    expect(row.position).toBe("");
  });

  it("still works with no planogram at all", () => {
    // `Experiment` renders as soon as the experiment resolves; the planogram is
    // a second request that may not have landed yet, and the chart must draw
    // rather than wait.
    const [row] = toChartRows({ B1S1P1: 0.1 }, {}, ["B1S1P1"]);

    expect(row.label).toBe("B1S1P1");
  });

  it("disambiguates two positions holding the same product", () => {
    // Two facings of one SKU in different bays would otherwise put two bars
    // under one axis label, and a reader could not tell which shelf was which.
    const duplicated = [
      ...AISLE,
      {
        ...AISLE[0],
        bay_id: "B9",
        shelves: [
          {
            ...AISLE[0].shelves[0],
            shelf_id: "B9S1",
            slots: [{ ...AISLE[0].shelves[0].slots[0], slot_id: "B9S1P1", bay_id: "B9" }],
          },
        ],
      },
    ];

    const rows = toChartRows({}, {}, ["B1S1P1", "B9S1P1"], duplicated);

    expect(rows[0].label).not.toBe(rows[1].label);
    expect(rows[0].label).toContain("Crunch Chips 100g");
    expect(rows[1].label).toContain("Crunch Chips 100g");
  });
});
