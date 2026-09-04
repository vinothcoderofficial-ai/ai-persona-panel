import * as THREE from "three";
import type { Planogram } from "@/contracts/planogram.schema";
import {
  adSlotCenter,
  adSlotSize,
  isFlatAd,
  quadCorners,
  shelfCenter,
  shelfSize,
  slotCenter,
  slotSize,
  type Vec3,
} from "@/store/geometry";

/**
 * Projects the planogram to screen rectangles for the current station.
 *
 * Pure maths on top of `camera.project` — no React, no renderer, no scene
 * objects — so it runs identically in the browser and in jsdom with a bare
 * PerspectiveCamera. Rebuild on resize and on station change.
 */

export type RectKind = "slot" | "ad_slot" | "shelf";

export interface ScreenRect {
  kind: RectKind;
  bay_id: string;
  shelf_id: string | null;
  slot_id: string | null;
  ad_slot_id: string | null;
  /** Axis-aligned bounding rectangle in CSS pixels, origin top-left. */
  x: number;
  y: number;
  w: number;
  h: number;
  /** Distance from the camera to the target centre; the nearest wins a tie. */
  depth: number;
}

export interface Hit {
  bay_id: string;
  shelf_id?: string;
  slot_id?: string;
  ad_slot_id?: string;
}

/**
 * A point this close to a rectangle's edge is ambiguous: fixtures butt up
 * against each other, so an edge-grazing hit is left to the padded pass rather
 * than letting the kind precedence hand it to whichever fixture happens to
 * share that edge.
 */
const EDGE_EPS_PX = 0.5;

/** Exact hits on a product beat an ad, which beats the enclosing shelf. */
const KIND_PRECEDENCE: RectKind[] = ["slot", "ad_slot", "shelf"];

const DEPTH_EPS = 1e-9;

interface Target {
  kind: RectKind;
  bay_id: string;
  shelf_id: string | null;
  slot_id: string | null;
  ad_slot_id: string | null;
  center: Vec3;
  corners: Vec3[];
}

function collectTargets(planogram: Planogram): Target[] {
  const targets: Target[] = [];

  planogram.bays.forEach((bay, bayIndex) => {
    for (const shelf of bay.shelves) {
      const bandCenter = shelfCenter(planogram, bayIndex, shelf);
      targets.push({
        kind: "shelf",
        bay_id: bay.bay_id,
        shelf_id: shelf.shelf_id,
        slot_id: null,
        ad_slot_id: null,
        center: bandCenter,
        corners: quadCorners(bandCenter, shelfSize(bay, shelf)),
      });

      for (const slot of shelf.slots) {
        // An empty slot is shelf space, never a product target.
        if (slot.sku_id === null) continue;
        const center = slotCenter(planogram, bayIndex, shelf, slot);
        targets.push({
          kind: "slot",
          bay_id: bay.bay_id,
          shelf_id: shelf.shelf_id,
          slot_id: slot.slot_id,
          ad_slot_id: null,
          center,
          corners: quadCorners(center, slotSize(slot)),
        });
      }
    }

    for (const ad of bay.ad_slots) {
      const center = adSlotCenter(planogram, bayIndex, ad);
      targets.push({
        kind: "ad_slot",
        bay_id: bay.bay_id,
        shelf_id: null,
        slot_id: null,
        ad_slot_id: ad.ad_slot_id,
        center,
        corners: quadCorners(center, adSlotSize(planogram, bayIndex, ad), isFlatAd(ad)),
      });
    }
  });

  return targets;
}

export function buildScreenRects(
  planogram: Planogram,
  camera: THREE.Camera,
  viewportW: number,
  viewportH: number,
): ScreenRect[] {
  camera.updateMatrixWorld();
  const eye = camera.getWorldPosition(new THREE.Vector3());
  const point = new THREE.Vector3();
  const rects: ScreenRect[] = [];

  for (const target of collectTargets(planogram)) {
    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    let visible = true;

    for (const corner of target.corners) {
      point.set(corner.x, corner.y, corner.z).applyMatrix4(camera.matrixWorldInverse);
      if (point.z >= 0) {
        // Behind the camera: the projection would fold the rectangle inside out.
        visible = false;
        break;
      }
      point.applyMatrix4(camera.projectionMatrix);

      const px = (point.x * 0.5 + 0.5) * viewportW;
      const py = (-point.y * 0.5 + 0.5) * viewportH;
      minX = Math.min(minX, px);
      maxX = Math.max(maxX, px);
      minY = Math.min(minY, py);
      maxY = Math.max(maxY, py);
    }
    if (!visible) continue;

    rects.push({
      kind: target.kind,
      bay_id: target.bay_id,
      shelf_id: target.shelf_id,
      slot_id: target.slot_id,
      ad_slot_id: target.ad_slot_id,
      x: minX,
      y: minY,
      w: maxX - minX,
      h: maxY - minY,
      depth: eye.distanceTo(
        point.set(target.center.x, target.center.y, target.center.z),
      ),
    });
  }

  return rects;
}

/** How far inside a rectangle the point is; negative means outside. */
function margin(rect: ScreenRect, x: number, y: number): number {
  return Math.min(x - rect.x, rect.x + rect.w - x, y - rect.y, rect.y + rect.h - y);
}

function toHit(rect: ScreenRect): Hit {
  const hit: Hit = { bay_id: rect.bay_id };
  if (rect.shelf_id !== null) hit.shelf_id = rect.shelf_id;
  if (rect.slot_id !== null) hit.slot_id = rect.slot_id;
  if (rect.ad_slot_id !== null) hit.ad_slot_id = rect.ad_slot_id;
  return hit;
}

/**
 * Screen point -> what the shopper was looking at.
 *
 * First pass: rectangles the point is properly inside, product before ad before
 * shelf. Second pass: the same order with every rectangle widened by `padPx`,
 * so padding never steals a hit from an exact match.
 */
export function hitTest(
  rects: ScreenRect[],
  x: number,
  y: number,
  padPx = 25,
): Hit | null {
  for (const floor of [EDGE_EPS_PX, -padPx]) {
    for (const kind of KIND_PRECEDENCE) {
      let best: ScreenRect | null = null;
      let bestDepth = Infinity;
      let bestMargin = -Infinity;

      for (const rect of rects) {
        if (rect.kind !== kind) continue;
        const m = margin(rect, x, y);
        if (m <= floor) continue;
        const nearer = rect.depth < bestDepth - DEPTH_EPS;
        const sameDepth = Math.abs(rect.depth - bestDepth) <= DEPTH_EPS;
        if (best === null || nearer || (sameDepth && m > bestMargin)) {
          best = rect;
          bestDepth = rect.depth;
          bestMargin = m;
        }
      }
      if (best !== null) return toHit(best);
    }
  }
  return null;
}
