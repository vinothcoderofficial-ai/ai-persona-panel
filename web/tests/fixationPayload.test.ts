import { describe, expect, it } from "vitest";
import * as THREE from "three";
import demoAisleJson from "../../data/planograms/demo_aisle.json";
import type { AdSlot, Planogram, Shelf, Slot } from "@/contracts/planogram.schema";
import { fixationPayload, type Fixation } from "@/capture/FixationFilter";
import { buildScreenRects, type ScreenRect } from "@/store/SlotMapper";
import {
  CAMERA_FAR,
  CAMERA_FOV,
  CAMERA_NEAR,
  adSlotCenter,
  slotCenter,
} from "@/store/geometry";

/**
 * The last step of SPEC M2's pipeline: centroid -> hitTest -> the `fixation`
 * payload. Same station setup as web/tests/cursorTracker.test.ts, so both
 * trackers are checked against the same projected rectangles.
 */

const planogram = demoAisleJson as unknown as Planogram;

const VIEWPORT_W = 1280;
const VIEWPORT_H = 800;
const STATION_INDEX = 0;

const station = planogram.bays[STATION_INDEX];

const camera = new THREE.PerspectiveCamera(
  CAMERA_FOV,
  VIEWPORT_W / VIEWPORT_H,
  CAMERA_NEAR,
  CAMERA_FAR,
);
camera.position.set(...station.station.camera_pos);
camera.lookAt(new THREE.Vector3(...station.station.look_at));
camera.updateMatrixWorld();
camera.updateProjectionMatrix();

const rects: ScreenRect[] = buildScreenRects(planogram, camera, VIEWPORT_W, VIEWPORT_H);

function toPixels(point: { x: number; y: number; z: number }): { x: number; y: number } {
  const ndc = new THREE.Vector3(point.x, point.y, point.z).project(camera);
  return {
    x: (ndc.x * 0.5 + 0.5) * VIEWPORT_W,
    y: (-ndc.y * 0.5 + 0.5) * VIEWPORT_H,
  };
}

function shelfOf(shelfId: string): Shelf {
  const shelf = station.shelves.find((candidate) => candidate.shelf_id === shelfId);
  if (!shelf) throw new Error(`fixture is missing shelf ${shelfId}`);
  return shelf;
}

function slotOf(shelfId: string, slotId: string): Slot {
  const slot = shelfOf(shelfId).slots.find((candidate) => candidate.slot_id === slotId);
  if (!slot) throw new Error(`fixture is missing slot ${slotId}`);
  return slot;
}

function slotPixels(shelfId: string, slotId: string): { x: number; y: number } {
  return toPixels(
    slotCenter(planogram, STATION_INDEX, shelfOf(shelfId), slotOf(shelfId, slotId)),
  );
}

function adSlotOf(adSlotId: string): AdSlot {
  const ad = station.ad_slots.find((candidate) => candidate.ad_slot_id === adSlotId);
  if (!ad) throw new Error(`fixture is missing ad slot ${adSlotId}`);
  return ad;
}

/** A fixation whose centroid is `at`; the duration is irrelevant to hit testing. */
function fixationAt(at: { x: number; y: number }): Fixation {
  return {
    x: at.x,
    y: at.y,
    dur_ms: 240,
    t_start: 1000,
    t_end: 1240,
    n_samples: 12,
  };
}

const OCCUPIED_SLOT = "B1S3P1";
const EMPTY_SLOT = "B1S3P2";
const AD_SLOT = "B1_TALKER";

describe("fixationPayload", () => {
  it("emits exactly the SPEC 4.3 fixation payload", () => {
    const payload = fixationPayload(fixationAt(slotPixels("B1S3", OCCUPIED_SLOT)), rects);

    expect(Object.keys(payload).sort()).toEqual([
      "dur_ms",
      "shelf_id",
      "slot_id",
      "x",
      "y",
    ]);
    expect(payload.dur_ms).toBe(240);
  });

  it("names the product slot and the shelf it sits on", () => {
    const payload = fixationPayload(fixationAt(slotPixels("B1S3", OCCUPIED_SLOT)), rects);

    expect(payload.slot_id).toBe(OCCUPIED_SLOT);
    expect(payload.shelf_id).toBe("B1S3");
  });

  it("records the shelf when the centroid is on no product (SPEC M2)", () => {
    // An empty slot is shelf space, not a product target, so the gaze landed in
    // the shelf band and nowhere else. This is the case that makes "move a SKU
    // to eye level" measurable: attention on empty eye-level space is a signal.
    expect(slotOf("B1S3", EMPTY_SLOT).sku_id).toBeNull();
    const payload = fixationPayload(fixationAt(slotPixels("B1S3", EMPTY_SLOT)), rects);

    expect(payload.slot_id).toBeNull();
    expect(payload.shelf_id).toBe("B1S3");
  });

  it("carries the centroid through untouched", () => {
    const at = slotPixels("B1S1", "B1S1P2");
    const payload = fixationPayload(fixationAt(at), rects);

    expect(payload.x).toBe(at.x);
    expect(payload.y).toBe(at.y);
  });

  it("reports nothing at all when the centroid misses the shelf entirely", () => {
    const payload = fixationPayload(fixationAt({ x: -500, y: -500 }), rects);

    expect(payload.slot_id).toBeNull();
    expect(payload.shelf_id).toBeNull();
  });

  it("reports an ad fixture as neither a slot nor a shelf", () => {
    // SPEC 4.3 gives the fixation payload two identity fields, slot_id and
    // shelf_id, and an ad fixture is neither: SlotMapper hangs it off the bay,
    // not off a shelf. The fixation is still recorded - it counts toward
    // n_fixations and fixation_coverage - it simply names no target.
    const at = toPixels(adSlotCenter(planogram, STATION_INDEX, adSlotOf(AD_SLOT)));
    const payload = fixationPayload(fixationAt(at), rects);

    expect(rects.some((rect) => rect.ad_slot_id === AD_SLOT)).toBe(true);
    expect(payload.slot_id).toBeNull();
    expect(payload.shelf_id).toBeNull();
  });

  it("uses the 25 px gaze padding by default and honours an override", () => {
    // Gaze is not the cursor: SlotMapper's default padding exists because a
    // webcam estimate that lands just off a facing was still a look at it.
    // One isolated rectangle, so the only thing under test is the padding.
    const lone: ScreenRect[] = [
      {
        kind: "slot",
        bay_id: "BX",
        shelf_id: "BXS1",
        slot_id: "BXS1P1",
        ad_slot_id: null,
        x: 100,
        y: 100,
        w: 80,
        h: 60,
        depth: 2,
      },
    ];
    const tenPxLeftOfIt = fixationAt({ x: 90, y: 130 });

    expect(fixationPayload(tenPxLeftOfIt, lone).slot_id).toBe("BXS1P1");
    expect(fixationPayload(tenPxLeftOfIt, lone, 0).slot_id).toBeNull();
    expect(fixationPayload(tenPxLeftOfIt, lone, 0).shelf_id).toBeNull();
  });
});
