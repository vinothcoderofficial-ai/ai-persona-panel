import type { AdSlot, Bay, Planogram, Shelf, Slot } from "@/contracts/planogram.schema";

/**
 * The single source of world-space placement for the store.
 *
 * Both the rendered scene (Bay/ProductSlot/AdSlot) and the screen-space mapper
 * (SlotMapper) derive every position from here. If the two computed positions
 * independently they would drift, and the hitTest test would silently verify
 * nothing.
 *
 * Units are metres. +x runs along the aisle, +y is up, +z is toward the shopper.
 */

/** Gap between neighbouring bays (SPEC M1). */
export const BAY_GAP_M = 0.3;
/** Depth of the bay carcass (SPEC M1). */
export const BAY_DEPTH_M = 0.4;
/** Front face of the bay carcass: products are mounted here. */
export const FRONT_Z = BAY_DEPTH_M / 2;

/** Shelf boards are thin boxes that overhang the carcass front by 4 cm. */
export const SHELF_BOARD_THICKNESS_M = 0.03;
export const SHELF_BOARD_DEPTH_M = 0.44;
export const SHELF_BOARD_Z = (SHELF_BOARD_DEPTH_M - BAY_DEPTH_M) / 2;
/** Front edge of a shelf board. */
export const SHELF_LIP_Z = SHELF_BOARD_Z + SHELF_BOARD_DEPTH_M / 2;

/** Ad fixtures are mounted proud of the shelf lip so they never z-fight. */
export const AD_Z = SHELF_LIP_Z + 0.01;
/** A shelf talker hangs under the board it is attached to. */
export const AD_TALKER_HEIGHT_M = 0.1;
export const AD_TALKER_DROP_M = 0.06;
/** A header runs across the top of the bay. */
export const AD_HEADER_HEIGHT_M = 0.2;
/** A floor decal lies flat in front of the bay. */
export const FLOOR_DECAL_DEPTH_M = 0.3;
export const FLOOR_DECAL_Y = 0.02;

/** Camera the StationController drives; SlotMapper must be given the same one. */
export const CAMERA_FOV = 50;
export const CAMERA_NEAR = 0.1;
export const CAMERA_FAR = 100;

export interface Vec3 {
  x: number;
  y: number;
  z: number;
}

export interface Size2 {
  w: number;
  h: number;
}

/** Bay i is centred on its own station x, so look_at lines up with the bay. */
export function bayCenterX(planogram: Planogram, bayIndex: number): number {
  const bay = planogram.bays[bayIndex];
  return bayIndex * (bay.width_m + BAY_GAP_M);
}

export function bayLeftX(planogram: Planogram, bayIndex: number): number {
  return bayCenterX(planogram, bayIndex) - planogram.bays[bayIndex].width_m / 2;
}

/** Centre of the product facings in a slot; the product sits on the board. */
export function slotCenter(
  planogram: Planogram,
  bayIndex: number,
  shelf: Shelf,
  slot: Slot,
): Vec3 {
  return {
    x: bayLeftX(planogram, bayIndex) + slot.x_m + slot.width_m / 2,
    y: shelf.height_m + slot.height_m / 2,
    z: FRONT_Z,
  };
}

export function slotSize(slot: Slot): Size2 {
  return { w: slot.width_m, h: slot.height_m };
}

/**
 * Height of the band a shelf owns: tall enough for its own products, and at
 * least the clearance to the next shelf above, so the bands of a bay tile its
 * whole face. Gaze that lands between two products still falls in a band.
 */
export function shelfBandHeight(bay: Bay, shelf: Shelf): number {
  let tallestSlot = 0;
  for (const slot of shelf.slots) tallestSlot = Math.max(tallestSlot, slot.height_m);

  let nextAbove = bay.height_m;
  for (const other of bay.shelves) {
    if (other.height_m > shelf.height_m && other.height_m < nextAbove) {
      nextAbove = other.height_m;
    }
  }
  return Math.max(tallestSlot, nextAbove - shelf.height_m, 0);
}

export function shelfCenter(planogram: Planogram, bayIndex: number, shelf: Shelf): Vec3 {
  return {
    x: bayCenterX(planogram, bayIndex),
    y: shelf.height_m + shelfBandHeight(planogram.bays[bayIndex], shelf) / 2,
    z: FRONT_Z,
  };
}

export function shelfSize(bay: Bay, shelf: Shelf): Size2 {
  return { w: bay.width_m, h: shelfBandHeight(bay, shelf) };
}

/** Centre of a shelf board: its top surface is the shelf height. */
export function shelfBoardCenter(
  planogram: Planogram,
  bayIndex: number,
  shelf: Shelf,
): Vec3 {
  return {
    x: bayCenterX(planogram, bayIndex),
    y: shelf.height_m - SHELF_BOARD_THICKNESS_M / 2,
    z: SHELF_BOARD_Z,
  };
}

/** `attached_to` is either a shelf_id or a bay_id; null means it is the bay. */
export function adSlotShelf(bay: Bay, ad: AdSlot): Shelf | null {
  return bay.shelves.find((shelf) => shelf.shelf_id === ad.attached_to) ?? null;
}

/** Floor decals lie in the ground plane; every other ad fixture stands upright. */
export function isFlatAd(ad: AdSlot): boolean {
  return ad.type === "floor_decal";
}

/**
 * Ad placement follows the attachment first: attached to a shelf it hangs under
 * that board, attached to the bay it is a floor decal on the ground or a header
 * across the top.
 */
export function adSlotCenter(planogram: Planogram, bayIndex: number, ad: AdSlot): Vec3 {
  const bay = planogram.bays[bayIndex];
  const x = bayLeftX(planogram, bayIndex) + ad.x_m + ad.width_m / 2;
  const shelf = adSlotShelf(bay, ad);

  if (shelf) return { x, y: shelf.height_m - AD_TALKER_DROP_M, z: AD_Z };
  if (isFlatAd(ad)) {
    return { x, y: FLOOR_DECAL_Y, z: FRONT_Z + FLOOR_DECAL_DEPTH_M / 2 };
  }
  return { x, y: bay.height_m - AD_HEADER_HEIGHT_M / 2, z: AD_Z };
}

/** For a flat ad the height is a depth along +z, not along +y. */
export function adSlotSize(planogram: Planogram, bayIndex: number, ad: AdSlot): Size2 {
  const bay = planogram.bays[bayIndex];
  const shelf = adSlotShelf(bay, ad);

  if (shelf) return { w: ad.width_m, h: AD_TALKER_HEIGHT_M };
  if (isFlatAd(ad)) return { w: ad.width_m, h: FLOOR_DECAL_DEPTH_M };
  return { w: ad.width_m, h: AD_HEADER_HEIGHT_M };
}

/** The four corners of a quad, upright in the xy plane or flat in the xz plane. */
export function quadCorners(center: Vec3, size: Size2, flat = false): Vec3[] {
  const hw = size.w / 2;
  const hh = size.h / 2;

  if (flat) {
    return [
      { x: center.x - hw, y: center.y, z: center.z - hh },
      { x: center.x + hw, y: center.y, z: center.z - hh },
      { x: center.x - hw, y: center.y, z: center.z + hh },
      { x: center.x + hw, y: center.y, z: center.z + hh },
    ];
  }
  return [
    { x: center.x - hw, y: center.y - hh, z: center.z },
    { x: center.x + hw, y: center.y - hh, z: center.z },
    { x: center.x - hw, y: center.y + hh, z: center.z },
    { x: center.x + hw, y: center.y + hh, z: center.z },
  ];
}
