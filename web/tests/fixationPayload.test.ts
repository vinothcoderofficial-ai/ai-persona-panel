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

/** The camera parked at one shelf station, exactly as StationController leaves it. */
function cameraAt(stationIndex: number): THREE.PerspectiveCamera {
  const bay = planogram.bays[stationIndex];
  const eye = new THREE.PerspectiveCamera(
    CAMERA_FOV,
    VIEWPORT_W / VIEWPORT_H,
    CAMERA_NEAR,
    CAMERA_FAR,
  );
  eye.position.set(...bay.station.camera_pos);
  eye.lookAt(new THREE.Vector3(...bay.station.look_at));
  eye.updateMatrixWorld();
  eye.updateProjectionMatrix();
  return eye;
}

const camera = cameraAt(STATION_INDEX);

const rects: ScreenRect[] = buildScreenRects(planogram, camera, VIEWPORT_W, VIEWPORT_H);

function pixelsWith(
  eye: THREE.PerspectiveCamera,
  point: { x: number; y: number; z: number },
): { x: number; y: number } {
  const ndc = new THREE.Vector3(point.x, point.y, point.z).project(eye);
  return {
    x: (ndc.x * 0.5 + 0.5) * VIEWPORT_W,
    y: (-ndc.y * 0.5 + 0.5) * VIEWPORT_H,
  };
}

function toPixels(point: { x: number; y: number; z: number }): { x: number; y: number } {
  return pixelsWith(camera, point);
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
  for (const bay of planogram.bays) {
    const ad = bay.ad_slots.find((candidate) => candidate.ad_slot_id === adSlotId);
    if (ad) return ad;
  }
  throw new Error(`fixture is missing ad slot ${adSlotId}`);
}

function bayIndexOfAdSlot(adSlotId: string): number {
  const index = planogram.bays.findIndex((bay) =>
    bay.ad_slots.some((candidate) => candidate.ad_slot_id === adSlotId),
  );
  if (index < 0) throw new Error(`fixture is missing ad slot ${adSlotId}`);
  return index;
}

/** The centroid of an ad slot, seen from the station that ad's own bay owns. */
function adSlotFixation(adSlotId: string): { at: { x: number; y: number }; rects: ScreenRect[] } {
  const bayIndex = bayIndexOfAdSlot(adSlotId);
  const eye = cameraAt(bayIndex);
  return {
    at: pixelsWith(eye, adSlotCenter(planogram, bayIndex, adSlotOf(adSlotId))),
    rects: buildScreenRects(planogram, eye, VIEWPORT_W, VIEWPORT_H),
  };
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
/** The endcap analytics/tests/test_lift.py splits its real panel on. */
const ENDCAP = "B3_ENDCAP";

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

  it("names the ad slot a fixation landed on", () => {
    // This assertion used to read `slot_id === null`, which is exactly the
    // defect: `analytics/lift.py:split_panel` decides who was exposed to a
    // creative by matching its ad slot ids against `payload["slot_id"]` on a
    // `fixation`, `hover` or `pickup`. `hover` and `pickup` fire on product
    // slots only, so the fixation is the ONLY event that can ever carry real ad
    // exposure - and dropping the id here made S18's Ad-to-Purchase Lift, the
    // project's headline metric, permanently unable to report a `real` column.
    const at = toPixels(adSlotCenter(planogram, STATION_INDEX, adSlotOf(AD_SLOT)));
    const payload = fixationPayload(fixationAt(at), rects);

    expect(rects.some((rect) => rect.ad_slot_id === AD_SLOT)).toBe(true);
    expect(payload.slot_id).toBe(AD_SLOT);
    expect(payload.ad_slot_id).toBe(AD_SLOT);
  });

  it("makes a fixation on B3_ENDCAP visible to split_panel's exposure rule", () => {
    // B3_ENDCAP is the endcap `analytics/tests/test_lift.py` splits its panel
    // on. Seen from its own bay's station, a centroid on it must produce a
    // payload that `split_panel(sessions, ad_slot_ids=["B3_ENDCAP"])` counts as
    // exposed - i.e. `payload["slot_id"] in {"B3_ENDCAP"}`.
    const { at, rects: endcapRects } = adSlotFixation(ENDCAP);
    const payload = fixationPayload(fixationAt(at), endcapRects);

    expect(payload.slot_id).toBe(ENDCAP);
    expect(payload.ad_slot_id).toBe(ENDCAP);

    // The exposure predicate, transcribed from analytics/lift.py:288.
    const exposureSlots = new Set([ENDCAP]);
    expect(exposureSlots.has(payload.slot_id as string)).toBe(true);
  });

  it("emits exactly the ad-hit payload shape, and no shelf", () => {
    // shelf_id stays null for an ad. SlotMapper hangs an ad rect off the bay
    // and not off a shelf (`shelf_id: null` in collectTargets), so there is no
    // shelf the gaze demonstrably fell inside; naming one would take a second,
    // different hit test. Keeping it null also keeps "shelf_id names a shelf
    // band the gaze actually landed in" true, so shelf-level attention is not
    // polluted by ad looks - and it matches the fixture the committed
    // split_panel test uses (`shelf_id=None` on an exposure fixation).
    const { at, rects: endcapRects } = adSlotFixation(ENDCAP);
    const payload = fixationPayload(fixationAt(at), endcapRects);

    expect(Object.keys(payload).sort()).toEqual([
      "ad_slot_id",
      "dur_ms",
      "shelf_id",
      "slot_id",
      "x",
      "y",
    ]);
    expect(payload).toEqual({
      x: at.x,
      y: at.y,
      dur_ms: 240,
      slot_id: ENDCAP,
      shelf_id: null,
      ad_slot_id: ENDCAP,
    });
  });

  it("leaves a product hit byte-for-byte unchanged - no ad_slot_id key at all", () => {
    // The additive change must not leak into the product case: this is the
    // payload analytics/fusion.py sums per slot, and split_panel must never see
    // a product slot in the ad set by accident.
    const at = slotPixels("B1S3", OCCUPIED_SLOT);
    const payload = fixationPayload(fixationAt(at), rects);

    expect(payload).toEqual({
      x: at.x,
      y: at.y,
      dur_ms: 240,
      slot_id: OCCUPIED_SLOT,
      shelf_id: "B1S3",
    });
    expect("ad_slot_id" in payload).toBe(false);
  });

  it("still reports nothing at all, and no ad_slot_id, on a total miss", () => {
    const payload = fixationPayload(fixationAt({ x: -500, y: -500 }), rects);

    expect(payload).toEqual({
      x: -500,
      y: -500,
      dur_ms: 240,
      slot_id: null,
      shelf_id: null,
    });
    expect("ad_slot_id" in payload).toBe(false);
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
