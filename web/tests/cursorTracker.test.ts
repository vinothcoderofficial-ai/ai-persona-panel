import { describe, expect, it } from "vitest";
import * as THREE from "three";
import demoAisleJson from "../../data/planograms/demo_aisle.json";
import type { AdSlot, Planogram, Shelf, Slot } from "@/contracts/planogram.schema";
import {
  CURSOR_DWELL_MIN_MS,
  CursorTracker,
  type CursorDwell,
} from "@/capture/CursorTracker";
import { buildScreenRects, type ScreenRect } from "@/store/SlotMapper";
import {
  CAMERA_FAR,
  CAMERA_FOV,
  CAMERA_NEAR,
  adSlotCenter,
  slotCenter,
} from "@/store/geometry";

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

// Two occupied slots on different shelves, one empty slot, one ad fixture.
const SLOT_A = "B1S3P1";
const SLOT_B = "B1S1P1";
const EMPTY_SLOT = "B1S3P2";
const AD_SLOT = "B1_TALKER";

const insideA = slotPixels("B1S3", SLOT_A);
const insideB = slotPixels("B1S1", SLOT_B);
const insideEmpty = slotPixels("B1S3", EMPTY_SLOT);
const insideAd = toPixels(adSlotCenter(planogram, STATION_INDEX, adSlotOf(AD_SLOT)));
const outside = { x: -500, y: -500 };

describe("the cursor dwell fixture", () => {
  it("uses an occupied slot, an empty slot and an ad fixture", () => {
    expect(slotOf("B1S3", SLOT_A).sku_id).not.toBeNull();
    expect(slotOf("B1S1", SLOT_B).sku_id).not.toBeNull();
    expect(slotOf("B1S3", EMPTY_SLOT).sku_id).toBeNull();
    expect(adSlotOf(AD_SLOT).attached_to).toBe("B1S3");
    expect(CURSOR_DWELL_MIN_MS).toBe(300);
  });
});

describe("CursorTracker", () => {
  it("emits one dwell for the full time the cursor held the slot", () => {
    const tracker = new CursorTracker();
    expect(tracker.sample(rects, insideA.x, insideA.y, 1000)).toBeNull();
    expect(tracker.sample(rects, insideA.x, insideA.y, 1300)).toBeNull();

    const dwell = tracker.sample(rects, outside.x, outside.y, 1500);
    expect(dwell).toEqual({ slot_id: SLOT_A, dur_ms: 500 });
  });

  it("emits nothing for a dwell shorter than the threshold", () => {
    const tracker = new CursorTracker();
    tracker.sample(rects, insideA.x, insideA.y, 1000);
    expect(tracker.sample(rects, outside.x, outside.y, 1200)).toBeNull();
    expect(tracker.end(1200)).toBeNull();
  });

  it("counts a return visit as a separate dwell", () => {
    const tracker = new CursorTracker();
    const emitted: (CursorDwell | null)[] = [];

    tracker.sample(rects, insideA.x, insideA.y, 0);
    emitted.push(tracker.sample(rects, insideB.x, insideB.y, 400));
    emitted.push(tracker.sample(rects, insideA.x, insideA.y, 900));
    emitted.push(tracker.end(1400));

    expect(emitted).toEqual([
      { slot_id: SLOT_A, dur_ms: 400 },
      { slot_id: SLOT_B, dur_ms: 500 },
      { slot_id: SLOT_A, dur_ms: 500 },
    ]);
  });

  it("emits exactly the cursor_dwell payload contract", () => {
    const tracker = new CursorTracker();
    tracker.sample(rects, insideA.x, insideA.y, 1000.4);
    const dwell = tracker.end(1500.9);

    expect(dwell).not.toBeNull();
    expect(Object.keys(dwell as object).sort()).toEqual(["dur_ms", "slot_id"]);
    expect(Number.isInteger(dwell?.dur_ms)).toBe(true);
    expect(dwell?.dur_ms).toBe(501);
    expect(typeof dwell?.slot_id).toBe("string");
  });

  it("never dwells on an empty slot or an ad fixture", () => {
    const tracker = new CursorTracker();

    expect(tracker.sample(rects, insideEmpty.x, insideEmpty.y, 0)).toBeNull();
    expect(tracker.sample(rects, insideEmpty.x, insideEmpty.y, 5000)).toBeNull();
    expect(tracker.end(9000)).toBeNull();

    expect(tracker.sample(rects, insideAd.x, insideAd.y, 10000)).toBeNull();
    expect(tracker.sample(rects, insideAd.x, insideAd.y, 15000)).toBeNull();
    expect(tracker.end(19000)).toBeNull();

    // The station's own ad and empty slot are on the board, just not dwellable.
    expect(rects.some((rect) => rect.ad_slot_id === AD_SLOT)).toBe(true);
    expect(rects.some((rect) => rect.slot_id === EMPTY_SLOT)).toBe(false);
  });
});
