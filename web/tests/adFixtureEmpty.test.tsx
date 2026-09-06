import { Children, isValidElement } from "react";
import type { ReactElement, ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import demoAisleJson from "../../data/planograms/demo_aisle.json";
import type { AdSlot as AdSlotData, Creative, Planogram } from "@/contracts/planogram.schema";
import { AdSlot } from "@/store/AdSlot";
import {
  adSlotCenter,
  adSlotSize,
  emptyAdFixtureParts,
  isFlatAd,
  type Size2,
} from "@/store/geometry";
import { CARCASS_COLOR, EMPTY_SPACE_COLOR } from "@/store/palette";

/**
 * An unbooked ad slot is an empty fixture, and it has to look like one.
 *
 * Variant A - the arm the demo opens on - books `AD_1` on `B3_ENDCAP` and
 * nothing else, so `B1_TALKER` and `B2_DECAL` carry no creative. That is the
 * experiment working exactly as designed: `data/variants/` defines the study,
 * `D.json` is a control arm with no creative anywhere, and booking more ads to
 * make the store look finished would move every number in RESULTS.md. The
 * fixtures are supposed to be empty.
 *
 * What was wrong was how empty read. A blank light-grey plane is what a
 * renderer produces when a texture failed to load, so two of the three
 * fixtures looked like a bug in the demo rather than a condition of the
 * experiment.
 *
 * The treatment: an unbooked fixture is drawn as an empty poster holder - a
 * recessed panel in the same dark slate `Bay.tsx` already uses for a
 * deliberately empty shelf position, framed by a lip in the carcass grey. The
 * store therefore teaches one rule and not two: dark slate is space nothing is
 * booked into, whether that is a shelf position or an ad fixture. It carries no
 * imagery of any kind, so it cannot be mistaken for a creative.
 *
 * Two properties matter more than the taste, and both are pinned below:
 *
 *  1. **Footprint parity.** The empty fixture occupies exactly the rectangle
 *     the booked one would. `geometry.ts` mounts every ad proud of the shelf
 *     lip, so a fixture occludes whatever is behind it - at station B3 that is
 *     the bottom 45% of the top-shelf packs. If an empty fixture occluded less
 *     than a booked one, then booking `AD_1` would change how much merchandise
 *     is visible, and A-vs-C-vs-D would be comparing more than the creative.
 *  2. **It stays quiet.** `sim/saliency.py` skips a creative-less ad slot
 *     outright - "an ad slot with no creative shows nothing, so nobody looks at
 *     it" - so the synthetic panel scores it at zero. A loud empty fixture
 *     would pull real gaze the synthetic panel has no way to predict. It is
 *     built from colours already all over the scene, and adds no new contrast.
 */

// `AdSlot` is called directly here, as the plain function it is, and the tree
// it returns is inspected rather than mounted - so `CreativePlane` is only ever
// an unrendered element and its `useTexture` is never reached. drei is stubbed
// all the same, as it is in every other file here: loaded for real it pulls in
// a second copy of three.js and warns about it on every run.
vi.mock("@react-three/drei", () => ({
  useTexture: () => ({}),
}));

const planogram = demoAisleJson as unknown as Planogram;

const adSlots: Array<{ bayIndex: number; ad: AdSlotData }> = planogram.bays.flatMap(
  (bay, bayIndex) => bay.ad_slots.map((ad) => ({ bayIndex, ad })),
);

const creative: Creative = {
  creative_id: "AD_1",
  brand: "TestBrand",
  texture_url: "/textures/ads/AD_1.png",
};

function adSlotNamed(adSlotId: string): { bayIndex: number; ad: AdSlotData } {
  const found = adSlots.find((entry) => entry.ad.ad_slot_id === adSlotId);
  if (!found) throw new Error(`fixture is missing ad slot ${adSlotId}`);
  return found;
}

/** Every React element in a returned tree, parents before children. */
function elements(node: ReactNode): ReactElement[] {
  const out: ReactElement[] = [];
  Children.forEach(node, (child) => {
    if (!isValidElement(child)) return;
    out.push(child);
    out.push(...elements((child.props as { children?: ReactNode }).children));
  });
  return out;
}

function tagged(tree: ReactNode, tag: string): ReactElement[] {
  return elements(tree).filter((element) => element.type === tag);
}

function colorsOf(tree: ReactNode): string[] {
  return tagged(tree, "meshStandardMaterial").map(
    (element) => (element.props as { color?: string }).color ?? "",
  );
}

function render(ad: AdSlotData, bayIndex: number, booked: Creative | null): ReactNode {
  return AdSlot({
    ad,
    creative: booked,
    center: adSlotCenter(planogram, bayIndex, ad),
    size: adSlotSize(planogram, bayIndex, ad),
    flat: isFlatAd(ad),
  });
}

describe("emptyAdFixtureParts", () => {
  it("keeps the exact footprint of the booked fixture, for every ad slot in the aisle", () => {
    for (const { bayIndex, ad } of adSlots) {
      const size = adSlotSize(planogram, bayIndex, ad);
      const parts = emptyAdFixtureParts(size);
      expect(parts.panel.w, ad.ad_slot_id).toBe(size.w);
      expect(parts.panel.h, ad.ad_slot_id).toBe(size.h);
      expect(parts.panel.x, ad.ad_slot_id).toBe(0);
      expect(parts.panel.y, ad.ad_slot_id).toBe(0);
    }
  });

  it("frames the panel with a lip that lies wholly inside the footprint", () => {
    for (const { bayIndex, ad } of adSlots) {
      const size = adSlotSize(planogram, bayIndex, ad);
      const parts = emptyAdFixtureParts(size);
      expect(parts.lip, ad.ad_slot_id).toHaveLength(4);

      for (const bar of parts.lip) {
        expect(Math.abs(bar.x) + bar.w / 2, ad.ad_slot_id).toBeLessThanOrEqual(
          size.w / 2 + 1e-12,
        );
        expect(Math.abs(bar.y) + bar.h / 2, ad.ad_slot_id).toBeLessThanOrEqual(
          size.h / 2 + 1e-12,
        );
      }
    }
  });

  it("tiles the border exactly: the four bars neither overlap nor leave a gap", () => {
    // Four bars covering a border of width `lip` have area
    // `w*h - (w-2*lip)*(h-2*lip)`. Equality both ways is what proves they meet
    // at the corners without doubling up on them.
    for (const { bayIndex, ad } of adSlots) {
      const size = adSlotSize(planogram, bayIndex, ad);
      const parts = emptyAdFixtureParts(size);
      const lip = parts.lip[0].h;
      const area = parts.lip.reduce((sum, bar) => sum + bar.w * bar.h, 0);
      expect(area, ad.ad_slot_id).toBeCloseTo(
        size.w * size.h - (size.w - 2 * lip) * (size.h - 2 * lip),
        12,
      );
    }
  });

  it("never lets the lip swallow the fixture, however small the fixture is", () => {
    // The shelf talker is only 10 cm tall. A lip sized as a flat fraction of
    // the larger dimension would close over it entirely and turn an empty
    // fixture into a solid bar, which is the blank-panel bug in a new colour.
    const sizes: Size2[] = [
      ...adSlots.map(({ bayIndex, ad }) => adSlotSize(planogram, bayIndex, ad)),
      { w: 0.04, h: 0.02 },
    ];
    for (const size of sizes) {
      const parts = emptyAdFixtureParts(size);
      const lip = parts.lip[0].h;
      expect(size.w - 2 * lip).toBeGreaterThan(0);
      expect(size.h - 2 * lip).toBeGreaterThan(0);
    }
  });
});

describe("AdSlot", () => {
  it("draws an unbooked fixture as a framed empty panel, not a blank plane", () => {
    const { bayIndex, ad } = adSlotNamed("B1_TALKER");
    const tree = render(ad, bayIndex, null);

    // One back panel plus four lip bars: a frame with nothing in it.
    expect(tagged(tree, "mesh")).toHaveLength(5);
    const colors = colorsOf(tree);
    expect(colors.filter((color) => color === EMPTY_SPACE_COLOR)).toHaveLength(1);
    expect(colors.filter((color) => color === CARCASS_COLOR)).toHaveLength(4);
  });

  it("gives the empty fixture the same silhouette the creative would have had", () => {
    // Footprint parity again, this time as rendered: whatever is behind an ad
    // fixture is hidden by exactly as much of it whether or not `AD_1` is on it.
    for (const { bayIndex, ad } of adSlots) {
      const size = adSlotSize(planogram, bayIndex, ad);
      const empty = render(ad, bayIndex, null);
      const panel = tagged(empty, "planeGeometry")[0];
      expect((panel.props as { args: [number, number] }).args, ad.ad_slot_id).toEqual([
        size.w,
        size.h,
      ]);
    }
  });

  it("puts no texture and no image on an empty fixture", () => {
    for (const { bayIndex, ad } of adSlots) {
      const tree = render(ad, bayIndex, null);
      for (const element of elements(tree)) {
        expect(typeof element.type, ad.ad_slot_id).toBe("string");
        expect((element.props as { map?: unknown }).map, ad.ad_slot_id).toBeUndefined();
      }
    }
  });

  it("draws a booked fixture as the creative alone, at that same size", () => {
    const { bayIndex, ad } = adSlotNamed("B3_ENDCAP");
    const size = adSlotSize(planogram, bayIndex, ad);
    const tree = render(ad, bayIndex, creative);

    expect(tagged(tree, "mesh")).toHaveLength(1);
    expect(colorsOf(tree)).toEqual([]);

    const plane = elements(tree).find((element) => typeof element.type === "function");
    expect((plane?.props as { url: string; size: Size2 }).url).toBe(creative.texture_url);
    expect((plane?.props as { url: string; size: Size2 }).size).toEqual(size);
  });
});
