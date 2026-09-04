import { describe, expect, it } from "vitest";
import {
  CLEAR_CREATIVE,
  DEFAULT_N_SYNTH,
  DEFAULT_SEED,
  EMPTY_SELECTION,
  destinationSlotId,
  toPatches,
  toRequestBody,
  type WhatIfSelection,
} from "@/whatif/patches";
import { demoAisle } from "./whatifFixture";

/**
 * The controls-to-patches mapping is the whole contract with `POST /whatif`.
 * The endpoint's request model is `extra="forbid"` and its patches are checked
 * against `schemas/variant.schema.json`, so an extra key is not a field that
 * gets ignored - it is a 422 on the whole call. Every assertion here is
 * therefore on the *exact* object, never on a subset of it.
 */

const PLANOGRAM = demoAisle();

function select(overrides: Partial<WhatIfSelection>): WhatIfSelection {
  return { ...EMPTY_SELECTION, ...overrides };
}

describe("focal SKU -> shelf level", () => {
  it("moves the SKU into the empty slot on that level, in its own bay", () => {
    // SKU_008 sits at B1S5P1 (bottom). Bay 1's eye shelf keeps B1S3P2 free -
    // this is variant B, the known-effect variant, reproduced from the controls.
    expect(toPatches(PLANOGRAM, select({ focalSkuId: "SKU_008", shelfLevel: "eye" }))).toEqual([
      { op: "move_sku", sku_id: "SKU_008", to_slot_id: "B1S3P2" },
    ]);
  });

  it("never leaves the SKU's own bay", () => {
    expect(toPatches(PLANOGRAM, select({ focalSkuId: "SKU_017", shelfLevel: "eye" }))).toEqual([
      { op: "move_sku", sku_id: "SKU_017", to_slot_id: "B3S3P2" },
    ]);
  });

  it("takes the first slot - a swap - when the target shelf is full", () => {
    // No shelf at `top` has a free slot, so B1S1P1's occupant and SKU_008 trade
    // places. resolve() does exactly that for an occupied destination.
    expect(toPatches(PLANOGRAM, select({ focalSkuId: "SKU_008", shelfLevel: "top" }))).toEqual([
      { op: "move_sku", sku_id: "SKU_008", to_slot_id: "B1S1P1" },
    ]);
  });

  it("emits nothing when the SKU is already on a shelf at that level", () => {
    // SKU_005 is at B1S3P1, already eye level. Moving it to B1S3P2 would change
    // its horizontal position, which is not what the control asked for.
    expect(destinationSlotId(PLANOGRAM, "SKU_005", "eye")).toBeNull();
    expect(toPatches(PLANOGRAM, select({ focalSkuId: "SKU_005", shelfLevel: "eye" }))).toEqual([]);
  });

  it("emits nothing when a level is chosen with no focal SKU", () => {
    expect(toPatches(PLANOGRAM, select({ shelfLevel: "eye" }))).toEqual([]);
  });

  it("emits nothing when a focal SKU is chosen with no level", () => {
    expect(toPatches(PLANOGRAM, select({ focalSkuId: "SKU_008" }))).toEqual([]);
  });
});

describe("creative -> ad slot", () => {
  it("sets the creative on the chosen ad slot", () => {
    expect(
      toPatches(PLANOGRAM, select({ adSlotId: "B1_TALKER", creativeId: "AD_2" })),
    ).toEqual([{ op: "set_ad_creative", ad_slot_id: "B1_TALKER", creative_id: "AD_2" }]);
  });

  it("clears a creative with an explicit null, not an omitted key", () => {
    const patches = toPatches(
      PLANOGRAM,
      select({ adSlotId: "B3_ENDCAP", creativeId: CLEAR_CREATIVE }),
    );
    expect(patches).toEqual([
      { op: "set_ad_creative", ad_slot_id: "B3_ENDCAP", creative_id: null },
    ]);
    // An omitted key would fail variant.schema.json, which requires creative_id.
    expect(Object.keys(patches[0]).sort()).toEqual(["ad_slot_id", "creative_id", "op"]);
    expect(JSON.parse(JSON.stringify(patches[0]))).toHaveProperty("creative_id", null);
  });

  it("emits nothing while the creative is left at no change", () => {
    expect(toPatches(PLANOGRAM, select({ adSlotId: "B1_TALKER" }))).toEqual([]);
  });

  it("emits nothing when a creative is chosen with no ad slot", () => {
    expect(toPatches(PLANOGRAM, select({ creativeId: "AD_2" }))).toEqual([]);
  });
});

