import { act } from "react";
import type {
  AdSlot,
  Bay,
  Creative,
  Planogram,
  Shelf,
  Sku,
  Slot,
} from "@/contracts/planogram.schema";
import type { SimResult } from "@/contracts/simresult.schema";
import type { Schedule } from "@/whatif/debounce";

/**
 * The seed aisle `GET /variants/A/resolved` returns, rebuilt here so the what-if
 * tests do not need a server or a JSON import from outside `web/`.
 *
 * It mirrors `data/planograms/demo_aisle.json` in every respect the what-if UI
 * reasons about: three bays, five shelf levels each, two slots per shelf, and
 * the second slot left empty at `eye` and at `bottom` level - the six empty
 * slots CLAUDE.md calls "real slot objects with sku_id: null", and the reason a
 * "move this SKU to eye level" control has anywhere to move it to.
 *
 * Facings and geometry are a simple deterministic pattern rather than a copy of
 * the seed's: they feed `sim/saliency.py`, which runs on the server, and nothing
 * in `web/src/whatif/` reads them.
 */

const LEVELS: Shelf["level"][] = ["top", "above_eye", "eye", "below_eye", "bottom"];
const BRANDS = ["Crunch", "Zapp", "Nimbus", "Orchid"];
const CATEGORIES = ["chips", "cola", "biscuits", "nuts"];
const GRAMS = [100, 110, 120, 130, 140];

/** A shelf keeps its second slot free at these levels, exactly as the seed does. */
const KEEPS_AN_EMPTY_SLOT: Shelf["level"][] = ["eye", "bottom"];

function nonEmpty<T>(items: T[]): [T, ...T[]] {
  const [first, ...rest] = items;
  if (first === undefined) throw new Error("the fixture built an empty list");
  return [first, ...rest];
}

function skuId(n: number): string {
  return `SKU_${String(n).padStart(3, "0")}`;
}

function sku(n: number): Sku {
  const brand = BRANDS[(n - 1) % BRANDS.length];
  const category = CATEGORIES[Math.floor(((n - 1) % 8) / 2)];
  return {
    sku_id: skuId(n),
    name: `${brand} ${category} ${GRAMS[(n - 1) % GRAMS.length]}g`,
    brand,
    category,
    // 25, 30, 35, 40, 45, 50, 55, 25, ... - the seed's cycle, so a set_price
    // patch that "carries the current price unchanged" has a real number to carry.
    price: 25 + 5 * ((n - 1) % 7),
    // The seed's three promo SKUs: SKU_001, SKU_010, SKU_019.
    promo: n % 9 === 1,
    texture_url: `/textures/sku_${String(n).padStart(3, "0")}.png`,
    color_lab: [62, 48, 51],
  };
}

function slot(slotId: string, occupant: string | null, index: number): Slot {
  return {
    slot_id: slotId,
    sku_id: occupant,
    facings: occupant === null ? 0 : 2 + (index % 3),
    x_m: 0.05 + index * 0.55,
    width_m: 0.5,
    height_m: 0.22,
  };
}

function adSlots(bayNumber: number): AdSlot[] {
  if (bayNumber === 1) {
    return [{
      ad_slot_id: "B1_TALKER",
      type: "shelf_talker",
      attached_to: "B1S3",
      x_m: 0.3,
      width_m: 0.4,
      creative_id: null,
    }];
  }
  if (bayNumber === 2) {
    return [{
      ad_slot_id: "B2_DECAL",
      type: "floor_decal",
      attached_to: "B2",
      x_m: 0.5,
      width_m: 0.8,
      creative_id: null,
    }];
  }
  return [{
    ad_slot_id: "B3_ENDCAP",
    type: "endcap_header",
    attached_to: "B3",
    x_m: 0.2,
    width_m: 1,
    creative_id: "AD_1",
  }];
}

