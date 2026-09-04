import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import type { ReactElement } from "react";
import { createRoot } from "react-dom/client";
import { CameraCheck } from "@/capture/CameraCheck";
import { CaptureFlow, type CaptureResult } from "@/capture/CaptureFlow";

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

function has(container: HTMLElement, testId: string): boolean {
  return container.querySelector(`[data-testid="${testId}"]`) !== null;
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
function fakeStream(): { stream: MediaStream; stops: number[] } {
  const stops: number[] = [];
  const track = (id: number) => ({
    stop: () => stops.push(id),
    kind: "video",
    readyState: "live",
  });
  const stream = { getTracks: () => [track(1), track(2)] } as unknown as MediaStream;
  return { stream, stops };
}

let getUserMedia = vi.fn(async () => fakeStream().stream);

function installCamera(impl: () => Promise<MediaStream>): void {
  getUserMedia = vi.fn(impl);
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia },
  });
}

beforeEach(() => {
  installCamera(async () => fakeStream().stream);
});

afterEach(() => {
  Reflect.deleteProperty(navigator as unknown as Record<string, unknown>, "mediaDevices");
  document.body.innerHTML = "";
});

describe("consent", () => {
  it("starts no session and touches no camera when the shopper declines", async () => {
    const onComplete = vi.fn<(result: CaptureResult) => void>();
    const view = mount(<CaptureFlow onComplete={onComplete} />);

    click(view.container, "consent-decline");
    await settle();

    expect(onComplete).not.toHaveBeenCalled();
    expect(getUserMedia).not.toHaveBeenCalled();
    expect(find(view.container, "consent-declined").textContent ?? "").toContain(
      "No session was started",
    );
    // No way back into the flow from here, and no intake question asked.
    expect(has(view.container, "intake-continue")).toBe(false);

    view.unmount();
  });

  it("asks for consent explicitly — nothing is pre-agreed", () => {
    const view = mount(<CaptureFlow onComplete={vi.fn()} />);

    expect(has(view.container, "consent-agree")).toBe(true);
    expect(has(view.container, "consent-decline")).toBe(true);
    expect(has(view.container, "intake-continue")).toBe(false);

    view.unmount();
  });
});

describe("intake", () => {
  it("will not continue until all three questions are answered", () => {
    const view = mount(<CaptureFlow onComplete={vi.fn()} />);
    click(view.container, "consent-agree");

    const continueButton = find(view.container, "intake-continue") as HTMLButtonElement;
    expect(continueButton.disabled).toBe(true);

    click(view.container, "intake-has_list-yes");
    click(view.container, "intake-same_brand-no");
    expect((find(view.container, "intake-continue") as HTMLButtonElement).disabled).toBe(
      true,
    );

    click(view.container, "intake-hurry-yes");
    expect((find(view.container, "intake-continue") as HTMLButtonElement).disabled).toBe(
      false,
    );

    view.unmount();
  });
});

describe("camera check", () => {
  it("releases every track of the stream it opened", async () => {
    const opened = fakeStream();
    installCamera(async () => opened.stream);
    const onCameraReady = vi.fn();
    const view = mount(
      <CameraCheck onCameraReady={onCameraReady} onCursorOnly={vi.fn()} />,
    );

    click(view.container, "camera-start");
    await settle();

    // The check proves the camera opens; WebGazer opens its own stream next, so
    // this one is handed straight back.
    expect(opened.stops).toEqual([1, 2]);
    click(view.container, "camera-continue");
    expect(onCameraReady).toHaveBeenCalledTimes(1);

    view.unmount();
  });

  it("offers cursor_only instead of a dead end when the camera is refused", async () => {
    installCamera(() =>
      Promise.reject(new DOMException("Permission denied", "NotAllowedError")),
    );
    const onCursorOnly = vi.fn<(reason: string) => void>();
    const view = mount(
      <CameraCheck onCameraReady={vi.fn()} onCursorOnly={onCursorOnly} />,
    );

    click(view.container, "camera-start");
    await settle();

    expect(find(view.container, "camera-error").textContent ?? "").not.toBe("");
    click(view.container, "camera-cursor-only");
    expect(onCursorOnly).toHaveBeenCalledTimes(1);

    view.unmount();
  });
});

describe("the whole flow, cursor-only", () => {
  it("completes with the real consent, intake, archetype and mode", async () => {
    installCamera(() => Promise.reject(new DOMException("No camera", "NotFoundError")));
    const onComplete = vi.fn<(result: CaptureResult) => void>();
    const view = mount(<CaptureFlow onComplete={onComplete} />);

    click(view.container, "consent-agree");
    click(view.container, "intake-has_list-yes");
    click(view.container, "intake-same_brand-yes");
    click(view.container, "intake-hurry-yes");
    click(view.container, "intake-continue");

    click(view.container, "camera-start");
    await settle();
    click(view.container, "camera-cursor-only");

    expect(onComplete).not.toHaveBeenCalled(); // the shopper starts the store
    click(view.container, "done-start");

    expect(onComplete).toHaveBeenCalledTimes(1);
    expect(onComplete.mock.calls[0][0]).toEqual({
      consent: true,
      intake: { has_list: true, same_brand: true, hurry: true },
      archetype_label: "mission", // ordered rules: not loyalist
      mode: "cursor_only",
      calibration_error_px: null,
    });

    view.unmount();
  });
});
