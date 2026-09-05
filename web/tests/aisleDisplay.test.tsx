import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import type { ReactElement, ReactNode } from "react";
import { createRoot } from "react-dom/client";
import demoAisleJson from "../../data/planograms/demo_aisle.json";
import type { Event as ShopperEvent } from "@/contracts/event.schema";
import type { Planogram } from "@/contracts/planogram.schema";
import type { EventSink } from "@/capture/SessionSocket";
import { PlanogramScene } from "@/store/PlanogramScene";
import {
  BAY_GAP_M,
  aisleDisplayPlacement,
  bayLeftX,
  slotCenter,
  slotSize,
} from "@/store/geometry";

/**
 * `data/models/WaterBottle.glb` is the CC0 Khronos sample asset the hackathon
 * portal's "sample 3D model from github or huggingface" row requires. It is
 * 8,966,700 bytes - roughly three times the whole rest of the store put
 * together - and it is decoration, not data. Three rules follow from that, and
 * this file is the only thing standing between them and a regression.
 *
 *  1. **It is not a product.** `data/planograms/demo_aisle.json` has 24 SKUs in
 *     real slots and three deliberately empty ones, `B1S3P2` at eye level
 *     chief among them: that empty slot is what makes the "move a SKU to eye
 *     level" recommendation legible in the demo, and the whole comparison
 *     between the synthetic and real panels is defined on the planogram
 *     document. A rendered bottle that reads as shelf stock - or worse, as the
 *     thing that belongs in the empty eye-level slot - would corrupt what a
 *     viewer thinks is being measured. So the prop stands on its own display
 *     plinth in the aisle gap between two gondolas, clear of every slot.
 *
 *  2. **It must not delay the shelves.** `sceneReadyOverlay.test.tsx` covers
 *     the "Loading shelves…" gate that keeps a click from vanishing into a
 *     blank canvas. Dropping an 8.6 MB model into that same `<Suspense>`
 *     boundary would hold the overlay up until the model arrived, roughly
 *     tripling the time the store spends behind it, for a decoration. The
 *     prop therefore suspends in its own boundary, and the shelves must be
 *     released the instant the pack textures resolve, model or no model.
 *
 *  3. **It must not be able to take the store down.** `sceneErrorBoundary.test.tsx`
 *     covers the texture failure, whose message names `make seed` because pack
 *     textures are generated and gitignored. The GLB is committed, so `make seed`
 *     is the wrong remedy for it and a full-screen "the shelves could not be
 *     loaded" is the wrong outcome: the store works perfectly without a
 *     decorative bottle. A failed model degrades to no bottle, silently for
 *     the shopper and loudly on the console.
 */

// See sceneReadyOverlay.test.tsx for why: the real Canvas needs a WebGL
// context jsdom does not have, and this file needs the children inside it to
// really mount so the real <Suspense> and error-boundary mechanics run.
vi.mock("@react-three/fiber", () => ({
  Canvas: ({ children }: { children?: ReactNode }) => <>{children}</>,
  useFrame: () => undefined,
  useThree: () => ({}),
}));

// Outside both boundaries this file tests; faked away for the same reason as
// in sceneReadyOverlay.test.tsx.
vi.mock("@/store/StationController", () => ({
  StationController: () => null,
}));

/**
 * Two independent gates, one per Suspense boundary, so a test can hold either
 * side back and watch the other proceed. Each `read()` throws a pending
 * promise while loading - exactly what `useTexture` and `useGLTF` do, both
 * being built on `suspend-react` - and `fail()` makes the retry throw a plain
 * `Error` instead, which `<Suspense>` does not catch and passes to the nearest
 * error boundary. `vi.hoisted` is required: `vi.mock` factories run before this
 * file's own top-level code.
 */
