import type { Planogram } from "@/contracts/planogram.schema";

/**
 * The planogram's slot grid, joined to the products that sit in it.
 *
 * Every screen in this project has had the same defect: it names positions.
 * `B1S3P2` is what the dashboard chart's x-axis said, what the what-if controls
 * offered, and what the AI trace printed — while `planogram.skus` has carried
 * `"Orchid Nuts 120g"` the whole time, one join away. Nobody outside this repo
 * can read a slot id, and after a week nobody inside it can either.
 *
 * So this is the one place that join happens, and everything that needs to name
 * a shelf position goes through it. One implementation, for the same reason
 * `resolve()` and the fixation filter each have one: two would drift, and the
 * drift would be a caption confidently naming the wrong product.
 *
 * **Nothing here invents a label.** An unknown slot id returns `null`, and an
 * empty slot says it is empty rather than borrowing the name of whatever the
 * base planogram used to keep there. A wrong caption is worse than a missing
 * one, because the wrong one is what somebody stops checking.
 */

export type ShelfLevel = "top" | "above_eye" | "eye" | "below_eye" | "bottom";

export interface MappedSlot {
  slot_id: string;
  bay_id: string;
  shelf_id: string;
  level: ShelfLevel;
  facings: number;
  /** Null for an empty position — a real slot with no product in it. */
  sku_id: string | null;
  name: string | null;
  brand: string | null;
  category: string | null;
  price: number | null;
  promo: boolean;
  texture_url: string | null;
}

export interface MappedShelf {
  shelf_id: string;
  level: ShelfLevel;
  slots: MappedSlot[];
}

export interface MappedAdSlot {
  ad_slot_id: string;
  bay_id: string;
  type: string;
  attached_to: string | null;
  /** Null when this slot carries no creative — variant D is nothing but these. */
  creative_id: string | null;
  brand: string | null;
  texture_url: string | null;
}

export interface MappedBay {
  bay_id: string;
  type: string;
  shelves: MappedShelf[];
  adSlots: MappedAdSlot[];
}

/** The schema's enum, in English. */
export function levelLabel(level: ShelfLevel | string): string {
  switch (level) {
    case "top":
      return "top shelf";
    case "above_eye":
      return "above eye level";
    case "eye":
      return "eye level";
    case "below_eye":
      return "below eye level";
    case "bottom":
      return "bottom shelf";
    default:
      return level;
  }
}

/** "bay 1" from "B1"; anything unexpected is passed through as itself. */
function bayLabel(bayId: string): string {
  const match = /^B(\d+)$/.exec(bayId);
  return match === null ? bayId : `bay ${match[1]}`;
}

/**
 * The planogram as bays → shelves → slots, each slot carrying its product.
 *
 * Order is the planogram's own throughout, because that order is the shelf: the
 * simulator, the fusion vector and the dashboard's slot vocabulary are all built
 * over it, and re-sorting here would put this screen's shelves in a different
 * arrangement from every other screen's.
 */
export function buildAisle(planogram: Planogram): MappedBay[] {
  const skus = new Map((planogram.skus ?? []).map((sku) => [sku.sku_id, sku]));
  const creatives = new Map(
    (planogram.creatives ?? []).map((creative) => [creative.creative_id, creative]),
  );

  return (planogram.bays ?? []).map((bay) => ({
    bay_id: bay.bay_id,
    type: bay.type,
    shelves: (bay.shelves ?? []).map((shelf) => ({
      shelf_id: shelf.shelf_id,
      level: shelf.level as ShelfLevel,
      slots: (shelf.slots ?? []).map((slot): MappedSlot => {
        const sku = slot.sku_id === null || slot.sku_id === undefined
          ? undefined
          : skus.get(slot.sku_id);
        return {
          slot_id: slot.slot_id,
          bay_id: bay.bay_id,
          shelf_id: shelf.shelf_id,
          level: shelf.level as ShelfLevel,
          facings: slot.facings,
          sku_id: slot.sku_id ?? null,
          name: sku?.name ?? null,
          brand: sku?.brand ?? null,
          category: sku?.category ?? null,
          price: sku?.price ?? null,
          promo: sku?.promo === true,
          texture_url: sku?.texture_url ?? null,
        };
      }),
    })),
    adSlots: (bay.ad_slots ?? []).map((ad): MappedAdSlot => {
      const creative =
        ad.creative_id === null || ad.creative_id === undefined
          ? undefined
          : creatives.get(ad.creative_id);
      return {
        ad_slot_id: ad.ad_slot_id,
        bay_id: bay.bay_id,
        type: ad.type,
        attached_to: ad.attached_to ?? null,
        creative_id: ad.creative_id ?? null,
        brand: creative?.brand ?? null,
        texture_url: creative?.texture_url ?? null,
      };
    }),
  }));
}

export interface SlotDescription {
  /** The product, or "empty" — never a blank where a name goes. */
  product: string;
  /** Where it sits: "bay 1, eye level". */
  position: string;
}

/**
 * The two halves of a slot's description, apart, so a caller can lay them out —
 * a chart axis wants the product alone, a caption wants both.
 *
 * Ad slots are described too: the trace and the attention vectors both name
 * them alongside product slots, and a screen that could label one but not the
 * other would show `B3_ENDCAP` next to a row of proper names.
 */
export function describeSlot(aisle: MappedBay[], slotId: string): SlotDescription | null {
  for (const bay of aisle) {
    for (const shelf of bay.shelves) {
      for (const slot of shelf.slots) {
        if (slot.slot_id !== slotId) continue;
        return {
          product: slot.name ?? "empty",
          position: `${bayLabel(bay.bay_id)}, ${levelLabel(slot.level)}`,
        };
      }
    }
    for (const ad of bay.adSlots) {
      if (ad.ad_slot_id !== slotId) continue;
      return {
        product: ad.creative_id === null ? "no creative" : ad.creative_id,
        position: `${bayLabel(bay.bay_id)}, ${ad.type.replace(/_/g, " ")}`,
      };
    }
  }
  return null;
}

/** `describeSlot` as one string: "Orchid Nuts 120g — bay 1, bottom shelf". */
export function labelForSlot(aisle: MappedBay[], slotId: string): string | null {
  const described = describeSlot(aisle, slotId);
  return described === null ? null : `${described.product} — ${described.position}`;
}

/**
 * `labelForSlot`, falling back to the slot id itself.
 *
 * For the places that must render *something* per row — a chart axis, a table
 * key — where dropping an unknown slot would silently shorten the data. The id
 * is not a label, but it is the truth, and it is what the caller was given.
 */
export function labelOrId(aisle: MappedBay[], slotId: string): string {
  return labelForSlot(aisle, slotId) ?? slotId;
}
