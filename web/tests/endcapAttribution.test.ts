import { describe, expect, it } from "vitest";
import * as THREE from "three";
import demoAisleJson from "../../data/planograms/demo_aisle.json";
import type { AdSlot, Bay, Planogram, Shelf } from "@/contracts/planogram.schema";
import {
  AD_HEADER_HEIGHT_M,
  CAMERA_FAR,
  CAMERA_FOV,
  CAMERA_NEAR,
  adSlotCenter,
  adSlotSize,
  bayCenterX,
  slotCenter,
} from "@/store/geometry";
import { buildScreenRects, hitTest, type ScreenRect } from "@/store/SlotMapper";

/**
 * What the shopper sees is what has to be attributed to them.
 *
 * `geometry.ts` hangs an endcap header across the top of the bay carcass, at
 * `AD_Z` - 5 cm proud of the shelf lip, so the fixture never z-fights the
 * boards. `data/planograms/demo_aisle.json` then puts 0.22 m packs on a top
 * shelf at 1.70 m, so the top-shelf facings run 1.70-1.92 m while the header
 * runs 1.60-1.80 m. The two overlap through 10 cm of world space, and because
 * the header is the nearer of the two, the renderer draws it *over* the bottom
 * 45% of `B3S1P1` and `B3S1P2`. That is what a shopper sees at station B3: an
 * ad panel, with two half-hidden packs above it.
 *
 * `SlotMapper.hitTest` disagreed. Its precedence list was `slot` before
 * `ad_slot` before `shelf`, applied before depth ever came into it, so every
 * pixel of that overlap - a 184 x 50 px band under each top-shelf pack, at a
 * 1280 x 800 viewport - was attributed to the *pack behind the panel*. A
 * shopper looking straight at the booked creative was recorded as looking at
 * SKU_017 or SKU_018.
 *
 * Neither rule is wrong on its own. "A header runs across the top of the bay"
 * is right, and "an exact hit on a product beats an ad" is right, and nobody
 * had ever checked one against the other. This file is that check.
 *
 * Why the existing centre probe in `slotmapper.test.ts` never caught it: the
 * header is centred on the bay, the camera is centred on the bay, and
 * `B3S1P2` starts at exactly the bay's centre line - so the header's own
 * centre projects to x = 640.00 px, which is `B3S1P2`'s left edge to the
 * pixel, and `EDGE_EPS_PX` drops an edge-grazing hit. The one point the old
 * test probed was the one point in the whole overlap where no slot claimed the
 * header's pixels. Everything here is probed off-centre for that reason.
 *
 * This changes what is measured, deliberately. It is safe to do now and it
 * will not be later: `predictions/` holds nothing but `.gitkeep` and
 * `data/sessions/anon/` is empty, so no committed prediction lock and no real
 * session was ever scored under the old attribution.
 */

const planogram = demoAisleJson as unknown as Planogram;

const VIEWPORT_W = 1280;
const VIEWPORT_H = 800;

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

function bayIndexOf(bayId: string): number {
  const index = planogram.bays.findIndex((bay) => bay.bay_id === bayId);
  if (index === -1) throw new Error(`fixture is missing bay ${bayId}`);
  return index;
}

function adSlotOf(bay: Bay, adSlotId: string): AdSlot {
  const ad = bay.ad_slots.find((candidate) => candidate.ad_slot_id === adSlotId);
  if (!ad) throw new Error(`fixture is missing ad slot ${adSlotId}`);
  return ad;
}

function shelfOf(bay: Bay, shelfId: string): Shelf {
  const shelf = bay.shelves.find((candidate) => candidate.shelf_id === shelfId);
  if (!shelf) throw new Error(`fixture is missing shelf ${shelfId}`);
  return shelf;
}

function rectFor(rects: ScreenRect[], match: (rect: ScreenRect) => boolean): ScreenRect {
  const found = rects.find(match);
  if (!found) throw new Error("no screen rect matched");
  return found;
}

function contains(rect: ScreenRect, point: { x: number; y: number }): boolean {
  return (
    point.x > rect.x &&
    point.x < rect.x + rect.w &&
    point.y > rect.y &&
    point.y < rect.y + rect.h
  );
}