function bay(bayNumber: number): Bay {
  let next = (bayNumber - 1) * 8 + 1;
  const shelves = LEVELS.map((level, shelfIndex) => {
    const shelfId = `B${bayNumber}S${shelfIndex + 1}`;
    const slots = [0, 1].map((slotIndex) => {
      const empty = slotIndex === 1 && KEEPS_AN_EMPTY_SLOT.includes(level);
      const occupant = empty ? null : skuId(next++);
      return slot(`${shelfId}P${slotIndex + 1}`, occupant, slotIndex);
    });
    const shelf: Shelf = {
      shelf_id: shelfId,
      height_m: 1.8 - shelfIndex * 0.3,
      level,
      slots,
    };
    return shelf;
  });
  return {
    bay_id: `B${bayNumber}`,
    type: bayNumber === 3 ? "endcap" : "shelf",
    width_m: 1.2,
    height_m: 2,
    station: { camera_pos: [0, 1.5, 2.2], look_at: [0, 1.1, 0] },
    shelves: nonEmpty(shelves),
    ad_slots: adSlots(bayNumber),
  };
}

const CREATIVES: Creative[] = [
  { creative_id: "AD_1", brand: "Crunch", texture_url: "/textures/ad_1.png" },
  { creative_id: "AD_2", brand: "Zapp", texture_url: "/textures/ad_2.png" },
];

/** A fresh copy each call, so a test that mutates one cannot affect another. */
export function demoAisle(): Planogram {
  return {
    planogram_id: "demo_aisle",
    name: "Demo snacks aisle",
    source: "manual",
    bays: nonEmpty([bay(1), bay(2), bay(3)]),
    skus: nonEmpty(Array.from({ length: 24 }, (_, i) => sku(i + 1))),
    creatives: CREATIVES.map((creative) => ({ ...creative })),
  };
}

/**
 * One persona's SimResult, as `per_persona` carries it. Only `fixation_prob`
 * varies: it is the field the per-persona lift bars are computed from, and the
 * rest is filled in so the object really is a `SimResult` and not a subset that
 * happens to typecheck.
 */
export function simResult(
  personaId: string,
  fixationProb: Record<string, number>,
  overrides: Partial<SimResult> = {},
): SimResult {
  return {
    sim_run_id: `sim_${personaId}`,
    variant_id: "wi_test",
    persona_id: personaId,
    n_runs: 10_000,
    seed: 42,
    fixation_prob: fixationProb,
    dwell_ms_mean: {},
    ad_slot_attention: {},
    purchase_share: {},
    path: { stations_mean: 2.4, duration_s_mean: 31.2 },
    ...overrides,
  };
}

export interface FakeClock {
  /** Hand this to `createDebouncer` or to `WhatIfPanel`'s `schedule` prop. */
  schedule: Schedule;
  /** Every delay that was asked for, in order - SPEC M9 says all of them are 300. */
  delays: number[];
  /** Fire every timer still pending. Cancelled ones are already gone. */
  flush(): void;
}

/** A debounce timer under the test's control, so nothing ever waits on a clock. */
export function fakeClock(): FakeClock {
  const pending = new Map<number, () => void>();
  const delays: number[] = [];
  let nextHandle = 0;
  return {
    delays,
    schedule: (fn, delayMs) => {
      const handle = nextHandle++;
      delays.push(delayMs);
      pending.set(handle, fn);
      return () => {
        pending.delete(handle);
      };
    },
    flush: () => {
      const due = [...pending.values()];
      pending.clear();
      for (const fn of due) fn();
    },
  };
}

const NATIVE_SELECT_VALUE = Object.getOwnPropertyDescriptor(
  HTMLSelectElement.prototype,
  "value",
);

/**
 * Pick an option the way a person would.
 *
 * React installs its own `value` property on every controlled form node and
 * skips a change event whose value it believes it already knows, so assigning
 * `select.value` directly is silently swallowed. Going through the prototype's
 * setter leaves React's tracker holding the old value, which is exactly what a
 * real interaction looks like from React's side.
 */
export function chooseOption(select: HTMLSelectElement, value: string): void {
  const setter = NATIVE_SELECT_VALUE?.set;
  if (setter === undefined) throw new Error("HTMLSelectElement has no value setter");
  act(() => {
    setter.call(select, value);
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
}
