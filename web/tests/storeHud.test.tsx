import { afterEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import type { ReactElement } from "react";
import { createRoot } from "react-dom/client";
import demoAisleJson from "../../data/planograms/demo_aisle.json";
import type { Event as ShopperEvent } from "@/contracts/event.schema";
import type { Planogram } from "@/contracts/planogram.schema";
import type { Session } from "@/contracts/session.schema";
import type { EventSink } from "@/capture/SessionSocket";
import { rememberSession } from "@/session/lastSession";
import { PlanogramScene, armOfSession } from "@/store/PlanogramScene";

/**
 * Which arm am I on, and is the camera still working?
 *
 * Neither question could be answered from the shopper's own screen. The store
 * opened on `?variant=D` and on `?variant=A` looking identical, and a session
 * whose calibration failed silently degraded to `mode: "cursor_only"` - the
 * documented, correct response to a validation error over 12% of screen width
 * - with nothing anywhere saying so. An operator running a panel of people
 * back to back had no way to catch a mistyped link or a dead webcam until the
 * run was over and the session was already evidence.
 *
 * `web/src/session/urlParams.ts` exists because a shopper *was* measured on
 * the wrong arm once, silently. This is the same failure seen from the other
 * end: the URL rule is now right, and this makes the result of it visible
 * while there is still time to stop.
 *
 * This is static text and that is the whole point. CLAUDE.md forbids the
 * shopper's screen showing their gaze dot, because people stare at the dot and
 * corrupt the measurement. Two labels that never change while the session runs
 * carry no measurement, tell the shopper nothing about how they are doing, and
 * give them nothing to chase.
 *
 * It also may not invent. The arm is the `variant_id` the *server* echoed back
 * from `POST /sessions`, recorded against this session's id by
 * `rememberSession`, and it is only shown when that id matches the session
 * actually streaming events. A note from some other tab's session is not this
 * session's arm, and a HUD that would rather say "unknown" than guess is the
 * same rule `lastSession.ts` already holds itself to.
 */

// The 3D scene is not what is under test, and jsdom has no WebGL. Canvas
// renders null, so StationController, Bay and the drei texture loaders never
// mount - the HUD, the chevrons and the cart are DOM siblings of the canvas
// and keep rendering regardless.
vi.mock("@react-three/fiber", () => ({
  Canvas: () => null,
  useFrame: () => undefined,
  useThree: () => ({}),
}));
vi.mock("@react-three/drei", () => ({
  useTexture: () => ({}),
}));

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

const planogram = demoAisleJson as unknown as Planogram;
const SESSION_ID = "3f1d8c62-0a44-4a5f-9d1e-7c2b6a0f5e11";

class NullSink implements EventSink {
  readonly sessionId = SESSION_ID;
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

function mountStore(
  mode: Session["mode"],
  variantId?: string,
): { container: HTMLDivElement; unmount: () => void } {
  return mount(
    <PlanogramScene
      planogram={planogram}
      logger={new NullSink()}
      tracker={null}
      consent={true}
      mode={mode}
      variantId={variantId}
    />,
  );
}

function hud(container: HTMLElement): string {
  const element = container.querySelector('[data-testid="hud-session"]');
  if (element === null) throw new Error("the store HUD has no session line");
  return element.textContent ?? "";
}

afterEach(() => {
  document.body.innerHTML = "";
  window.localStorage.clear();
});

describe("armOfSession", () => {
  it("is the variant the server recorded against this very session", () => {
    expect(
      armOfSession(SESSION_ID, {
        session_id: SESSION_ID,
        variant_id: "C",
        started_at: "2026-09-06T09:00:00Z",
      }),
    ).toBe("C");
  });

  it("is null for a note about some other session, rather than that note's arm", () => {
    expect(
      armOfSession(SESSION_ID, {
        session_id: "a-different-session",
        variant_id: "D",
        started_at: "2026-09-06T09:00:00Z",
      }),
    ).toBeNull();
  });

  it("is null when there is no note at all", () => {
    expect(armOfSession(SESSION_ID, null)).toBeNull();
  });
});

describe("the store HUD", () => {
  it("names the arm the shopper is being measured on", () => {
    rememberSession({
      session_id: SESSION_ID,
      variant_id: "D",
      started_at: "2026-09-06T09:00:00Z",
    });
    const view = mountStore("webcam");
    expect(hud(view.container)).toContain("Variant D");
    view.unmount();
  });

  it("says when the session has degraded to cursor only", () => {
    rememberSession({
      session_id: SESSION_ID,
      variant_id: "A",
      started_at: "2026-09-06T09:00:00Z",
    });
    const view = mountStore("cursor_only");
    const line = hud(view.container);
    expect(line).toContain("Variant A");
    expect(line.toLowerCase()).toContain("cursor");
    expect(line.toLowerCase()).not.toContain("webcam");
    view.unmount();
  });

  it("says so when the webcam is the one doing the measuring", () => {
    const view = mountStore("webcam");
    const line = hud(view.container);
    expect(line.toLowerCase()).toContain("webcam");
    expect(line.toLowerCase()).not.toContain("cursor");
    view.unmount();
  });

  it("admits it does not know the arm rather than borrowing another session's", () => {
    rememberSession({
      session_id: "some-other-tab",
      variant_id: "B",
      started_at: "2026-09-06T09:00:00Z",
    });
    const view = mountStore("webcam");
    const line = hud(view.container);
    expect(line).not.toContain("Variant B");
    expect(line.toLowerCase()).toContain("unknown");
    view.unmount();
  });

  it("lets an explicit variantId prop win, so main.tsx can hand it down instead", () => {
    rememberSession({
      session_id: SESSION_ID,
      variant_id: "A",
      started_at: "2026-09-06T09:00:00Z",
    });
    const view = mountStore("webcam", "C");
    expect(hud(view.container)).toContain("Variant C");
    view.unmount();
  });
});