const gates = vi.hoisted(() => {
  function makeGate() {
    let state: "pending" | "ready" | "failed" = "pending";
    let settle: (() => void) | null = null;
    let failure: Error | null = null;
    return {
      read(): void {
        if (state === "ready") return;
        if (state === "failed") throw failure;
        throw new Promise<void>((res) => {
          settle = res;
        });
      },
      resolve(): void {
        state = "ready";
        const res = settle;
        settle = null;
        res?.();
      },
      fail(error: Error): void {
        state = "failed";
        failure = error;
        const res = settle;
        settle = null;
        res?.();
      },
      reset(): void {
        state = "pending";
        settle = null;
        failure = null;
      },
    };
  }
  return { shelves: makeGate(), model: makeGate() };
});

// Stands in for the whole Bay -> ProductSlot -> `useTexture` chain.
vi.mock("@/store/Bay", () => ({
  Bay: () => {
    gates.shelves.read();
    return null;
  },
}));

// Stands in for `useGLTF`, which AisleDisplay calls to load the GLB. Only the
// loader is faked: AisleDisplay's own Suspense boundary, error boundary and
// placement are the real ones under test.
vi.mock("@react-three/drei", () => ({
  useGLTF: () => {
    gates.model.read();
    return { scene: { isObject3D: true } };
  },
  useTexture: () => ({}),
}));

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

const planogram = demoAisleJson as unknown as Planogram;

/** Nothing in this file interacts with a product slot, so every call is a no-op. */
class NullSink implements EventSink {
  readonly sessionId = "sess-1";
  get events(): readonly ShopperEvent[] {
    return [];
  }
  log(): void {}
  flush(): Promise<void> {
    return Promise.resolve();
  }
}

function mount(ui: ReactElement): { container: HTMLDivElement; unmount: () => void } {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(ui);
  });
  return {
    container,
    unmount: () => {
      act(() => {
        root.unmount();
      });
      container.remove();
    },
  };
}

function mountScene(): { container: HTMLDivElement; unmount: () => void } {
  return mount(
    <PlanogramScene
      planogram={planogram}
      logger={new NullSink()}
      tracker={null}
      consent={true}
      mode="cursor_only"
    />,
  );
}

function loadingOverlay(container: HTMLElement): HTMLElement | null {
  return container.querySelector('[data-testid="scene-loading-overlay"]');
}

function errorOverlay(container: HTMLElement): HTMLElement | null {
  return container.querySelector('[data-testid="scene-error-overlay"]');
}

/**
 * The prop names its groups: `aisle-display` is the plinth that is there from
 * the first frame, `aisle-display-bottle` is the model that arrives when the
 * GLB does. Both are real three.js `Object3D.name`s in the browser and
 * ordinary attributes under the faked Canvas here, so jsdom can tell which
 * parts of the prop are on screen.
 */
function displayPlinth(container: HTMLElement): HTMLElement | null {
  return container.querySelector('[name="aisle-display"]');
}

function displayBottle(container: HTMLElement): HTMLElement | null {
  return container.querySelector('[name="aisle-display-bottle"]');
}

