import { Children, isValidElement } from "react";
import type { ReactElement, ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import type { Sku, Slot } from "@/contracts/planogram.schema";

/**
 * A product with no texture has to draw as its own colour, not crash the scene.
 *
 * `vision/planogram.py` emits `texture_url: ""` for every SKU it reads off a
 * video, and it is right to: a classical pipeline measures a facing's position,
 * size and mean colour, and there is no pack shot anywhere in a frame it can
 * point a URL at. Inventing one would put a picture of some other product on a
 * shelf and every screenshot afterwards would be fiction.
 *
 * `ProductSlot` called `useTexture(sku.texture_url)` unconditionally. drei's
 * loader throws on an empty string, and because the call sits above the
 * component's own return there is nothing to catch it below - it unwinds past
 * Suspense into `PlanogramScene`'s error boundary and takes the whole store
 * down. So the first video-read shelf anyone tried to walk into would have
 * rendered as the scene's error panel, and the bug is not reachable today only
 * because the reading has no route into the scene yet.
 *
 * The fix has to respect a rule of hooks: `useTexture` cannot be called
 * conditionally, so the load moves into a child element that is only created
 * when there is something to load. That is what the first test pins - not that
 * the component survives an empty URL, but that it never asks for one.
 *
 * The fallback is `sku.color_lab`, which for a video-read SKU is the one thing
 * about it that *was* measured. A shelf of grey boxes would lose the colour
 * contrast that `sim/saliency.py` reads, and a viewer comparing the scene
 * against the heatmap would be looking at two different shelves.
 */

const useTextureMock = vi.hoisted(() => vi.fn(() => ({})));
vi.mock("@react-three/drei", () => ({ useTexture: useTextureMock }));

const { ProductSlot } = await import("@/store/ProductSlot");

const slot: Slot = {
  slot_id: "V1S1P1",
  sku_id: "V_001",
  facings: 2,
  x_m: 0.05,
  width_m: 0.4,
  height_m: 0.3,
  confidence: 0.82,
} as unknown as Slot;

function sku(textureUrl: string): Sku {
  return {
    sku_id: "V_001",
    name: "unidentified product 1",
    brand: "unknown",
    category: "unknown",
    price: 0,
    promo: false,
    texture_url: textureUrl,
    color_lab: [62.4, 31.2, -18.9],
  } as unknown as Sku;
}

function render(textureUrl: string): ReactNode {
  return ProductSlot({
    slot,
    sku: sku(textureUrl),
    center: { x: 0, y: 1.2, z: 0 },
    size: { w: 0.4, h: 0.3 },
    hovered: false,
    onEnter: () => {},
    onLeave: () => {},
    onSelect: () => {},
  });
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

describe("a product with no texture", () => {
  it("never asks the loader for an empty url", () => {
    useTextureMock.mockClear();

    render("");

    expect(useTextureMock).not.toHaveBeenCalled();
  });

  it("draws the colour the video measured instead", () => {
    const materials = descendants(render("")).filter(
      (element) => element.type === "meshStandardMaterial",
    );

    expect(materials.length).toBeGreaterThan(0);
    for (const material of materials) {
      const props = material.props as { map?: unknown; color?: unknown };
      expect(props.map).toBeUndefined();
      expect(typeof props.color).toBe("string");
    }
  });

  it("still draws one facing per facing the slot carries", () => {
    const geometries = descendants(render("")).filter(
      (element) => element.type === "planeGeometry",
    );

    // Two facing planes plus the one transparent hit plane.
    expect(geometries).toHaveLength(slot.facings + 1);
  });

  it("leaves a product that does have a texture alone", () => {
    useTextureMock.mockClear();

    const tree = render("/textures/SKU_001.png");

    expect(descendants(tree).length).toBeGreaterThan(0);
  });
});
