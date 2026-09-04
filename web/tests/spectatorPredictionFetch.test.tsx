import { afterEach, describe, expect, it } from "vitest";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { SpectatorView } from "@/spectator/SpectatorView";
import type {
  SpectatorHandlers,
  SpectatorSocketLike,
} from "@/spectator/SpectatorSocket";
import {
  NO_LOCK,
  lockFromPredictionEndpoint,
  lockFromQuery,
  type LockView,
} from "@/spectator/lock";

/**
 * `GET /sessions/{id}/prediction` as the spectator's default lock source, so a
 * demo operator only has to open `#/spectator?session=<id>` and the badge and
 * the locked heatmap column fill themselves in.
 *
 * Precedence, highest first (see `lock.ts:resolveLock`):
 *   1. `?lock=<url>`   — a whole lock document, named explicitly
 *   2. `?sha256=` / `?locked_at=` / `?prediction=` — hand-typed badge fields
 *   3. `GET /sessions/{id}/prediction`             — the automatic default
 */

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

/** Exactly what `api/app/routers/sessions.py:get_session_prediction` returns. */
const PREDICTION_RESPONSE = {
  prediction_id: "f2493990-4a5d-479d-902c-eaeb8d91680d",
  sim_run_id: "run-1",
  created_at: "2026-09-04T22:19:33.086Z",
  sha256_prefix: "f3ded23e",
  population_fixation_prob: { B1S3P1: 0.038, B1S3P2: 0.021 },
};

const SHA = "a3f9c0d1e2b3a4958677665544332211aabbccddeeff00112233445566778899";

interface Mounted {
  container: HTMLDivElement;
  unmount: () => void;
  /** The session ids the prediction fetch was called with. */
  fetched: string[];
  handlers(): SpectatorHandlers;
}

interface Options {
  fake?: boolean;
  lock?: LockView;
  /** What the injected prediction fetch resolves to. NO_LOCK stands in for a 404. */
  resolves?: LockView;
  rejects?: boolean;
}

function render(options: Options = {}): Mounted {
  const fetched: string[] = [];
  let captured: SpectatorHandlers | null = null;

  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(
      <SpectatorView
        sessionId="sess-1"
        fake={options.fake ?? false}
        lock={options.lock ?? NO_LOCK}
        createSocket={(_url, handlers): SpectatorSocketLike => {
          captured = handlers;
          return { readyState: 1, close: () => undefined };
        }}
        fetchPrediction={async (sessionId) => {
          fetched.push(sessionId);
          if (options.rejects === true) throw new Error("network down");
          return options.resolves ?? NO_LOCK;
        }}
        now={() => 10_000}
        wallClock={() => new Date(2026, 8, 4, 22, 19, 40)}
        screen={{ w: 1_440, h: 900 }}
        fadeIntervalMs={null}
      />,
    );
  });

  return {
    container,
    fetched,
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

async function settle(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

afterEach(() => {
  document.body.innerHTML = "";
});

describe("?session=<id> alone is enough", () => {
  it("fills the badge and the locked column from the prediction endpoint", async () => {
    const view = render({ resolves: lockFromPredictionEndpoint(PREDICTION_RESPONSE) });

    expect(view.fetched).toEqual(["sess-1"]);
    await settle();

    expect(text(find(view.container, "prediction-hash"))).toBe("f3ded23e");
    expect(text(find(view.container, "prediction-created-at"))).toContain(
      "2026-09-04T22:19:33.086Z",
    );
    expect(find(view.container, "heat-locked-B1S3P1").dataset.value).toBe("0.038");
    expect(find(view.container, "heat-locked-B1S3P2").dataset.value).toBe("0.021");
    expect(has(view.container, "locked-unavailable")).toBe(false);

    view.unmount();
  });

  it("shows the badge before any gaze data — a loading state, then the hash", async () => {
    const view = render({ resolves: lockFromPredictionEndpoint(PREDICTION_RESPONSE) });

    // The fetch has not resolved and the socket has not opened.
    expect(has(view.container, "prediction-hash")).toBe(false);
    expect(text(find(view.container, "prediction-lock-loading")).toLowerCase()).toContain(
      "fetching",
    );
    expect(has(view.container, "prediction-lock-missing")).toBe(false);
    expect(has(view.container, "gaze-dot")).toBe(false);

    await settle();
    expect(text(find(view.container, "prediction-hash"))).toBe("f3ded23e");
    expect(has(view.container, "gaze-dot")).toBe(false);

    view.unmount();
  });
});

describe("a 404 from the endpoint", () => {
  it("keeps the honest 'no lock supplied' messaging and draws no zeros", async () => {
    const view = render(); // the stub resolves NO_LOCK, as a 404 does
    await settle();

    expect(has(view.container, "prediction-hash")).toBe(false);
    expect(has(view.container, "prediction-lock-loading")).toBe(false);
    expect(
      text(find(view.container, "prediction-lock-missing")).length,
    ).toBeGreaterThan(0);

    expect(has(view.container, "locked-unavailable")).toBe(true);
    // Not one zero-valued bar anywhere in the locked column.
    expect(
      view.container.querySelectorAll('[data-testid^="heat-locked-"]'),
    ).toHaveLength(0);

    view.unmount();
  });

  it("does the same when the fetch rejects outright", async () => {
    const view = render({ rejects: true });
    await settle();

    expect(has(view.container, "prediction-lock-missing")).toBe(true);
    expect(has(view.container, "prediction-lock-loading")).toBe(false);
    expect(
      view.container.querySelectorAll('[data-testid^="heat-locked-"]'),
    ).toHaveLength(0);

    view.unmount();
  });
});

describe("explicit URL params override the fetched lock", () => {
  it("prefers a hand-typed sha256 and locked_at", async () => {
    const view = render({
      lock: lockFromQuery(`?sha256=${SHA}&locked_at=2026-09-14T10:32:07.412Z`),
      resolves: lockFromPredictionEndpoint(PREDICTION_RESPONSE),
    });
    await settle();

    expect(text(find(view.container, "prediction-hash"))).toBe("a3f9c0d1");
    expect(text(find(view.container, "prediction-created-at"))).toContain(
      "2026-09-14T10:32:07.412Z",
    );
    // ...and the override does not blank the column beside it: the typed
    // params carry a badge, never a vector, so the fetched one still shows.
    expect(find(view.container, "heat-locked-B1S3P1").dataset.value).toBe("0.038");

    view.unmount();
  });
});

describe("the fake stream fetches nothing", () => {
  it("never asks the API for a lock that cannot exist", async () => {
    const view = render({
      fake: true,
      resolves: lockFromPredictionEndpoint(PREDICTION_RESPONSE),
    });
    await settle();

    expect(view.fetched).toEqual([]);
    expect(has(view.container, "fake-banner")).toBe(true);
    expect(has(view.container, "prediction-hash")).toBe(false);
    expect(has(view.container, "prediction-lock-loading")).toBe(false);
    expect(has(view.container, "prediction-lock-missing")).toBe(true);

    view.unmount();
  });
});
