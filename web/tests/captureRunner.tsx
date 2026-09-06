import { act } from "react";
import type { ReactElement } from "react";
import { createRoot } from "react-dom/client";
import { vi } from "vitest";
import { CALIBRATION_POINTS, VALIDATION_POINTS } from "@/capture/calibrationMath";
import { CLICKS_PER_POINT } from "@/capture/Calibration";
import { COLLECT_MS, SETTLE_MS } from "@/capture/Validation";
import { CaptureFlow, type CaptureResult } from "@/capture/CaptureFlow";
import { GazeTracker, type WebGazerLike } from "@/capture/GazeTracker";

/**
 * The capture flow, driven end to end in jsdom - consent through to the "you
 * are set" screen - so that more than one test file can ask what a shopper is
 * actually shown at the verdict.
 *
 * This is `gazeHandoff.test.tsx`'s harness, lifted out rather than copied a
 * third time. It is deliberately not a `*.test.tsx` file: vitest's `include`
 * only collects those, so this one is a fixture, like `whatifFixture.ts`.
 */

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

export interface Mounted {
  container: HTMLDivElement;
  unmount: () => void;
}

export function mount(ui: ReactElement): Mounted {
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

export function find(container: HTMLElement, testId: string): HTMLElement {
  const element = container.querySelector<HTMLElement>(`[data-testid="${testId}"]`);
  if (element === null) throw new Error(`no element with data-testid="${testId}"`);
  return element;
}

export function has(container: HTMLElement, testId: string): boolean {
  return container.querySelector(`[data-testid="${testId}"]`) !== null;
}

export function text(container: HTMLElement, testId: string): string {
  return find(container, testId).textContent ?? "";
}

export function click(container: HTMLElement, testId: string): void {
  const element = find(container, testId);
  act(() => {
    element.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

export async function settle(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

/** A MediaStream stand-in: jsdom has neither getUserMedia nor MediaStream. */
export function fakeStream(): MediaStream {
  const track = () => ({ stop: () => undefined, kind: "video", readyState: "live" });
  return { getTracks: () => [track(), track()] } as unknown as MediaStream;
}

/** Point `navigator.mediaDevices` at something jsdom can actually run. */
export function installCamera(impl: () => Promise<MediaStream>): void {
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia: vi.fn(impl) },
  });
}

/** Fake timers plus a working camera: what every capture-flow test needs. */
export function installCaptureEnvironment(): void {
  vi.useFakeTimers();
  installCamera(async () => fakeStream());
}

export function restoreCaptureEnvironment(): void {
  vi.useRealTimers();
  Reflect.deleteProperty(navigator as unknown as Record<string, unknown>, "mediaDevices");
  document.body.innerHTML = "";
}

/** The same WebGazer double the S10 tracker tests use. */
export function makeFakeWebGazer() {
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

export type Fake = ReturnType<typeof makeFakeWebGazer>;

export type Aim = (target: { x: number; y: number }) => { x: number; y: number };

/** A tracker that lands on every validation dot: the session ends up `webcam`. */
export const onTarget: Aim = (target) => target;

/** A tracker that answers the top-left corner every time: `cursor_only`. */
export const wayOff: Aim = () => ({ x: 0, y: 0 });

export interface Run {
  view: Mounted;
  onComplete: ReturnType<typeof vi.fn>;
  fake: Fake;
  /** The result the flow hands over; only valid after clicking `done-start`. */
  result: () => CaptureResult;
}

/**
 * Consent -> intake -> camera -> 9-point calibration -> 4-point validation, all
 * the way to the "you are set" screen. `aim` decides where the fake tracker
 * claims the shopper looked during validation, and therefore whether the
 * session ends up webcam or cursor_only.
 *
 * Requires fake timers: the validation screen is driven by two of them.
 */
export async function runCaptureFlow(aim: Aim, fake: Fake = makeFakeWebGazer()): Promise<Run> {
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

  return {
    view,
    onComplete,
    fake,
    result: () => {
      if (onComplete.mock.calls.length === 0) {
        throw new Error("the flow has not handed anything over yet - click done-start");
      }
      return onComplete.mock.calls[0][0] as CaptureResult;
    },
  };
}
