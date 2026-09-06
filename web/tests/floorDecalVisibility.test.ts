import { readFileSync, readdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import * as THREE from "three";
import demoAisleJson from "../../data/planograms/demo_aisle.json";
import type { AdSlot, Bay, Planogram } from "@/contracts/planogram.schema";
import {
  CAMERA_FAR,
  CAMERA_FOV,
  CAMERA_NEAR,
  adSlotCenter,
  adSlotSize,
  isFlatAd,
  quadCorners,
} from "@/store/geometry";
import { buildScreenRects, hitTest } from "@/store/SlotMapper";

/**
 * `B2_DECAL` is below the bottom of the frame, from every station, always.
 *
 * The camera is fixed per bay and looks slightly down: `[bx, 1.5, 2.2]` at
 * `[bx, 1.1, 0]`, 50 degrees of vertical field of view. The floor at the bay
 * front is already off the bottom of that frame - `geometry.ts`'s aisle-display
 * note records it projecting to ndc y = -1.07, and that is why the water bottle
 * stands on a plinth rather than on the ground. A floor decal lies flatter and
 * nearer still, so all four of its corners land between ndc -1.06 and -1.28: it
 * is off screen by a fifth of the frame, at every aspect ratio, because a
 * vertical field of view does not change with one.
 *
 * Right now that costs nothing, and this file is here to make sure it stays
 * that way rather than to fix it. Nothing can fix it inside `web/`: the decal
 * is off screen because of where `bay.station.camera_pos` is, and the stations
 * are in `data/planograms/`, where a change would move every number in
 * RESULTS.md.
 *
 * Why it costs nothing today, and what it would cost tomorrow
 * -----------------------------------------------------------
 * `sim/saliency.py` only makes an ad slot a fixation target when it carries a
 * creative: "an ad slot with no creative shows nothing, so nobody looks at it".
 * `B2_DECAL` is `null` in the base planogram and set `null` again by variant D,
 * and no variant books anything on it - so the synthetic panel scores it at
 * nothing, the real panel can never look at it, and the two agree on zero for
 * opposite reasons.
 *
 * Book a creative on it and they stop agreeing. Saliency would score a
 * `floor_decal` at `AD_SLOT_RAW["floor_decal"]` = 0.3 and hand the persona
 * panel fixations on a fixture no human being in this study can see, while the
 * real panel returns zero - and the gap would be read as the synthetic panel
 * being wrong rather than as the store being unable to show the thing. That is
 * a silent, plausible-looking discrepancy in the one number the project exists
 * to report, so the last test here fails the build the day somebody books it.
 */

const HERE = dirname(fileURLToPath(import.meta.url));
const VARIANTS = resolve(HERE, "..", "..", "data", "variants");

const planogram = demoAisleJson as unknown as Planogram;

const VIEWPORTS: Array<[number, number]> = [
  [1280, 800],
  [1280, 720],
  [1024, 768],
  [900, 900],
];

function stationCamera(bay: Bay, w: number, h: number): THREE.PerspectiveCamera {
  const camera = new THREE.PerspectiveCamera(CAMERA_FOV, w / h, CAMERA_NEAR, CAMERA_FAR);
  camera.position.set(...bay.station.camera_pos);
  camera.lookAt(new THREE.Vector3(...bay.station.look_at));
  camera.updateMatrixWorld();
  camera.updateProjectionMatrix();
  return camera;
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

const B2 = bayIndexOf("B2");
const decal = adSlotOf(planogram.bays[B2], "B2_DECAL");

describe("the floor decal nobody can see", () => {
  it("is a flat fixture lying on the ground, which is what puts it there", () => {
    expect(decal.type).toBe("floor_decal");
    expect(isFlatAd(decal)).toBe(true);
  });

  it("projects entirely below the bottom of the frame, at every station and shape", () => {
    const corners = quadCorners(
      adSlotCenter(planogram, B2, decal),
      adSlotSize(planogram, B2, decal),
      true,
    );

    for (const [w, h] of VIEWPORTS) {
      for (const station of planogram.bays) {
        const camera = stationCamera(station, w, h);
        for (const corner of corners) {
          const ndc = new THREE.Vector3(corner.x, corner.y, corner.z).project(camera);
          expect(ndc.y, `${station.bay_id} at ${w}x${h}`).toBeLessThan(-1);
        }
      }
    }
  });

  it("can never be hit exactly, because its rectangle is not in the window", () => {
    // `buildScreenRects` projects every ad slot whether or not it is on screen,
    // so the decal does have a rectangle - it just sits below the window
    // entirely. With padding off, a 41 x 26 sweep of the whole viewport,
    // corners and edges included, never lands on it.
    for (const [w, h] of VIEWPORTS) {
      for (const station of planogram.bays) {
        const camera = stationCamera(station, w, h);
        const rects = buildScreenRects(planogram, camera, w, h);
        expect(rects.some((rect) => rect.ad_slot_id === "B2_DECAL")).toBe(true);

        for (let x = 0; x <= w; x += w / 40) {
          for (let y = 0; y <= h; y += h / 25) {
            const hit = hitTest(rects, x, y, 0);
            expect(hit?.ad_slot_id, `${station.bay_id} at ${x},${y}`).not.toBe("B2_DECAL");
          }
        }
      }
    }
  });

  it("is reachable only through the pad, in the last few pixels of the bottom edge", () => {
    // Reported, not fixed - and measured so the exposure is a number rather
    // than a worry.
    //
    // `hitTest`'s second pass widens every rectangle by `padPx` (25 by default)
    // so a near-miss on a fixture still resolves to it. The decal's rectangle
    // starts 22 px below an 800 px window, which the pad reaches: a cursor or
    // a gaze sample in the bottom ~3 px of the window - 5.2 px at 1280x720,
    // 0.2 px at 900x900 - can be attributed to a fixture that is not on screen.
    //
    // The clean fix is for `buildScreenRects` to drop a rectangle that falls
    // entirely outside the viewport, since padding is there to forgive a near
    // miss on something visible and not to reach something that is not there.
    // That is deliberately *not* done here: `slotmapper.test.ts` checks every
    // slot at every station by projecting slot centres that are far outside the
    // window at two stations out of three, so culling off-screen rectangles
    // needs that file's expectations rebuilt in the same change. Handed over
    // rather than half-done.
    //
    // The bound below is what makes this a test and not a comment: it fails if
    // the exposure ever grows past 6 px, whether from a bigger pad, a lower
    // camera or a deeper decal.
    const MAX_EXPOSED_PX = 6;

    for (const [w, h] of VIEWPORTS) {
      for (const station of planogram.bays) {
        const camera = stationCamera(station, w, h);
        const rects = buildScreenRects(planogram, camera, w, h);
        const rect = rects.find((candidate) => candidate.ad_slot_id === "B2_DECAL");
        if (!rect) throw new Error("the decal has no screen rect");

        // Off the bottom of the window, and by more than the pad less 6 px.
        expect(rect.y, `${station.bay_id} at ${w}x${h}`).toBeGreaterThan(h);
        expect(h - rect.y + 25, `${station.bay_id} at ${w}x${h}`).toBeLessThan(
          MAX_EXPOSED_PX,
        );

        // Everything above that strip is clean, with the default pad in force.
        for (let x = 0; x <= w; x += w / 40) {
          for (let y = 0; y <= h - MAX_EXPOSED_PX; y += (h - MAX_EXPOSED_PX) / 25) {
            const hit = hitTest(rects, x, y);
            expect(hit?.ad_slot_id, `${station.bay_id} at ${x},${y}`).not.toBe("B2_DECAL");
          }
        }
      }
    }
  });

  it("carries no creative in the base planogram, and no variant books one on it", () => {
    // The guard. If this fails, somebody has just booked an ad onto a fixture
    // that is off the bottom of the screen at every camera station in the
    // study: `sim/saliency.py` will score it at 0.3 and the real panel will
    // return zero, and the difference will look like the persona panel being
    // wrong. Either move the station cameras (which moves every number in
    // RESULTS.md and needs a fresh set of prediction locks) or book the
    // creative onto `B1_TALKER` or `B3_ENDCAP`, which are both on screen.
    expect(decal.creative_id).toBeNull();

    for (const name of readdirSync(VARIANTS).filter((file) => file.endsWith(".json"))) {
      const variant = JSON.parse(readFileSync(join(VARIANTS, name), "utf8")) as {
        patches?: Array<Record<string, unknown>>;
      };
      for (const patch of variant.patches ?? []) {
        if (patch.op !== "set_ad_creative") continue;
        if (patch.ad_slot_id !== "B2_DECAL") continue;
        expect(patch.creative_id, `${name} books a creative on B2_DECAL`).toBeNull();
      }
    }
  });
});