const B3 = bayIndexOf("B3");
const endcap = adSlotOf(planogram.bays[B3], "B3_ENDCAP");
const endcapCenter = adSlotCenter(planogram, B3, endcap);
const endcapSize = adSlotSize(planogram, B3, endcap);
const topShelf = shelfOf(planogram.bays[B3], "B3S1");

/**
 * The centre of one quadrant of the header - well off its own centre line, and
 * squarely inside the band where it is drawn over a top-shelf pack.
 */
function headerQuadrant(sign: -1 | 1): { x: number; y: number; z: number } {
  return {
    x: endcapCenter.x + (sign * endcapSize.w) / 4,
    y: endcapCenter.y + endcapSize.h / 4,
    z: endcapCenter.z,
  };
}

describe("the endcap header the shopper can actually see", () => {
  it("is drawn in front of the top-shelf facings, and its quadrants land inside them", () => {
    // The premise of every assertion below. If a later change moves the header
    // clear of the facings this fails first, and says so, rather than letting
    // the rest of the file pass while probing nothing.
    const camera = stationCamera(planogram.bays[B3]);
    const rects = buildScreenRects(planogram, camera, VIEWPORT_W, VIEWPORT_H);

    const adRect = rectFor(rects, (rect) => rect.ad_slot_id === "B3_ENDCAP");
    const leftRect = rectFor(rects, (rect) => rect.slot_id === "B3S1P1");
    const rightRect = rectFor(rects, (rect) => rect.slot_id === "B3S1P2");

    // Nearer to the camera than either pack: this is why the renderer puts the
    // panel on top, and therefore why the panel is what the shopper sees.
    expect(adRect.depth).toBeLessThan(leftRect.depth);
    expect(adRect.depth).toBeLessThan(rightRect.depth);

    const left = toPixels(headerQuadrant(-1), camera);
    const right = toPixels(headerQuadrant(1), camera);

    expect(contains(adRect, left)).toBe(true);
    expect(contains(leftRect, left)).toBe(true);
    expect(contains(adRect, right)).toBe(true);
    expect(contains(rightRect, right)).toBe(true);
  });

  it("wins the pixels it covers, off-centre, on both sides of the bay", () => {
    const camera = stationCamera(planogram.bays[B3]);
    const rects = buildScreenRects(planogram, camera, VIEWPORT_W, VIEWPORT_H);

    for (const sign of [-1, 1] as const) {
      const probe = toPixels(headerQuadrant(sign), camera);
      const hit = hitTest(rects, probe.x, probe.y);
      expect(hit?.ad_slot_id, `header quadrant ${sign}`).toBe("B3_ENDCAP");
      expect(hit?.slot_id, `header quadrant ${sign}`).toBeUndefined();
    }
  });

  it("wins those pixels in every station's rectangles, not just bay 3's", () => {
    // `buildScreenRects` projects the whole planogram from whichever station
    // the camera is parked at, so the same overlap is in all three sets of
    // rectangles and the fix has to hold in all three. Only bay 3's probe is
    // inside the window - at the other two stations the endcap sits off the
    // right-hand edge, x 1813-2204 px in a 1280 px window from bay 1 - so this
    // is a check on the rule, not on anything a shopper there could do. What a
    // shopper *can* do at those stations is covered by the sweeps in
    // `floorDecalVisibility.test.ts`.
    for (const station of planogram.bays) {
      const camera = stationCamera(station);
      const rects = buildScreenRects(planogram, camera, VIEWPORT_W, VIEWPORT_H);
      const probe = toPixels(headerQuadrant(1), camera);
      const hit = hitTest(rects, probe.x, probe.y);
      expect(hit?.ad_slot_id, `station ${station.bay_id}`).toBe("B3_ENDCAP");
    }
  });

  it("takes nothing above its own top edge: uncovered merchandise still wins", () => {
    // The other half of the rule. The header hides the bottom 45% of the
    // top-shelf packs and not one millimetre more, so the part of a pack that
    // is genuinely on screen must still be attributed to the pack. Probed at
    // 1.87 m, above the header's 1.80 m top edge and below the packs' 1.92 m.
    const camera = stationCamera(planogram.bays[B3]);
    const rects = buildScreenRects(planogram, camera, VIEWPORT_W, VIEWPORT_H);

    for (const slot of topShelf.slots) {
      if (slot.sku_id === null) continue;
      const center = slotCenter(planogram, B3, topShelf, slot);
      const probe = toPixels({ x: center.x, y: 1.87, z: center.z }, camera);
      const hit = hitTest(rects, probe.x, probe.y);
      expect(hit?.slot_id, slot.slot_id).toBe(slot.slot_id);
      expect(hit?.ad_slot_id, slot.slot_id).toBeUndefined();
    }
  });
});

