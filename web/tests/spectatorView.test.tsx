import { afterEach, describe, expect, it } from "vitest";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { SpectatorView } from "@/spectator/SpectatorView";
import type {
  SpectatorHandlers,
  SpectatorSocketLike,
} from "@/spectator/SpectatorSocket";
import { lockFromQuery } from "@/spectator/lock";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

const SHA = "a3f9c0d1e2b3a4958677665544332211aabbccddeeff00112233445566778899";
const LOCK = lockFromQuery(`?sha256=${SHA}&locked_at=2026-09-14T10:32:07.412Z`);

interface Message {
  session_id?: string;
  t_ms?: number;
  n_fixations?: number;
  stations_visited?: number;
  attention?: Record<string, number>;
  latest_gaze?: { x: number; y: number } | null;
  spearman?: number | null;
  meaningful?: boolean;
  prediction_id?: string;
  fake?: boolean;
}

/** A SPEC 4.7 frame with sensible defaults, so each test states only its point. */
function frame(overrides: Message = {}): string {
  return JSON.stringify({
    session_id: "sess-1",
    t_ms: 41_200,
    n_fixations: 37,
    stations_visited: 2,
    attention: { B1S3P1: 0.11 },
    latest_gaze: { x: 812, y: 344 },
    spearman: 0.58,
    meaningful: true,
    prediction_id: "pred-1",
    ...overrides,
  });
}

interface Mounted {
  container: HTMLDivElement;
  unmount: () => void;
  handlers(): SpectatorHandlers;
  urls: string[];
  closes: number;
}

interface ViewOptions {
  sessionId?: string | null;
  fake?: boolean;
  lock?: typeof LOCK;
  ceiling?: number | null;
}

