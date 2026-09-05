import { afterEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import type { ReactElement, ReactNode } from "react";
import { createRoot } from "react-dom/client";
import demoAisleJson from "../../data/planograms/demo_aisle.json";
import type { Event as ShopperEvent } from "@/contracts/event.schema";
import type { Planogram } from "@/contracts/planogram.schema";
import type { EventSink } from "@/capture/SessionSocket";
import { PlanogramScene } from "@/store/PlanogramScene";

/**
 * `<Suspense fallback={null}>` around `Bay` (PlanogramScene.tsx, around L363)
 * means the scene renders nothing for as long as any `ProductSlot`'s
 * `useTexture` call is still loading a pack texture - measured by hand at 1
 * to 3 seconds on a real store. Nothing is on screen and nothing is
 * registered for the raycaster to hit, so a click in that window is not
 * merely ugly, it is thrown away with no error, no state change and no
 * `logger.log` call to show it ever happened. A presenter clicking a product
 * the instant the store opens would hit exactly that.
 *
 * This file proves the readiness gate that fixes it:
 *  - an overlay covers the canvas for as long as any texture is unresolved,
 *    and it is a real, hit-testable element rather than nothing, so a click
 *    lands on it instead of vanishing;
 *  - it clears the moment `SceneReadySentinel` proves every texture has
 *    settled - a real signal read from React's own Suspense bookkeeping, not
 *    a guessed timeout;
 *  - it never takes the HUD, the cart or the bay chevrons down with it, which
 *    is what `sessionFinish.test.tsx`, `devSessionFinish.test.tsx` and
 *    `gazeHandoff.test.tsx` depend on - they mock `Canvas` down to `null`, so
 *    in those files the scene never becomes ready at all, and the store must
 *    still work regardless.
 */

// The real Canvas needs a WebGL context jsdom does not have. The other three
// files sidestep that by mocking Canvas down to `null`, which also means
// nothing inside it - including whatever this file needs to test - ever
// mounts. This file's whole point is the moment children of <Suspense>
// mount, so the fake Canvas here renders its children for real instead.
vi.mock("@react-three/fiber", () => ({
  Canvas: ({ children }: { children?: ReactNode }) => <>{children}</>,
  useFrame: () => undefined,
  useThree: () => ({}),
}));

// StationController drives the camera through `useThree`/`useFrame`, and the
// stubs above give it nothing real to drive. It sits outside the <Suspense>
// boundary this file exists to test - its own behaviour is StationController
// and SlotMapper's tests to cover, not this file's - so it is faked away
// rather than made to limp along on a fake camera.
vi.mock("@/store/StationController", () => ({
  StationController: () => null,
}));

// A controllable stand-in for the real Suspense chain (Bay -> ProductSlot ->
// `useTexture`). `read()` throws a promise while "loading", exactly like
// `useTexture` really does under the hood (drei's `useTexture` is built on
// `suspend-react`, which suspends by throwing a promise) - and stops once
// `resolve()` is called, standing in for every pack texture finishing its
// fetch. `vi.hoisted` is required here: `vi.mock` factories run before this
// file's own top-level code, so the state they close over has to be created
// through it rather than as an ordinary module-level variable.
const bayGate = vi.hoisted(() => {
  let ready = false;
  let pendingResolve: (() => void) | null = null;
  return {
    read(): void {
      if (ready) return;
      throw new Promise<void>((resolve) => {
        pendingResolve = resolve;
      });
    },
    resolve(): void {
      ready = true;
      const settle = pendingResolve;
      pendingResolve = null;
      settle?.();
    },
    reset(): void {
      ready = false;
      pendingResolve = null;
    },
  };
});

vi.mock("@/store/Bay", () => ({
  Bay: () => {
    bayGate.read();
    return null;
  },
}));

// The aisle display prop hangs off its own <Suspense> boundary, deliberately
// separate from the shelf one this file is about, so it is not part of what is
// under test here. Left real it would still run drei's `useGLTF` against the
// GLB's `/@fs/...` dev URL, which jsdom's fetch rejects outright ("Failed to
// parse URL"), and the caught failure would print a stack into this file's
// output on every run. Stubbing it is the same move already made above for
// StationController and Bay: keep the 3D children that are not under test out
// of the way, so a failure here means the readiness gate broke and nothing else.
vi.mock("@/store/AisleDisplay", () => ({
  AisleDisplay: () => null,
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

function overlay(container: HTMLElement): HTMLElement | null {
  return container.querySelector('[data-testid="scene-loading-overlay"]');
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
  bayGate.reset();
});

describe("the scene loading overlay", () => {
  it("covers the scene the instant it mounts, before any texture has resolved", () => {
    const view = mountScene();

    const shown = overlay(view.container);
    expect(shown).not.toBeNull();
    expect(shown?.textContent).toContain("Loading shelves");

    view.unmount();
  });

  it("clears once every texture has resolved, and does not come back", async () => {
    const view = mountScene();
    expect(overlay(view.container)).not.toBeNull();

    await act(async () => {
      bayGate.resolve();
    });
    await settle();

    expect(overlay(view.container)).toBeNull();

    view.unmount();
  });

  it("never sets pointer-events: none, so a click during loading lands on it instead of an empty canvas", () => {
    // This is the fix for the swallowed click: covering the canvas with a
    // real, visible, hit-testable element means a pointer event during
    // loading always lands on *something* - this overlay - instead of
    // falling through to a <canvas> with nothing registered on its
    // raycaster yet. An overlay with `pointerEvents: "none"` would look
    // identical on screen and still lose the click, so that is exactly the
    // regression this guards against.
    const view = mountScene();

    const shown = overlay(view.container);
    expect(shown).not.toBeNull();
    expect(shown?.style.pointerEvents).not.toBe("none");

    view.unmount();
  });
});

describe("while the overlay is showing", () => {
  it("still renders the HUD, the cart, the checkout button and the bay chevrons", () => {
    const view = mountScene();
    expect(overlay(view.container)).not.toBeNull();

    expect(view.container.textContent).toContain(planogram.name);
    expect(view.container.textContent).toContain("Cart (0)");

    const checkout = [...view.container.querySelectorAll("button")].find(
      (candidate) => candidate.textContent === "Checkout",
    );
    expect(checkout).not.toBeUndefined();

    expect(
      view.container.querySelector('button[aria-label="Previous bay"]'),
    ).not.toBeNull();
    expect(
      view.container.querySelector('button[aria-label="Next bay"]'),
    ).not.toBeNull();

    view.unmount();
  });
});
