import type { Planogram, Shelf, Slot } from "@/contracts/planogram.schema";
import type { Patch } from "@/contracts/variant.schema";

/**
 * The three what-if controls, turned into `POST /whatif`'s request body.
 *
 * Pure: no React, no fetch, no clock. This is the whole contract with the
 * endpoint, and the endpoint is unforgiving about it - `WhatIfRequest` is
 * `extra="forbid"`, and the patches are validated against
 * `schemas/variant.schema.json` before anything else happens - so an extra or
 * misspelled key is a 422 on the whole call rather than a field quietly
 * ignored. Keeping the mapping here means it can be asserted object-for-object
 * without a browser or a server.
 *
 * Nothing in this file resolves anything. `resolve()` lives only in
 * `api/app/resolve.py` (CLAUDE.md); all this does is name the patches and let
 * the server apply them.
 */

export type ShelfLevel = Shelf["level"];

/** Every level, top shelf first, which is also the order the controls list. */
export const SHELF_LEVELS: ShelfLevel[] = [
  "top",
  "above_eye",
  "eye",
  "below_eye",
  "bottom",
];

/** A select's "leave this alone" option. Empty, so it is also the initial state. */
export const NO_CHANGE = "";

/**
 * The creative select's "no creative at all" option.
 *
 * It cannot be the empty string: "leave the ad slot as it is" and "take the
 * creative off this ad slot" are different instructions, and the second one is
 * `creative_id: null` - a patch, not the absence of one.
 */
export const CLEAR_CREATIVE = "__clear__";

export type PromoChoice = "" | "on" | "off";

/**
 * What the five dropdowns are showing. Each field is a select's `value`, so the
 * component holds no state this module cannot see.
 */
export interface WhatIfSelection {
  /** "" when no SKU is focal: then nothing can be moved or re-priced. */
  focalSkuId: string;
  /** "" leaves the focal SKU where it is. */
  shelfLevel: ShelfLevel | "";
  /** "" when no ad slot is chosen. */
  adSlotId: string;
  /** "" no change; CLEAR_CREATIVE clears the slot; otherwise a creative_id. */
  creativeId: string;
  /** "" leaves the focal SKU's promo flag alone. */
  promo: PromoChoice;
}

export const EMPTY_SELECTION: WhatIfSelection = {
  focalSkuId: "",
  shelfLevel: "",
  adSlotId: "",
  creativeId: "",
  promo: "",
};

/** SPEC 4.8's defaults, and CLAUDE.md's budget: 10,000 shoppers per persona. */
export const DEFAULT_N_SYNTH = 10_000;
export const DEFAULT_SEED = 42;

/**
 * `POST /whatif`'s request body, and nothing else. `focal_sku_id` is optional
 * and is left out entirely rather than sent as null when no SKU is focal: an
 * absent focal SKU is exactly how the endpoint is told to report no lift.
 */
export interface WhatIfRequestBody {
  base_planogram_id: string;
  patches: Patch[];
  n_synth: number;
  seed: number;
  focal_sku_id?: string;
}

interface Placement {
  bayIndex: number;
  shelf: Shelf;
  slot: Slot;
}

function placementOf(planogram: Planogram, skuId: string): Placement | null {
  for (let bayIndex = 0; bayIndex < planogram.bays.length; bayIndex += 1) {
    for (const shelf of planogram.bays[bayIndex].shelves) {
      for (const slot of shelf.slots) {
        if (slot.sku_id === skuId) return { bayIndex, shelf, slot };
      }
    }
  }
  return null;
}

/** The slot a SKU occupies in this planogram, or null if it is not placed. */
export function slotIdOfSku(planogram: Planogram, skuId: string): string | null {
  return placementOf(planogram, skuId)?.slot.slot_id ?? null;
}

/**
 * Where "put this SKU at eye level" actually sends it.
 *
 * The control names a height, not a slot, so this picks the slot. Three rules,
 * in order:
 *
 *  1. **Its own bay.** Every bay carries all five levels, and a level change
 *     that also walked the SKU across the aisle would change far more than the
 *     one thing the control names.
 *  2. **The first empty slot on that bay's shelf at that level**, if there is
 *     one. `resolve()` moves the SKU and its facings in and leaves the old slot
 *     empty; nothing else on the shelf shifts. The seed planogram keeps one
 *     empty slot per bay at eye and at bottom level, which is exactly why
 *     CLAUDE.md insists empty positions are real slot objects.
 *  3. **Otherwise the first slot on that shelf**, which `resolve()` turns into a
 *     swap: the two SKUs exchange slot and facings. A full shelf can only take a
 *     new product by giving one up, and that is the honest model of it.
 *
 * Returns null - "there is no move to make" - when the SKU is not placed, when
 * its bay has no shelf at that level, when that shelf has no slots, or when the
 * SKU is **already** on a shelf at that level. The last one is not a failure:
 * emitting a move to a different slot of the same shelf would silently change
 * the SKU's horizontal position as well, which nobody asked for.
 */
