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

/** A box: `Size2`'s face plus the depth along z. */
export interface Size3 extends Size2 {
  d: number;
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

/**
 * ---------------------------------------------------------------------------
 * The aisle display: where the one third-party 3D asset in this repo stands.
 * ---------------------------------------------------------------------------
 *
 * `data/models/WaterBottle.glb` is the Khronos CC0 sample model (see that
 * directory's README for licence, source and hash). It is scenery. It is *not*
 * a SKU, it is not in `data/planograms/`, and nothing below is read by `sim/`,
 * `analytics/` or `scripts/eval.py` — `SlotMapper` builds its rectangles from
 * the planogram document alone, so the prop cannot enter a screen rect, cannot
 * be hovered, clicked or dwelled on, and cannot move a measured number.
 *
 * Everything about the placement follows from three things that are not
 * obvious until you do the maths against this camera (fov 50, at
 * `[bx, 1.5, 2.2]` looking at `[bx, 1.1, 0]`):
 *
 *  - **The floor is off-screen.** The floor at the bay front, `(bx, 0, 0.2)`,
 *    projects to ndc y = -1.07 — below the bottom of the frame at every
 *    aspect ratio. A prop standing on the aisle floor would be visible only
 *    from the waist up, which is exactly the "bottle lying on the floor reads
 *    as a bug" failure in a different costume.
 *  - **The top of the gondola does not fit.** It is the obvious retail answer
 *    and it is wrong here. The top shelf sits at 1.70 m with 0.22 m products
 *    on it, so the fixture's visual top is 1.92 m, not the carcass's 1.80 m;
 *    and the top of the frame at the shelf plane is y ≈ 2.08 m. Worse, the
 *    camera is *below* the carcass top, so the top-shelf facings occlude
 *    anything standing behind them up to y ≈ 1.96 m. That leaves an 11 cm
 *    visible band for a 26 cm bottle.
 *  - **The gap between two bays is the one generous piece of empty frame.**
 *    It is 0.30 m wide (`BAY_GAP_M`), it belongs to no bay, it holds no slot,
 *    and it is in frame from *both* neighbouring stations at every aspect
 *    ratio from 16:9 down to a square window.
 *
 * So: a display plinth standing in the aisle gap between bay 1 and bay 2, with
 * the bottle on its deck. A promotional plinth beside a gondola run is real
 * retail furniture, it is unmistakably not shelf stock, and its own colour
 * (below, in AisleDisplay.tsx) keeps it from reading as another bay.
 *
 * The plinth is also narrow enough to hide nothing. Projecting the deck's
 * corners onto the shelf-face plane z = `FRONT_Z` from each of the three
 * station cameras, its silhouette spans x ∈ [0.556, 0.893] at worst. Bay 1's
 * rightmost slot ends at x = 0.5 and bay 2's leftmost begins at x = 0.95, so
 * no slot is occluded from any station. It is deliberately motionless for the
 * same reason the shopper's own gaze dot is hidden: movement in the periphery
 * would pull attention and corrupt the measurement it sits next to.
 */

/** Which gap the display stands in: between bay `n` and bay `n + 1`. */
export const AISLE_DISPLAY_GAP_INDEX = 0;
/** Plinth column: a slim square section, 5 cm clear of each bay in a 30 cm gap. */
export const AISLE_DISPLAY_COLUMN_W = 0.2;
export const AISLE_DISPLAY_COLUMN_D = 0.2;
/** The deck overhangs the column, exactly as a shelf board overhangs its carcass. */
export const AISLE_DISPLAY_DECK_W = 0.26;
export const AISLE_DISPLAY_DECK_D = 0.26;
export const AISLE_DISPLAY_DECK_THICKNESS_M = 0.04;
/**
 * Height of the deck's top surface, which is where the bottle's base sits.
 *
 * Two constraints pick this number, and both were found by looking at the
 * rendered store rather than at the maths:
 *
 *  - It must clear the eye-level shelf band. `B1S3` runs 1.20–1.42 m and
 *    `B1S3P2` — the deliberately empty position the whole "move a SKU to eye
 *    level" story is built on — is the second half of it. Nothing about this
 *    prop may read as an answer to what belongs in that gap.
 *  - It must not line up with a shelf board. The obvious 1.45 m does clear the
 *    eye-level band, but it is also exactly `B1S2`'s height, and a deck level
 *    with a board reads as the shelf carrying on through the gap — which is
 *    the "it looks like stock" failure arriving by the back door. 1.55 m
 *    matches none of 1.70 / 1.45 / 1.20 / 0.85 / 0.40, and it puts the top of
 *    the bottle at 1.81 m, crowning level with the 1.80 m top of the gondola
 *    run. That reads as composed, which is the whole idea.
 */
export const AISLE_DISPLAY_DECK_TOP_Y = 1.55;
/**
 * Front faces flush with the bay carcass front, so the plinth lines up with
 * the gondola run rather than jutting into the aisle.
 */
export const AISLE_DISPLAY_Z = FRONT_Z - AISLE_DISPLAY_COLUMN_D / 2;

/**
 * The model's own bounding box, in metres, read from the `POSITION` accessor's
 * `min`/`max` in the GLB itself: x and z ±0.05445001, y ±0.130220339. It is
 * centred on its origin, so the y half-extent is what lifts its base onto the
 * deck. Rendered at scale 1: the asset is already modelled life-size, and a
 * water bottle that is not 26 cm tall is a worse advertisement for the
 * renderer than one that is.
 */
export const AISLE_DISPLAY_MODEL_SIZE: Size3 = {
  w: 2 * 0.05445001,
  h: 2 * 0.130220339,
  d: 2 * 0.0544500239,
};

export interface AisleDisplayPlacement {
  column: Vec3;
  columnSize: Size3;
  deck: Vec3;
  deckSize: Size3;
  /** Centre of the model's bounding box, which is the model's own origin. */
  model: Vec3;
  modelSize: Size3;
}

/** Centre of the aisle gap between bay `leftBayIndex` and the bay after it. */
export function gapCenterX(planogram: Planogram, leftBayIndex: number): number {
  return (
    bayLeftX(planogram, leftBayIndex) +
    planogram.bays[leftBayIndex].width_m +
    BAY_GAP_M / 2
  );
}

/** Column, deck and model placement for the aisle display. See the note above. */
export function aisleDisplayPlacement(planogram: Planogram): AisleDisplayPlacement {
  const x = gapCenterX(planogram, AISLE_DISPLAY_GAP_INDEX);
  const deckBottom = AISLE_DISPLAY_DECK_TOP_Y - AISLE_DISPLAY_DECK_THICKNESS_M;

  return {
    // The column runs from the floor to the underside of the deck. Its foot is
    // below the bottom of the frame, exactly as the bays' feet are, so it is
    // cropped by the viewport rather than hovering in it.
    column: { x, y: deckBottom / 2, z: AISLE_DISPLAY_Z },
    columnSize: {
      w: AISLE_DISPLAY_COLUMN_W,
      h: deckBottom,
      d: AISLE_DISPLAY_COLUMN_D,
    },
    deck: {
      x,
      y: AISLE_DISPLAY_DECK_TOP_Y - AISLE_DISPLAY_DECK_THICKNESS_M / 2,
      z: AISLE_DISPLAY_Z,
    },
    deckSize: {
      w: AISLE_DISPLAY_DECK_W,
      h: AISLE_DISPLAY_DECK_THICKNESS_M,
      d: AISLE_DISPLAY_DECK_D,
    },
    model: {
      x,
      y: AISLE_DISPLAY_DECK_TOP_Y + AISLE_DISPLAY_MODEL_SIZE.h / 2,
      z: AISLE_DISPLAY_Z,
    },
    modelSize: AISLE_DISPLAY_MODEL_SIZE,
  };
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