async function settle(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

afterEach(() => {
  document.body.innerHTML = "";
  gates.shelves.reset();
  gates.model.reset();
});

describe("the committed CC0 asset", () => {
  it("is at the path data/models/README.md documents, with the documented SHA-256", () => {
    // The provenance README records the licence, the source and this hash. A
    // hash that no longer matches means the file in the repo is not the
    // Khronos CC0 asset the README says it is, which is a licensing and
    // auditing problem long before it is a rendering one. It also catches the
    // duller failure of the file going missing from the commit, which would
    // break the import in AisleDisplay.tsx.
    const path = resolve(__dirname, "../../data/models/WaterBottle.glb");
    const bytes = readFileSync(path);
    expect(bytes.byteLength).toBe(8_966_700);
    expect(createHash("sha256").update(bytes).digest("hex")).toBe(
      "b337e526fd6a162013c2984aeec163f5fbb4f717252724dfc3f3458bd51df94b",
    );
  });
});

describe("where the display prop stands", () => {
  it("is in the aisle gap between two bays, not inside any bay", () => {
    // Rule 1: it is a store fixture, not merchandise. The gap between two
    // gondolas is outside every bay's footprint, so nothing about it can be
    // mistaken for shelf space.
    const place = aisleDisplayPlacement(planogram);
    const leftBay = planogram.bays[0];
    const gapLeft = bayLeftX(planogram, 0) + leftBay.width_m;
    const gapRight = gapLeft + BAY_GAP_M;

    expect(place.model.x).toBeGreaterThan(gapLeft);
    expect(place.model.x).toBeLessThan(gapRight);
    expect(place.column.x).toBe(place.model.x);
    expect(place.deck.x).toBe(place.model.x);
  });

  it("puts the bottle clear of every slot in the planogram", () => {
    // Rule 1 again, checked exhaustively rather than by eye: no slot's
    // rectangle on the bay face may overlap the bottle's footprint. The bottle
    // is a solid, so its extent is taken from the model's own bounding box.
    const place = aisleDisplayPlacement(planogram);
    const bottleLeft = place.model.x - place.modelSize.w / 2;
    const bottleRight = place.model.x + place.modelSize.w / 2;
    const bottleBottom = place.model.y - place.modelSize.h / 2;
    const bottleTop = place.model.y + place.modelSize.h / 2;

    let checked = 0;
    for (let bayIndex = 0; bayIndex < planogram.bays.length; bayIndex++) {
      for (const shelf of planogram.bays[bayIndex].shelves) {
        for (const slot of shelf.slots) {
          const center = slotCenter(planogram, bayIndex, shelf, slot);
          const size = slotSize(slot);
          const overlapsX =
            bottleRight > center.x - size.w / 2 && bottleLeft < center.x + size.w / 2;
          const overlapsY =
            bottleTop > center.y - size.h / 2 && bottleBottom < center.y + size.h / 2;
          expect(
            overlapsX && overlapsY,
            `${slot.slot_id} overlaps the display prop`,
          ).toBe(false);
          checked++;
        }
      }
    }
    // Guards the loop itself: an empty planogram would pass every assertion
    // above and prove nothing.
    expect(checked).toBe(30);
  });

  it("never sits at the height of the deliberately empty eye-level slot B1S3P2", () => {
    // The single most load-bearing empty slot in the study. Even at a
    // different x, a bottle floating at exactly that height beside it would
    // read as the answer to "what should go here", which is the one question
    // the demo asks the viewer to think about.
    const shelf = planogram.bays[0].shelves.find((s) => s.shelf_id === "B1S3");
    expect(shelf).toBeDefined();
    const slot = shelf?.slots.find((s) => s.slot_id === "B1S3P2");
    expect(slot).toBeDefined();
    expect(slot?.sku_id).toBeNull();

    const place = aisleDisplayPlacement(planogram);
    const bottleBottom = place.model.y - place.modelSize.h / 2;
    // The empty slot's band runs from the shelf board up by the slot height.
    const slotTop = (shelf?.height_m ?? 0) + (slot?.height_m ?? 0);
    expect(bottleBottom).toBeGreaterThan(slotTop);
  });

  it("stands the bottle on its own plinth rather than floating it", () => {
    // A prop with nothing under it reads as a bug. The deck's top surface and
    // the bottle's base are the same height, and the column reaches the floor.
    const place = aisleDisplayPlacement(planogram);
    const deckTop = place.deck.y + place.deckSize.h / 2;
    const bottleBottom = place.model.y - place.modelSize.h / 2;
    expect(bottleBottom).toBeCloseTo(deckTop, 6);

    const columnBottom = place.column.y - place.columnSize.h / 2;
    expect(columnBottom).toBeCloseTo(0, 6);
    const columnTop = place.column.y + place.columnSize.h / 2;
    expect(columnTop).toBeCloseTo(place.deck.y - place.deckSize.h / 2, 6);
  });
});

describe("the shelves and the 8.6 MB model load independently", () => {
  it("clears the loading overlay as soon as the pack textures resolve, with the model still in flight", async () => {
    // Rule 2, and the whole reason the prop has a Suspense boundary of its
    // own. Sharing the shelves' boundary would hold this overlay up for the
    // length of an 8.6 MB download.
    const view = mountScene();
    expect(loadingOverlay(view.container)).not.toBeNull();

    await act(async () => {
      gates.shelves.resolve();
    });
    await settle();

    expect(loadingOverlay(view.container)).toBeNull();
    expect(errorOverlay(view.container)).toBeNull();
    // Still suspended: the shelves did not wait for it, and it has not arrived.
    expect(displayBottle(view.container)).toBeNull();
    // Its plinth is procedural and costs nothing, so that much is already up.
    expect(displayPlinth(view.container)).not.toBeNull();

    view.unmount();
  });

  it("adds the bottle to the store when it finally arrives, without disturbing the shelves", async () => {
    const view = mountScene();

    await act(async () => {
      gates.shelves.resolve();
    });
    await settle();
    expect(loadingOverlay(view.container)).toBeNull();

    await act(async () => {
      gates.model.resolve();
    });
    await settle();

    expect(displayBottle(view.container)).not.toBeNull();
    expect(loadingOverlay(view.container)).toBeNull();
    expect(errorOverlay(view.container)).toBeNull();

    view.unmount();
  });
});

describe("when the model fails to load", () => {
  it("degrades to no bottle rather than no store", async () => {
    // Rule 3. The GLB is committed, unlike the pack textures, so the texture
    // overlay's `make seed` remedy would be actively misleading here - and a
    // full-screen failure for a decoration would be out of all proportion.
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    const view = mountScene();

    await act(async () => {
      gates.shelves.resolve();
    });
    await settle();

    await act(async () => {
      gates.model.fail(new Error("404: /assets/WaterBottle.glb"));
    });
    await settle();

    // No bottle, and no plinth left stranded under it either...
    expect(displayBottle(view.container)).toBeNull();
    expect(displayPlinth(view.container)).toBeNull();
    // ...and no overlay of either kind: the store is untouched.
    expect(errorOverlay(view.container)).toBeNull();
    expect(loadingOverlay(view.container)).toBeNull();
    // The store itself is all still there.
    expect(view.container.textContent).toContain(planogram.name);
    expect(view.container.textContent).toContain("Cart (0)");
    const checkout = [...view.container.querySelectorAll("button")].find(
      (candidate) => candidate.textContent === "Checkout",
    );
    expect(checkout).not.toBeUndefined();
    expect(
      view.container.querySelector('button[aria-label="Previous bay"]'),
    ).not.toBeNull();
    expect(view.container.querySelector('button[aria-label="Next bay"]')).not.toBeNull();

    view.unmount();
    consoleError.mockRestore();
  });

  it("does not stop the shelves loading afterwards", async () => {
    // Order matters: a model that fails first must not poison the shelves'
    // own boundary, which is the failure mode a single shared boundary would
    // have had.
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    const view = mountScene();

    await act(async () => {
      gates.model.fail(new Error("404: /assets/WaterBottle.glb"));
    });
    await settle();
    expect(loadingOverlay(view.container)).not.toBeNull();

    await act(async () => {
      gates.shelves.resolve();
    });
    await settle();

    expect(loadingOverlay(view.container)).toBeNull();
    expect(errorOverlay(view.container)).toBeNull();

    view.unmount();
    consoleError.mockRestore();
  });

  it("reports the real error on the console instead of swallowing it", async () => {
    // Silent for the shopper is not the same as silent for whoever is
    // debugging: a prop that vanishes with no trace is worse than one that
    // fails loudly in a terminal.
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    const view = mountScene();

    const failure = new Error("404: /assets/WaterBottle.glb");
    await act(async () => {
      gates.model.fail(failure);
    });
    await settle();

    // Not merely "something was logged": React logs every error a boundary
    // catches all by itself, so a boundary that reported nothing of its own
    // would still slip past a looser check. The prop's own report, naming the
    // prop and carrying the actual error, has to be among the calls.
    const reportedTheRealError = consoleError.mock.calls.some(
      (call) =>
        call.some((arg) => String(arg).includes("AisleDisplay")) &&
        call.some((arg) => arg === failure || String(arg).includes(failure.message)),
    );
    expect(reportedTheRealError).toBe(true);

    view.unmount();
    consoleError.mockRestore();
  });
});