export function destinationSlotId(
  planogram: Planogram,
  skuId: string,
  level: ShelfLevel,
): string | null {
  const placement = placementOf(planogram, skuId);
  if (placement === null) return null;
  if (placement.shelf.level === level) return null;

  const shelf = planogram.bays[placement.bayIndex].shelves.find(
    (candidate) => candidate.level === level,
  );
  if (shelf === undefined) return null;

  const empty = shelf.slots.find((slot) => slot.sku_id === null);
  return (empty ?? shelf.slots[0])?.slot_id ?? null;
}

/**
 * The focal SKU's slot before and after the patches.
 *
 * `lift_vs_baseline` is a comparison of the SKU across two planograms, and a
 * `move_sku` patch is exactly the case where its slot differs between them -
 * so the per-persona bars have to look the SKU up on each side separately, the
 * same way `whatif.py:_lift_vs_baseline` does. Both are null when no SKU is
 * focal, which is how "there is no lift to report" is said.
 */
export interface FocalSlots {
  baseline: string | null;
  patched: string | null;
}

export function focalSlots(planogram: Planogram, selection: WhatIfSelection): FocalSlots {
  if (selection.focalSkuId === NO_CHANGE) return { baseline: null, patched: null };
  const baseline = slotIdOfSku(planogram, selection.focalSkuId);
  const moved =
    selection.shelfLevel === NO_CHANGE
      ? null
      : destinationSlotId(planogram, selection.focalSkuId, selection.shelfLevel);
  return { baseline, patched: moved ?? baseline };
}

function skuById(planogram: Planogram, skuId: string) {
  return planogram.skus.find((sku) => sku.sku_id === skuId);
}

/**
 * The patch list for a selection.
 *
 * Always in the same order - move, then ad creative, then price - because the
 * server hashes the patch list into the variant id that `sim_run_id` is built
 * from. Two identical selections that produced differently ordered lists would
 * be two unreproducible what-ifs.
 *
 * Nothing selected returns `[]`, which `POST /whatif` treats as the
 * exactly-neutral baseline: it hands back the cached unpatched run rather than
 * simulating anything.
 */
export function toPatches(planogram: Planogram, selection: WhatIfSelection): Patch[] {
  const patches: Patch[] = [];

  if (selection.focalSkuId !== NO_CHANGE && selection.shelfLevel !== NO_CHANGE) {
    const toSlotId = destinationSlotId(planogram, selection.focalSkuId, selection.shelfLevel);
    if (toSlotId !== null) {
      patches.push({ op: "move_sku", sku_id: selection.focalSkuId, to_slot_id: toSlotId });
    }
  }

  if (selection.adSlotId !== NO_CHANGE && selection.creativeId !== NO_CHANGE) {
    patches.push({
      op: "set_ad_creative",
      ad_slot_id: selection.adSlotId,
      // Explicitly null, never an omitted key: variant.schema.json requires
      // creative_id, and null is what "take the creative off" means.
      creative_id: selection.creativeId === CLEAR_CREATIVE ? null : selection.creativeId,
    });
  }

  if (selection.focalSkuId !== NO_CHANGE && selection.promo !== NO_CHANGE) {
    const sku = skuById(planogram, selection.focalSkuId);
    if (sku !== undefined) {
      // The price is carried through unchanged. `set_price` is the only patch
      // that can move the promo flag, and a promo toggle that also silently
      // re-priced the SKU would confound the two effects being measured.
      patches.push({
        op: "set_price",
        sku_id: sku.sku_id,
        price: sku.price,
        promo: selection.promo === "on",
      });
    }
  }

  return patches;
}

export interface RequestOptions {
  nSynth?: number;
  seed?: number;
}

/**
 * The exact body to POST. `base_planogram_id` comes off the resolved planogram
 * rather than a constant here, so the page cannot ask the endpoint to patch a
 * planogram other than the one its controls were built from.
 */
export function toRequestBody(
  planogram: Planogram,
  selection: WhatIfSelection,
  options: RequestOptions = {},
): WhatIfRequestBody {
  const body: WhatIfRequestBody = {
    base_planogram_id: planogram.planogram_id,
    patches: toPatches(planogram, selection),
    n_synth: options.nSynth ?? DEFAULT_N_SYNTH,
    seed: options.seed ?? DEFAULT_SEED,
  };
  if (selection.focalSkuId !== NO_CHANGE) body.focal_sku_id = selection.focalSkuId;
  return body;
}