function render(options: ViewOptions = {}): Mounted {
  const urls: string[] = [];
  const state = { closes: 0 };
  let captured: SpectatorHandlers | null = null;

  const createSocket = (url: string, handlers: SpectatorHandlers): SpectatorSocketLike => {
    urls.push(url);
    captured = handlers;
    return {
      readyState: 1,
      close: () => {
        state.closes += 1;
      },
    };
  };

  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(
      <SpectatorView
        sessionId={options.sessionId === undefined ? "sess-1" : options.sessionId}
        fake={options.fake ?? false}
        lock={options.lock ?? LOCK}
        ceiling={options.ceiling ?? null}
        createSocket={createSocket}
        now={() => 10_000}
        wallClock={() => new Date(2026, 8, 14, 10, 32, 41)}
        screen={{ w: 1_440, h: 900 }}
        // The fade ticker is the only thing in this component that reads a
        // clock by itself; the ageing it drives is tested in spectatorTrail.
        fadeIntervalMs={null}
      />,
    );
  });

  return {
    container,
    urls,
    get closes() {
      return state.closes;
    },
    handlers: () => {
      if (captured === null) throw new Error("no socket was opened");
      return captured;
    },
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

function text(container: HTMLElement): string {
  return container.textContent ?? "";
}

function deliver(view: Mounted, raw: string): void {
  act(() => {
    view.handlers().onMessage(raw);
  });
}

function open(view: Mounted): void {
  act(() => {
    view.handlers().onOpen();
  });
}

afterEach(() => {
  document.body.innerHTML = "";
});

describe("the prediction badge is on screen before any gaze data", () => {
  it("renders the hash prefix and created_at with the socket still connecting", () => {
    const view = render();

    // Nothing has arrived: no open, no message.
    expect(find(view.container, "spectator-status").dataset.status).toBe("connecting");
    expect(text(find(view.container, "prediction-hash"))).toBe("a3f9c0d1");
    expect(text(find(view.container, "prediction-created-at"))).toContain(
      "2026-09-14T10:32:07.412Z",
    );
    expect(has(view.container, "gaze-dot")).toBe(false);
    expect(find(view.container, "agreement-meter").dataset.state).toBe("warming_up");

    view.unmount();
  });

  it("opens ws/spectator for the session it was given", () => {
    const view = render();
    expect(view.urls).toEqual([`ws://${window.location.host}/ws/spectator/sess-1`]);
    view.unmount();
  });

  it("opens nothing and explains itself with no session id in the URL", () => {
    const view = render({ sessionId: null });
    expect(view.urls).toEqual([]);
    expect(text(find(view.container, "spectator-no-session")).toLowerCase()).toContain(
      "?session=",
    );
    view.unmount();
  });
});

describe("incoming SPEC 4.7 messages", () => {
  it("moves the dot, fills the heatmap and shows the counters", () => {
    const view = render();
    open(view);
    deliver(view, frame());

    expect(text(find(view.container, "stat-n-fixations"))).toContain("37");
    expect(text(find(view.container, "stat-stations-visited"))).toContain("2");
    expect(text(find(view.container, "stat-elapsed"))).toContain("0:41");

    expect(find(view.container, "heat-real-B1S3P1").dataset.value).toBe("0.11");

    // latest_gaze {812, 344} on a 1440x900 screen.
    const dot = find(view.container, "gaze-dot");
    expect(dot.dataset.x).toBe("812");
    expect(dot.dataset.y).toBe("344");

    // ...and the dot moves when the next frame reports a new position.
    deliver(view, frame({ latest_gaze: { x: 100, y: 200 } }));
    expect(find(view.container, "gaze-dot").dataset.x).toBe("100");
    expect(find(view.container, "gaze-dot").dataset.y).toBe("200");

    view.unmount();
  });

  it("keeps the meter grey at 14 fixations and shows rho at 15", () => {
    const view = render();
    open(view);

    deliver(view, frame({ n_fixations: 14, meaningful: false, spearman: 0.94 }));
    expect(find(view.container, "agreement-meter").dataset.state).toBe("warming_up");
    expect(has(view.container, "agreement-rho")).toBe(false);
    expect(text(view.container)).not.toContain("0.94");

    deliver(view, frame({ n_fixations: 15, meaningful: true, spearman: 0.58 }));
    expect(find(view.container, "agreement-meter").dataset.state).toBe("meaningful");
    expect(text(find(view.container, "agreement-rho"))).toContain("0.58");

    view.unmount();
  });

  it("puts the real attention beside the locked prediction when the lock has one", () => {
    const view = render({
      lock: {
        ...LOCK,
        population_fixation_prob: { B1S3P1: 0.038 },
        source: "file",
      },
    });
    open(view);
    deliver(view, frame());

    expect(find(view.container, "heat-real-B1S3P1").dataset.value).toBe("0.11");
    expect(find(view.container, "heat-locked-B1S3P1").dataset.value).toBe("0.038");
    expect(has(view.container, "locked-unavailable")).toBe(false);

    view.unmount();
  });

  it("says the locked prediction is unavailable rather than drawing an empty panel", () => {
    const view = render();
    open(view);
    deliver(view, frame());
    expect(has(view.container, "locked-unavailable")).toBe(true);
    view.unmount();
  });
});

describe("a dropped socket", () => {
  it("is visible, and the numbers it left behind are marked stale", () => {
    const view = render();
    open(view);
    deliver(view, frame());
    expect(find(view.container, "spectator-view").dataset.stale).toBe("false");

    act(() => {
      view.handlers().onClose(1006);
    });

    const root = find(view.container, "spectator-view");
    expect(find(view.container, "spectator-status").dataset.status).toBe("disconnected");
    expect(root.dataset.stale).toBe("true");
    const banner = find(view.container, "disconnected-banner");
    expect(text(banner).toLowerCase()).toContain("disconnected");
    expect(text(banner).toLowerCase()).toContain("stale");

    // The trail is live data by definition; a frozen dot would look like a
    // person staring at one product.
    expect(has(view.container, "gaze-dot")).toBe(false);

    view.unmount();
  });

  it("offers a reconnect that opens a new socket", () => {
    const view = render();
    open(view);
    act(() => {
      view.handlers().onClose(1006);
    });

    act(() => {
      find(view.container, "spectator-reconnect").dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });

    expect(view.urls).toHaveLength(2);
    expect(find(view.container, "spectator-status").dataset.status).toBe("connecting");

    view.unmount();
  });

  it("closes the socket when the window goes away", () => {
    const view = render();
    open(view);
    view.unmount();
    expect(view.closes).toBe(1);
  });
});

describe("the fake stream is unmistakable", () => {
  it("shouts when it was asked for with ?fake=1", () => {
    const view = render({ fake: true });
    const banner = find(view.container, "fake-banner");

    expect(text(banner).toUpperCase()).toContain("FAKE");
    expect(text(banner).toLowerCase()).toContain("not a measurement");
    expect(find(view.container, "spectator-view").dataset.fake).toBe("true");
    expect(view.urls[0]).toContain("?fake=1");

    view.unmount();
  });

  it("shouts even if the flag were missing, because the frames say so", () => {
    const view = render();
    expect(has(view.container, "fake-banner")).toBe(false);

    open(view);
    deliver(view, frame({ session_id: "fake-session", prediction_id: "fake-prediction" }));

    expect(has(view.container, "fake-banner")).toBe(true);
    expect(find(view.container, "spectator-view").dataset.fake).toBe("true");

    view.unmount();
  });

  it("leaves a real session unmarked", () => {
    const view = render();
    open(view);
    deliver(view, frame());
    expect(has(view.container, "fake-banner")).toBe(false);
    expect(find(view.container, "spectator-view").dataset.fake).toBe("false");
    view.unmount();
  });
});

describe("the wall clock", () => {
  it("is on screen for the recording", () => {
    const view = render();
    expect(text(find(view.container, "wall-clock"))).toBe("10:32:41");
    view.unmount();
  });
});

describe("relative to ceiling", () => {
  it("appears when a noise ceiling was supplied in the URL", () => {
    const view = render({ ceiling: 0.72 });
    open(view);
    deliver(view, frame({ meaningful: true, spearman: 0.58 }));
    expect(text(find(view.container, "agreement-relative"))).toContain("0.81");
    view.unmount();
  });
});
