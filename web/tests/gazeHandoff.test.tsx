import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import type { ReactElement } from "react";
import { createRoot } from "react-dom/client";
import { CALIBRATION_POINTS, VALIDATION_POINTS } from "@/capture/calibrationMath";
import { CLICKS_PER_POINT } from "@/capture/Calibration";
import { COLLECT_MS, SETTLE_MS } from "@/capture/Validation";
import { CaptureFlow, type CaptureResult } from "@/capture/CaptureFlow";
import { GazeTracker, type WebGazerLike } from "@/capture/GazeTracker";

/**
 * S11 decision 6: the gaze-ownership handoff.
 *
 * Before this, CaptureFlow released the camera the moment validation finished,
 * so a `mode: "webcam"` session recorded an honest calibration error and then
 * produced no gaze and no fixations at all. The tracker now survives into the
 * shopping session - and this file is the audit that it survives *only* there,
 * with every S10 privacy guarantee intact.
 */

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

interface Mounted {
  container: HTMLDivElement;
  unmount: () => void;
}

function mount(ui: ReactElement): Mounted {
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

function find(container: HTMLElement, testId: string): HTMLElement {
  const element = container.querySelector<HTMLElement>(`[data-testid="${testId}"]`);
  if (element === null) throw new Error(`no element with data-testid="${testId}"`);
  return element;
}

function click(container: HTMLElement, testId: string): void {
  const element = find(container, testId);
  act(() => {
    element.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

async function settle(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

/** A MediaStream stand-in: jsdom has neither getUserMedia nor MediaStream. */
function fakeStream(): MediaStream {
  const track = () => ({ stop: () => undefined, kind: "video", readyState: "live" });
  return { getTracks: () => [track(), track()] } as unknown as MediaStream;
}

/** The same WebGazer double the S10 tracker tests use. */
function makeFakeWebGazer() {
  const held: { listener: ((data: unknown, elapsed: number) => void) | null } = {
    listener: null,
  };
  const wg = {
    showVideo: vi.fn(),
    showVideoPreview: vi.fn(),
    showFaceOverlay: vi.fn(),
    showFaceFeedbackBox: vi.fn(),
    showPredictionPoints: vi.fn(),
    saveDataAcrossSessions: vi.fn(),
    removeMouseEventListeners: vi.fn(),
    setGazeListener: vi.fn((fn: (data: unknown, elapsed: number) => void) => {
      held.listener = fn;
    }),
    clearGazeListener: vi.fn(),
    recordScreenPosition: vi.fn(),
    begin: vi.fn(async (): Promise<void> => {}),
    end: vi.fn(),
    stopVideo: vi.fn(),
    params: { videoElementId: "webgazerVideoFeed" } as Record<string, unknown>,
  };
  return {
    wg,
    api: wg as unknown as WebGazerLike,
    emit(x: number, y: number): void {
      if (held.listener === null) throw new Error("setGazeListener was never called");
      held.listener({ x, y }, 0);
    },
  };
}

type Fake = ReturnType<typeof makeFakeWebGazer>;

/**
 * Consent -> intake -> camera -> 9-point calibration -> 4-point validation, all
 * the way to the "you are set" screen. `aim` decides where the fake tracker
 * claims the shopper looked during validation, and therefore whether the
 * session ends up webcam or cursor_only.
 */
async function runCaptureFlow(
  fake: Fake,
  aim: (target: { x: number; y: number }) => { x: number; y: number },
): Promise<{ view: Mounted; onComplete: ReturnType<typeof vi.fn> }> {
  const onComplete = vi.fn<(result: CaptureResult) => void>();
  const view = mount(
    <CaptureFlow
      onComplete={onComplete}
      createTracker={() => new GazeTracker({ load: async () => fake.api })}
    />,
  );

  click(view.container, "consent-agree");
  click(view.container, "intake-has_list-yes");
  click(view.container, "intake-same_brand-no");
  click(view.container, "intake-hurry-no");
  click(view.container, "intake-continue");

  click(view.container, "camera-start");
  await settle();
  click(view.container, "camera-continue");
  // The tracker loads and WebGazer "opens the camera" here.
  await settle();

  for (let point = 0; point < CALIBRATION_POINTS.length; point += 1) {
    for (let n = 0; n < CLICKS_PER_POINT; n += 1) {
      click(view.container, `calibration-point-${point}`);
    }
  }
  await settle();

  for (const point of VALIDATION_POINTS) {
    await act(async () => {
      vi.advanceTimersByTime(SETTLE_MS);
    });
    const looked = aim({
      x: point.x * window.innerWidth,
      y: point.y * window.innerHeight,
    });
    await act(async () => {
      for (let n = 0; n < 5; n += 1) fake.emit(looked.x, looked.y);
    });
    await act(async () => {
      vi.advanceTimersByTime(COLLECT_MS);
    });
  }
  await settle();

  return { view, onComplete };
}

const onTarget = (target: { x: number; y: number }) => target;
const wayOff = () => ({ x: 0, y: 0 });

beforeEach(() => {
  vi.useFakeTimers();
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia: vi.fn(async () => fakeStream()) },
  });
});

afterEach(() => {
  vi.useRealTimers();
  Reflect.deleteProperty(navigator as unknown as Record<string, unknown>, "mediaDevices");
  document.body.innerHTML = "";
});

describe("a webcam session", () => {
  it("hands the running tracker to the store instead of closing the camera", async () => {
    const fake = makeFakeWebGazer();
    const { view, onComplete } = await runCaptureFlow(fake, onTarget);

    click(view.container, "done-start");
    const result: CaptureResult = onComplete.mock.calls[0][0];

    expect(result.mode).toBe("webcam");
    // The fake looked exactly at every target, so the measured error is zero up
    // to the float noise of averaging four centroids.
    expect(result.calibration_error_px ?? Number.NaN).toBeCloseTo(0, 6);
    expect(result.tracker).toBeInstanceOf(GazeTracker);
    // Still predicting: this is the whole point of S11 decision 6.
    expect(result.tracker?.running).toBe(true);
    expect(fake.wg.end).not.toHaveBeenCalled();

    view.unmount();
  });

  it("does not release a tracker it has given away, then releases it on request", async () => {
    const fake = makeFakeWebGazer();
    const { view, onComplete } = await runCaptureFlow(fake, onTarget);

    click(view.container, "done-start");
    const handed: GazeTracker | undefined = onComplete.mock.calls[0][0].tracker;

    // The flow unmounts as soon as the store takes over. Its cleanup must not
    // reach into the tracker it no longer owns.
    view.unmount();
    expect(fake.wg.end).not.toHaveBeenCalled();
    expect(handed?.running).toBe(true);

    // ...and the new owner can still hand the camera back.
    handed?.stop();
    expect(fake.wg.end).toHaveBeenCalledTimes(1);
    expect(fake.wg.clearGazeListener).toHaveBeenCalled();
  });

  it("still delivers samples after the handoff", async () => {
    const fake = makeFakeWebGazer();
    const { view, onComplete } = await runCaptureFlow(fake, onTarget);

    click(view.container, "done-start");
    const handed: GazeTracker | undefined = onComplete.mock.calls[0][0].tracker;
    view.unmount();

    // What PlanogramScene subscribes with, and what feeds FixationFilter.
    const seen: { x: number; y: number; conf: number; t: number }[] = [];
    handed?.subscribe((sample) => seen.push(sample));
    fake.emit(640, 400);

    expect(seen).toHaveLength(1);
    expect(seen[0].x).toBe(640);
    expect(seen[0].y).toBe(400);
    expect(seen[0].conf).toBe(1);
    expect(Number.isFinite(seen[0].t)).toBe(true);
    handed?.stop();
  });

  it("releases the camera if the shopper never starts shopping", async () => {
    const fake = makeFakeWebGazer();
    const { view } = await runCaptureFlow(fake, onTarget);

    // Reached "you are set" and closed the tab. No handoff happened, so the
    // flow is still the owner and its unmount cleanup must release the camera.
    view.unmount();
    expect(fake.wg.end).toHaveBeenCalledTimes(1);
  });
});

describe("a cursor_only session", () => {
  it("closes the camera at validation and hands nothing over", async () => {
    const fake = makeFakeWebGazer();
    const { view, onComplete } = await runCaptureFlow(fake, wayOff);

    // The camera goes back the moment the verdict is in, before the shopper
    // even sees the "you are set" screen.
    expect(fake.wg.end).toHaveBeenCalledTimes(1);

    click(view.container, "done-start");
    const result: CaptureResult = onComplete.mock.calls[0][0];

    expect(result.mode).toBe("cursor_only");
    expect(result.tracker).toBeUndefined();
    expect("tracker" in result).toBe(false);

    view.unmount();
    expect(fake.wg.end).toHaveBeenCalledTimes(1);
  });
});

describe("the S10 privacy contract, across the handoff", () => {
  it("never shows the video, never shows a gaze dot, never persists training", async () => {
    const fake = makeFakeWebGazer();
    const { view, onComplete } = await runCaptureFlow(fake, onTarget);
    click(view.container, "done-start");
    const handed: GazeTracker | undefined = onComplete.mock.calls[0][0].tracker;
    view.unmount();

    // The dot is the spectator view's job. A shopper who can see their own gaze
    // dot stares at it, and a stared-at dot is not shopping data.
    for (const call of fake.wg.showPredictionPoints.mock.calls) expect(call[0]).toBe(false);
    for (const call of fake.wg.showVideo.mock.calls) expect(call[0]).toBe(false);
    for (const call of fake.wg.showVideoPreview.mock.calls) expect(call[0]).toBe(false);
    for (const call of fake.wg.saveDataAcrossSessions.mock.calls) expect(call[0]).toBe(false);
    expect(fake.wg.saveDataAcrossSessions).toHaveBeenCalledWith(false);

    // Nothing on the shopper's screen renders gaze, before or after the handoff.
    expect(document.body.querySelector("#webgazerGazeDot")).toBeNull();

    handed?.stop();
  });

  it("publishes only {x, y, conf, t} - never a frame or an eye patch", async () => {
    const fake = makeFakeWebGazer();
    const { view, onComplete } = await runCaptureFlow(fake, onTarget);
    click(view.container, "done-start");
    const handed: GazeTracker | undefined = onComplete.mock.calls[0][0].tracker;
    view.unmount();

    const seen: object[] = [];
    handed?.subscribe((sample) => seen.push(sample));
    fake.emit(100, 200);

    expect(seen).toHaveLength(1);
    expect(Object.keys(seen[0]).sort()).toEqual(["conf", "t", "x", "y"]);

    handed?.stop();
  });
});
