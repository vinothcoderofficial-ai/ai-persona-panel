import { describe, expect, it } from "vitest";
import * as THREE from "three";
import demoAisleJson from "../../data/planograms/demo_aisle.json";
import type { AdSlot, Bay, Planogram, Shelf, Slot } from "@/contracts/planogram.schema";
import {
  CAMERA_FAR,
  CAMERA_FOV,
  CAMERA_NEAR,
  adSlotCenter,
  slotCenter,
} from "@/store/geometry";
import { buildScreenRects, hitTest } from "@/store/SlotMapper";

const planogram = demoAisleJson as unknown as Planogram;

const VIEWPORT_W = 1280;
const VIEWPORT_H = 800;

interface SlotRef {
  bayIndex: number;
  shelf: Shelf;
  slot: Slot;
}

function slotsWhere(keep: (slot: Slot) => boolean): SlotRef[] {
  const out: SlotRef[] = [];
  planogram.bays.forEach((bay, bayIndex) => {
    for (const shelf of bay.shelves) {
      for (const slot of shelf.slots) {
        if (keep(slot)) out.push({ bayIndex, shelf, slot });
      }
    }
  });
  return out;
}

const occupiedSlots = slotsWhere((slot) => slot.sku_id !== null);
const emptySlots = slotsWhere((slot) => slot.sku_id === null);

/** The camera the StationController parks at a bay, rebuilt without a renderer. */
function stationCamera(bay: Bay): THREE.PerspectiveCamera {
  const camera = new THREE.PerspectiveCamera(
    CAMERA_FOV,
    VIEWPORT_W / VIEWPORT_H,
    CAMERA_NEAR,
    CAMERA_FAR,
  );
  camera.position.set(...bay.station.camera_pos);
  camera.lookAt(new THREE.Vector3(...bay.station.look_at));
  camera.updateMatrixWorld();
  camera.updateProjectionMatrix();
  return camera;
}

/** World point -> CSS pixels, the conversion the browser applies to a click. */
function toPixels(
  point: { x: number; y: number; z: number },
  camera: THREE.Camera,
): { x: number; y: number } {
  const ndc = new THREE.Vector3(point.x, point.y, point.z).project(camera);
  return {
    x: (ndc.x * 0.5 + 0.5) * VIEWPORT_W,
    y: (-ndc.y * 0.5 + 0.5) * VIEWPORT_H,
  };
}

function adSlotOf(bay: Bay, adSlotId: string): AdSlot {
  const ad = bay.ad_slots.find((candidate) => candidate.ad_slot_id === adSlotId);
  if (!ad) throw new Error(`fixture is missing ad slot ${adSlotId}`);
  return ad;
}

describe("the demo_aisle fixture", () => {
  it("has 3 bays, 24 occupied slots and 6 empty slots", () => {
    expect(planogram.bays).toHaveLength(3);
    expect(occupiedSlots).toHaveLength(24);
    expect(emptySlots).toHaveLength(6);
  });
});

describe("SlotMapper", () => {
  it("returns the slot id at the projected centre of every occupied slot, at every station", () => {
    let checked = 0;
    for (const station of planogram.bays) {
      const camera = stationCamera(station);
      const rects = buildScreenRects(planogram, camera, VIEWPORT_W, VIEWPORT_H);
      for (const { bayIndex, shelf, slot } of occupiedSlots) {
        const centre = toPixels(slotCenter(planogram, bayIndex, shelf, slot), camera);
        const hit = hitTest(rects, centre.x, centre.y);
        expect(
          hit?.slot_id,
          `${slot.slot_id} at station ${station.bay_id}`,
        ).toBe(slot.slot_id);
        checked += 1;
      }
    }
    expect(checked).toBe(occupiedSlots.length * planogram.bays.length);
  });

  it("never reports an empty slot, and falls back to its shelf instead", () => {
    for (const station of planogram.bays) {
      const camera = stationCamera(station);
      const rects = buildScreenRects(planogram, camera, VIEWPORT_W, VIEWPORT_H);
      for (const { slot } of emptySlots) {
        expect(rects.some((rect) => rect.slot_id === slot.slot_id)).toBe(false);
      }
      for (const { bayIndex, shelf, slot } of emptySlots) {
        const centre = toPixels(slotCenter(planogram, bayIndex, shelf, slot), camera);
        const hit = hitTest(rects, centre.x, centre.y);
        expect(hit?.slot_id, `${slot.slot_id} at station ${station.bay_id}`).toBeUndefined();
        expect(hit?.shelf_id, `${slot.slot_id} at station ${station.bay_id}`).toBe(
          shelf.shelf_id,
        );
      }
    }
  });

  it("reports the ad slot at its own projected centre", () => {
    const bayIndex = planogram.bays.findIndex((bay) => bay.bay_id === "B3");
    const ad = adSlotOf(planogram.bays[bayIndex], "B3_ENDCAP");
    expect(ad.creative_id).toBe("AD_1");

    const camera = stationCamera(planogram.bays[bayIndex]);
    const rects = buildScreenRects(planogram, camera, VIEWPORT_W, VIEWPORT_H);
    const centre = toPixels(adSlotCenter(planogram, bayIndex, ad), camera);
    const hit = hitTest(rects, centre.x, centre.y);

    expect(hit?.ad_slot_id).toBe("B3_ENDCAP");
    expect(hit?.slot_id).toBeUndefined();
  });

  it("returns null far outside every rect", () => {
    const camera = stationCamera(planogram.bays[0]);
    const rects = buildScreenRects(planogram, camera, VIEWPORT_W, VIEWPORT_H);
    expect(hitTest(rects, -5000, -5000)).toBeNull();
    expect(hitTest(rects, VIEWPORT_W * 50, VIEWPORT_H * 50)).toBeNull();
  });
});