/** The visual top of a shelf: its board plus the tallest pack standing on it. */
function facingsTop(shelf: Shelf): number {
  return shelf.height_m + Math.max(...shelf.slots.map((slot) => slot.height_m));
}

function overlap(aLo: number, aHi: number, bLo: number, bHi: number): number {
  return Math.max(0, Math.min(aHi, bHi) - Math.max(aLo, bLo));
}

describe("why this is fixed in the mapper and not by moving the header", () => {
  it("has nowhere to move it to: the frame is 9 cm and the fixture is 20", () => {
    // The obvious repair - hang the header above the merchandise instead of
    // across it - does not fit. The station camera's vertical field of view is
    // fixed at 50 degrees, so a wider window widens the frame and never raises
    // it: the top of frame at the fixture's own depth is the same world height
    // at every aspect ratio, and it is 9.1 cm above the top of the packs. A
    // header hung clear of them has its own top edge off the top of the screen,
    // where half a booked creative would be unreadable while `sim/saliency.py`
    // went on scoring an `endcap_header` at 0.6.
    const camera = stationCamera(planogram.bays[B3]);
    const packsTop = facingsTop(topShelf);
    const clearAbove = toPixels(
      { x: bayCenterX(planogram, B3), y: packsTop + AD_HEADER_HEIGHT_M, z: endcapCenter.z },
      camera,
    );
    expect(clearAbove.y).toBeLessThan(0);

    // Nor is there anywhere below. Dropping the header until its top edge meets
    // the underside of the top-shelf packs puts it straight through the packs on
    // the shelf under that one, and covers more of them than it covers now.
    const secondShelf = shelfOf(planogram.bays[B3], "B3S2");
    const covered = overlap(
      endcapCenter.y - endcapSize.h / 2,
      endcapCenter.y + endcapSize.h / 2,
      topShelf.height_m,
      packsTop,
    );
    const coveredIfLowered = overlap(
      topShelf.height_m - AD_HEADER_HEIGHT_M,
      topShelf.height_m,
      secondShelf.height_m,
      facingsTop(secondShelf),
    );
    expect(covered).toBeGreaterThan(0);
    expect(coveredIfLowered).toBeGreaterThan(covered);
  });
});

describe("the shelf talker, which overlaps nothing", () => {
  it("still beats the shelf band it hangs inside", () => {
    // `B1_TALKER` hangs 6 cm under the `B1S3` board, in the clear air above the
    // `B1S4` packs - it covers no facing at any station. Its own centre must
    // therefore still resolve to the ad and not to the enclosing shelf band,
    // which is the case the kind precedence was written for in the first place
    // and the case that must survive teaching it about depth.
    const B1 = bayIndexOf("B1");
    const talker = adSlotOf(planogram.bays[B1], "B1_TALKER");
    const camera = stationCamera(planogram.bays[B1]);
    const rects = buildScreenRects(planogram, camera, VIEWPORT_W, VIEWPORT_H);

    const probe = toPixels(adSlotCenter(planogram, B1, talker), camera);
    const hit = hitTest(rects, probe.x, probe.y);
    expect(hit?.ad_slot_id).toBe("B1_TALKER");

    // ...and the band it sits in is a real rect that would otherwise claim it.
    const band = rectFor(rects, (rect) => rect.kind === "shelf" && rect.shelf_id === "B1S4");
    expect(contains(band, probe)).toBe(true);
  });
});
