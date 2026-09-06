import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { Planogram } from "@/contracts/planogram.schema";
import { buildAisle, describeSlot, labelForSlot, levelLabel } from "@/panel/aisleMap";

/**
 * Joining the planogram's slot grid to its `skus` list — the fix for the single
 * most common complaint about every screen in this project: they said
 * `B1S3P2`, and the planogram has held `"Orchid Nuts 120g"` all along.
 *
 * Read against the real seed document rather than a fixture. A hand-written
 * planogram here would let this module keep agreeing with a shape the app no
 * longer serves, and the slot ids asserted below are the ones the demo script,
 * the variant patches and `RESULTS.md` all name.
 *
 * The rule the whole module turns on: **a label is never invented.** An unknown
 * slot id returns null, and an empty slot says it is empty rather than
 * borrowing the name of whatever used to be there.
 */

const PLANOGRAM: Planogram = JSON.parse(
  readFileSync(resolve(__dirname, "../../data/planograms/demo_aisle.json"), "utf-8"),
) as Planogram;

describe("buildAisle keeps the planogram's own structure", () => {
  it("carries every bay in planogram order", () => {
    const aisle = buildAisle(PLANOGRAM);

    expect(aisle.map((bay) => bay.bay_id)).toEqual(["B1", "B2", "B3"]);
  });

  it("carries every shelf with the level the planogram gives it", () => {
    const [bay] = buildAisle(PLANOGRAM);

    expect(bay.shelves.map((shelf) => shelf.level)).toEqual([
      "top",
      "above_eye",
      "eye",
      "below_eye",
      "bottom",
    ]);
  });

  it("carries every slot, including the empty ones", () => {
    const aisle = buildAisle(PLANOGRAM);
    const slotIds = aisle.flatMap((bay) =>
      bay.shelves.flatMap((shelf) => shelf.slots.map((slot) => slot.slot_id)),
    );

    // Empty positions are real slot objects with `sku_id: null` — that is what
    // makes "move a SKU to eye level" expressible at all (CLAUDE.md), so a map
    // that dropped them would quietly delete the thing variant B moves into.
    expect(slotIds).toContain("B1S3P2");
    expect(slotIds).toHaveLength(30);
  });

  it("joins each occupied slot to its product", () => {
    const aisle = buildAisle(PLANOGRAM);
    const slot = aisle[0].shelves[0].slots[0];

    expect(slot.slot_id).toBe("B1S1P1");
    expect(slot.sku_id).toBe("SKU_001");
    expect(slot.name).toBe("Crunch Chips 100g");
    expect(slot.brand).toBe("Crunch");
    expect(slot.price).toBe(25);
    expect(slot.texture_url).toBe("/textures/sku_001.png");
  });

  it("leaves an empty slot empty rather than inventing a product for it", () => {
    const aisle = buildAisle(PLANOGRAM);
    const empty = aisle
      .flatMap((bay) => bay.shelves.flatMap((shelf) => shelf.slots))
      .find((slot) => slot.slot_id === "B1S3P2");

    expect(empty).toBeDefined();
    expect(empty?.sku_id).toBeNull();
    expect(empty?.name).toBeNull();
    expect(empty?.facings).toBe(0);
  });

  it("carries the ad slots and resolves their creative", () => {
    const aisle = buildAisle(PLANOGRAM);
    const endcap = aisle
      .flatMap((bay) => bay.adSlots)
      .find((ad) => ad.ad_slot_id === "B3_ENDCAP");

    expect(endcap?.creative_id).toBe("AD_1");
    expect(endcap?.brand).toBe("Crunch");
    expect(endcap?.texture_url).toBe("/textures/ad_1.png");
  });

  it("reports an ad slot with no creative as carrying none", () => {
    const aisle = buildAisle(PLANOGRAM);
    const talker = aisle
      .flatMap((bay) => bay.adSlots)
      .find((ad) => ad.ad_slot_id === "B1_TALKER");

    expect(talker?.creative_id).toBeNull();
    expect(talker?.brand).toBeNull();
  });
});

describe("labelForSlot is how a slot id becomes something a person can read", () => {
  it("names the product, the bay and the shelf level", () => {
    const aisle = buildAisle(PLANOGRAM);

    const label = labelForSlot(aisle, "B1S5P1");

    expect(label).toContain("Orchid Nuts 120g");
    expect(label).toContain("bay 1");
    expect(label).toContain("bottom");
  });

  it("says a slot is empty rather than leaving a blank where a name goes", () => {
    const aisle = buildAisle(PLANOGRAM);

    expect(labelForSlot(aisle, "B1S3P2")?.toLowerCase()).toContain("empty");
  });

  it("returns null for a slot the planogram does not have", () => {
    // Never a fabricated label. A confident caption on the wrong slot is worse
    // than no caption, because it is the one an operator stops checking.
    expect(labelForSlot(buildAisle(PLANOGRAM), "B9S9P9")).toBeNull();
  });

  it("labels an ad slot by its creative and its position", () => {
    const label = labelForSlot(buildAisle(PLANOGRAM), "B3_ENDCAP");

    expect(label).toContain("AD_1");
    expect(label).toContain("bay 3");
  });
});

describe("describeSlot separates the product from where it sits", () => {
  it("gives the two halves apart, so a caller can lay them out", () => {
    const described = describeSlot(buildAisle(PLANOGRAM), "B1S5P1");

    expect(described?.product).toBe("Orchid Nuts 120g");
    expect(described?.position).toContain("bay 1");
    expect(described?.position).toContain("bottom");
  });

  it("is null for an unknown slot, like labelForSlot", () => {
    expect(describeSlot(buildAisle(PLANOGRAM), "nope")).toBeNull();
  });
});

describe("levelLabel", () => {
  it("turns the schema's enum into English", () => {
    expect(levelLabel("above_eye")).toBe("above eye level");
    expect(levelLabel("eye")).toBe("eye level");
    expect(levelLabel("bottom")).toBe("bottom shelf");
  });
});
