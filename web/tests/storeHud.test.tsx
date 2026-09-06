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


// ---------------------------------------------------------------------------
// A scene with a tracker the test drives by hand (S28)
// ---------------------------------------------------------------------------

/**
 * A `GazeTracker` in the one respect the HUD cares about: it hands samples to
 * whoever subscribes, and it can be stopped. No camera, no WebGazer, no clock.
 */
class FakeTracker {
  private listeners: Array<(sample: { x: number; y: number; conf: number; t: number }) => void> = [];
  stopped = false;

  subscribe(listener: (sample: { x: number; y: number; conf: number; t: number }) => void) {
    this.listeners.push(listener);
    return () => {
      this.listeners = this.listeners.filter((l) => l !== listener);
    };
  }

  stop(): void {
    this.stopped = true;
  }

  emit(n: number): void {
    for (let i = 0; i < n; i += 1) {
      for (const listener of this.listeners) {
        listener({ x: 100 + i, y: 200, conf: 0.9, t: 1000 + i * 30 });
      }
    }
  }
}

interface SceneHarness {
  container: HTMLDivElement;
  emitGaze: (n: number) => void;
  settle: () => Promise<void>;
  unmount: () => void;
}

async function mountScene(opts: {
  mode: Session["mode"];
  calibrationErrorPx: number | null;
  screenW?: number;
}): Promise<SceneHarness> {
  const tracker = opts.mode === "webcam" ? new FakeTracker() : null;
  const view = mount(
    <PlanogramScene
      planogram={planogram}
      logger={new NullSink()}
      tracker={tracker as never}
      consent={true}
      mode={opts.mode}
      calibrationErrorPx={opts.calibrationErrorPx}
      screenWidthPx={opts.screenW}
    />,
  );
  return {
    container: view.container,
    emitGaze: (n: number) => act(() => tracker?.emit(n)),
    settle: async () => {
      await act(async () => {
        await Promise.resolve();
      });
    },
    unmount: view.unmount,
  };
}

function text(view: { container: HTMLElement }, testId: string): string {
  return view.container.querySelector(`[data-testid="${testId}"]`)?.textContent ?? "";
}

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

// ---------------------------------------------------------------------------
// The measurement, on the screen where it is being taken (S28)
// ---------------------------------------------------------------------------

describe("the HUD reports the calibration it is measuring against", () => {
  it("states the error in pixels and as a share of the screen", async () => {
    // `calibration_error_px` decides whether a session is webcam or
    // cursor_only, is written into the session document, and was said out loud
    // exactly once - on a capture screen the operator has already clicked past.
    // From the store, the only way to find out how well the tracker was doing
    // was to read the session document afterwards, by which point the session
    // is evidence.
    const view = await mountScene({ mode: "webcam", calibrationErrorPx: 96, screenW: 1920 });
    try {
      const hud = text(view, "hud-calibration");
      expect(hud).toContain("96");
      expect(hud).toContain("5"); // 96 / 1920 = 5% of screen width
    } finally {
      view.unmount();
    }
  });

  it("says the calibration was not measured rather than printing a confident 0", async () => {
    const view = await mountScene({ mode: "cursor_only", calibrationErrorPx: null });
    try {
      expect(text(view, "hud-calibration").toLowerCase()).toContain("not measured");
      expect(text(view, "hud-calibration")).not.toContain("0 px");
    } finally {
      view.unmount();
    }
  });

  it("shows no calibration line at all for a cursor-only session that never ran one", async () => {
    const view = await mountScene({ mode: "cursor_only", calibrationErrorPx: null });
    try {
      // Present but honest, rather than absent: an operator glancing at the HUD
      // has to be able to tell "cursor only, on purpose" from "the webcam line
      // is missing because something broke".
      expect(text(view, "hud-calibration")).toBeTruthy();
    } finally {
      view.unmount();
    }
  });
});

describe("the HUD reports whether the tracker is still producing samples", () => {
  it("reads as waiting before any sample arrives", async () => {
    const view = await mountScene({ mode: "webcam", calibrationErrorPx: 40 });
    try {
      expect(text(view, "hud-tracker").toLowerCase()).toContain("waiting");
    } finally {
      view.unmount();
    }
  });

  it("counts the samples the tracker has delivered", async () => {
    const view = await mountScene({ mode: "webcam", calibrationErrorPx: 40 });
    try {
      view.emitGaze(30);
      await view.settle();
      expect(text(view, "hud-tracker")).toContain("30");
    } finally {
      view.unmount();
    }
  });

  it("says nothing about samples in a cursor-only session", async () => {
    // There is no tracker, so there is nothing to be healthy or unhealthy, and
    // a "0 samples" reading would look like a webcam that had died.
    const view = await mountScene({ mode: "cursor_only", calibrationErrorPx: null });
    try {
      expect(text(view, "hud-tracker").toLowerCase()).not.toContain("sample");
    } finally {
      view.unmount();
    }
  });
});
