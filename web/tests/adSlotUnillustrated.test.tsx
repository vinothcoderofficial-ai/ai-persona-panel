import { Children, isValidElement } from "react";
import type { ReactElement, ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import type { AdSlot as AdSlotData, Creative } from "@/contracts/planogram.schema";
import { emptyAdFixtureParts } from "@/store/geometry";

/**
 * A creative with no artwork is booked, and has to look booked.
 *
 * `AdSlot` branched on `if (creative)` — the object, not its `texture_url` —
 * and handed the string straight to `useTexture`, which throws on an empty one
 * and unwinds past Suspense into the scene's error boundary. That was harmless
 * while every creative came from `data/planograms/demo_aisle.json`, where all
 * of them carry artwork. It stopped being harmless when `#/vision` learned to
 * declare one: a creative an operator names on a video-read shelf has
 * `texture_url: ""`, because no poster was filmed and the pipeline will not
 * invent one. So the first person to declare an ad and then walk into their own
 * store would have got the error panel.
 *
 * This is the same defect that was fixed in `ProductSlot` and missed here.
 *
 * **The treatment is not the empty-fixture treatment, and that distinction is
 * load-bearing.** `EMPTY_SPACE_COLOR` means "nothing is booked here" — it is
 * what `B1_TALKER` and `B2_DECAL` show under variant A, which is the
 * experiment working as designed. A booked creative with no artwork is the
 * opposite state: something *is* booked, and the shopper should be able to see
 * that something is there. Drawing it as an empty holder would make a booked
 * arm look like a control arm, which is the one confusion this store must not
 * introduce — `sim/saliency.py` scores a slot differently depending on whether
 * an adjacent fixture carries a creative, so the render and the model would be
 * telling different stories about the same bay.
 *
 * Footprint parity is pinned for the same reason it is pinned for the empty
 * fixture: every ad mounts proud of the shelf lip and occludes what is behind
 * it, so if an unillustrated creative occupied less than an illustrated one,
 * booking one would change how much merchandise is visible and the arms would
 * differ by more than the ad.
 */

const useTextureMock = vi.hoisted(() => vi.fn(() => ({})));
vi.mock("@react-three/drei", () => ({ useTexture: useTextureMock }));

const { AdSlot } = await import("@/store/AdSlot");

const SIZE = { w: 0.9, h: 0.28 };
const CENTER = { x: 0.1, y: 1.3, z: 0.05 };

function creative(textureUrl: string): Creative {
  return {
    creative_id: "V_AD_1",
    brand: "Crunch",
    texture_url: textureUrl,
  } as unknown as Creative;
}

/** The fixture `#/vision` installs: a shelf talker on a video-read bay. */
const AD: AdSlotData = {
  ad_slot_id: "V_TALKER_1",
  type: "shelf_talker",
  attached_to: "V1S3",
  x_m: 0.1,
  width_m: 0.4,
  creative_id: null,
} as unknown as AdSlotData;

function render(value: Creative | null): ReactNode {
  return AdSlot({ ad: AD, creative: value, center: CENTER, size: SIZE, flat: false });
}

function descendants(node: ReactNode): ReactElement[] {
  const found: ReactElement[] = [];
  const walk = (current: ReactNode): void => {
    Children.forEach(current, (child) => {
      if (!isValidElement(child)) return;
      found.push(child);
      walk((child.props as { children?: ReactNode }).children);
    });
  };
  walk(node);
  return found;
}

/** Every plane this subtree draws, as {w, h} pairs. */
function planes(node: ReactNode): Array<{ w: number; h: number }> {
  return descendants(node)
    .filter((element) => element.type === "planeGeometry")
    .map((element) => {
      const args = (element.props as { args: [number, number] }).args;
      return { w: args[0], h: args[1] };
    });
}

describe("a booked creative with no artwork", () => {
  it("never asks the loader for an empty url", () => {
    useTextureMock.mockClear();

    render(creative(""));

    expect(useTextureMock).not.toHaveBeenCalled();
  });

  it("still draws something, so a booked fixture does not read as unbooked", () => {
    const drawn = planes(render(creative("")));

    expect(drawn.length).toBeGreaterThan(0);
  });

  it("occupies exactly the footprint an illustrated creative would", () => {
    const unillustrated = planes(render(creative("")));

    const widest = Math.max(...unillustrated.map((plane) => plane.w));
    const tallest = Math.max(...unillustrated.map((plane) => plane.h));
    expect(widest).toBeCloseTo(SIZE.w, 6);
    expect(tallest).toBeCloseTo(SIZE.h, 6);
  });

  it("does not draw the empty holder's recessed frame", () => {
    // The empty fixture is a panel plus four lip pieces. A booked-but-plain
    // creative is one panel: if it drew the frame it would be saying
    // "nothing booked" in the vocabulary the rest of the store teaches.
    const lipCount = emptyAdFixtureParts(SIZE).lip.length;

    expect(planes(render(creative(""))).length).toBeLessThan(1 + lipCount);
  });

  it("leaves an illustrated creative on the textured path", () => {
    // The textured branch delegates to `CreativePlane`, which is an unrendered
    // element here - so this asserts the delegation and the url it carries,
    // not the geometry inside it. That is the point: the loader must be
    // reached only through a component that is created only when there is
    // something to load.
    const tree = render(creative("/textures/AD_1.png"));

    const delegated = descendants(tree).filter(
      (element) => typeof element.type === "function",
    );
    expect(delegated).toHaveLength(1);
    expect((delegated[0].props as { url: string }).url).toBe("/textures/AD_1.png");
    expect(planes(tree)).toEqual([]);
  });

  it("still draws an unbooked fixture as the empty holder", () => {
    const lipCount = emptyAdFixtureParts(SIZE).lip.length;

    expect(planes(render(null))).toHaveLength(1 + lipCount);
  });
});
