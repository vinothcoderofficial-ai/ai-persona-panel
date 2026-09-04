import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import type { Planogram } from "@/contracts/planogram.schema";
import { destinationSlotId } from "@/whatif/patches";
import { demoAisle } from "./whatifFixture";

/**
 * The what-if tests assert real slot ids - "SKU_008 to eye level is B1S3P2" -
 * against `whatifFixture.ts`. This is what makes those assertions statements
 * about the aisle the demo actually runs on, rather than about a planogram the
 * tests invented for themselves.
 *
 * Read off disk rather than imported, the way `spectatorIsolation.test.ts`
 * reads source files: nothing outside `web/` belongs in this app's module
 * graph. `data/planograms/demo_aisle.json` is committed, and `make seed`
 * regenerates it - if a regeneration ever moves a SKU or fills an empty slot,
 * this fails here instead of silently changing what a dropdown does.
 */

const HERE = dirname(fileURLToPath(import.meta.url));
const SEED = resolve(HERE, "..", "..", "data", "planograms", "demo_aisle.json");

/** Everything under `src/whatif/` reads. Facings and geometry are the server's. */
function shape(planogram: Planogram) {
  return {
    planogram_id: planogram.planogram_id,
    bays: planogram.bays.map((bay) => ({
      bay_id: bay.bay_id,
      type: bay.type,
      shelves: bay.shelves.map((shelf) => ({
        shelf_id: shelf.shelf_id,
        level: shelf.level,
        slots: shelf.slots.map((slot) => [slot.slot_id, slot.sku_id]),
      })),
      ad_slots: bay.ad_slots.map((ad) => [ad.ad_slot_id, ad.type, ad.creative_id]),
    })),
    skus: planogram.skus.map((sku) => [sku.sku_id, sku.brand, sku.price, sku.promo]),
    creatives: planogram.creatives.map((creative) => [creative.creative_id, creative.brand]),
  };
}

function seed(): Planogram {
  return JSON.parse(readFileSync(SEED, "utf8")) as Planogram;
}

describe("the what-if fixture is the seed aisle", () => {
  it("agrees with data/planograms/demo_aisle.json on everything the controls read", () => {
    expect(shape(demoAisle())).toEqual(shape(seed()));
  });

  it("still has a free slot at eye level in every bay, which is what makes the move a move", () => {
    // CLAUDE.md: empty positions are real slot objects with sku_id: null, and
    // this is what they are for. Without one, "move to eye level" degrades from
    // a move into a swap - resolve() handles both, but they are not the same
    // intervention, and variant B (the known-effect variant) is the move.
    const planogram = seed();
    for (const bay of planogram.bays) {
      const eye = bay.shelves.find((shelf) => shelf.level === "eye");
      expect(eye?.slots.some((slot) => slot.sku_id === null), `${bay.bay_id} eye shelf`).toBe(
        true,
      );
    }
  });

  it("sends SKU_008 to B1S3P2, which is exactly variant B", () => {
    // data/variants/B.json is `move_sku SKU_008 -> B1S3P2`, the known-effect
    // variant S18 measures. The controls must reproduce it, or the what-if page
    // and the evaluation would be talking about different interventions.
    expect(destinationSlotId(seed(), "SKU_008", "eye")).toBe("B1S3P2");
  });
});