describe("promo on/off", () => {
  it("carries the SKU's current price unchanged", () => {
    expect(toPatches(PLANOGRAM, select({ focalSkuId: "SKU_008", promo: "on" }))).toEqual([
      { op: "set_price", sku_id: "SKU_008", price: 25, promo: true },
    ]);
  });

  it("turns promo off at the same price", () => {
    expect(toPatches(PLANOGRAM, select({ focalSkuId: "SKU_001", promo: "off" }))).toEqual([
      { op: "set_price", sku_id: "SKU_001", price: 25, promo: false },
    ]);
  });

  it("emits nothing without a focal SKU to price", () => {
    expect(toPatches(PLANOGRAM, select({ promo: "on" }))).toEqual([]);
  });
});

describe("all three controls at once", () => {
  it("emits one patch each, in a fixed order", () => {
    const patches = toPatches(
      PLANOGRAM,
      select({
        focalSkuId: "SKU_008",
        shelfLevel: "eye",
        adSlotId: "B1_TALKER",
        creativeId: "AD_1",
        promo: "on",
      }),
    );
    // Fixed order, because the server hashes the patch list into the variant id
    // that `sim_run_id` is built from: the same selection must reproduce it.
    expect(patches).toEqual([
      { op: "move_sku", sku_id: "SKU_008", to_slot_id: "B1S3P2" },
      { op: "set_ad_creative", ad_slot_id: "B1_TALKER", creative_id: "AD_1" },
      { op: "set_price", sku_id: "SKU_008", price: 25, promo: true },
    ]);
  });
});

describe("nothing selected", () => {
  it("is an empty patch list - the endpoint's exactly-neutral baseline", () => {
    expect(toPatches(PLANOGRAM, EMPTY_SELECTION)).toEqual([]);
  });
});

describe("the request body", () => {
  it("carries exactly the fields POST /whatif allows", () => {
    const body = toRequestBody(PLANOGRAM, select({ focalSkuId: "SKU_008", shelfLevel: "eye" }));
    expect(Object.keys(body).sort()).toEqual([
      "base_planogram_id",
      "focal_sku_id",
      "n_synth",
      "patches",
      "seed",
    ]);
    expect(body).toEqual({
      base_planogram_id: "demo_aisle",
      patches: [{ op: "move_sku", sku_id: "SKU_008", to_slot_id: "B1S3P2" }],
      n_synth: DEFAULT_N_SYNTH,
      seed: DEFAULT_SEED,
      focal_sku_id: "SKU_008",
    });
  });

  it("leaves focal_sku_id out entirely when no SKU is focal", () => {
    const body = toRequestBody(PLANOGRAM, select({ adSlotId: "B1_TALKER", creativeId: "AD_2" }));
    expect(Object.keys(body).sort()).toEqual([
      "base_planogram_id",
      "n_synth",
      "patches",
      "seed",
    ]);
    expect("focal_sku_id" in body).toBe(false);
  });

  it("still carries no extra key after the JSON round trip the fetch does", () => {
    const body = toRequestBody(PLANOGRAM, EMPTY_SELECTION);
    expect(JSON.parse(JSON.stringify(body))).toEqual({
      base_planogram_id: "demo_aisle",
      patches: [],
      n_synth: 10_000,
      seed: 42,
    });
  });

  it("takes base_planogram_id from the resolved planogram, not a constant", () => {
    const other = demoAisle();
    other.planogram_id = "other_aisle";
    expect(toRequestBody(other, EMPTY_SELECTION).base_planogram_id).toBe("other_aisle");
  });

  it("defaults to 10,000 synthetic shoppers per persona at seed 42", () => {
    expect(DEFAULT_N_SYNTH).toBe(10_000);
    expect(DEFAULT_SEED).toBe(42);
  });

  it("accepts an explicit n_synth and seed", () => {
    const body = toRequestBody(PLANOGRAM, EMPTY_SELECTION, { nSynth: 2_000, seed: 7 });
    expect(body.n_synth).toBe(2_000);
    expect(body.seed).toBe(7);
  });
});
