import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { act } from "react";
import { createRoot } from "react-dom/client";
import Experiment from "@/dashboard/Experiment";
import { SpectatorView } from "@/spectator/SpectatorView";
import { NO_LOCK } from "@/spectator/lock";
import type { SpectatorHandlers, SpectatorSocketLike } from "@/spectator/SpectatorSocket";
import { WhatIfPanel } from "@/whatif/WhatIfPanel";
import type { WhatIfResponse } from "@/whatif/client";
import { demoAisle, fakeClock } from "./whatifFixture";

/**
 * `#/home` is the only page that explains the product, and it used to be the
 * only page you could not reach from inside the product.
 *
 * CLAUDE.md keeps navigation chrome off the two screens that are being looked
 * at for real - the store, because a shopper is being measured against it, and
 * the spectator, because it is a filmed second monitor - and `launcher/
 * Launcher.tsx` extends that reasoning to explain why the links live on the
 * launcher instead. Every one of those decisions is right; their sum was a
 * launcher nothing linked to, so an operator who typed `#/dashboard` once could
 * not get back to the page that lists the four screens without knowing the URL
 * of the page that lists the four screens.
 *
 * These tests defend the counterweight, and its limit. Four places may link to
 * `#/home`, and they are the four that nobody is being measured on:
 *
 *   * the what-if header - a planning screen that creates no session;
 *   * the dashboard header - read after a session is finished;
 *   * the store's **load-failure** screen - the store did not open, so there is
 *     no measurement to disturb;
 *   * the spectator's **no-session** panel - it is watching nothing.
 *
 * And two places may not, which is the half that is easy to lose: a store that
 * actually loaded, and a spectator that actually has a session, must carry no
 * links at all. `web/tests/hashRouting.test.tsx` asserts the same two things
 * from the router's side; they are repeated here because this change is the one
 * that puts an anchor inside both of those components for the first time.
 *
 * The last test is the constraint the whole demo rests on:
 * `scripts/collect_link.py` hands participants a bare `/?variant=X`, so the
 * default route has to stay the store no matter how many screens link to
 * `#/home`.
 */

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

vi.mock("@react-three/fiber", () => ({
  Canvas: () => null,
  useFrame: () => undefined,
  useThree: () => ({}),
}));
vi.mock("@react-three/drei", () => ({
  useTexture: () => ({}),
}));

/** The `?experiment=` response shape, copied from dashboardExperiment.test.tsx. */
const EXPERIMENT_RESPONSE = {
  experiment_id: "exp_20260904_1a2b3c4d5e6f",
  variant_id: "var_eye_level_shift",
  session_id: "sess_9f8e7d6c5b4a3928",
  n_synth: 10_000,
  seed: 42,
  slot_ids: ["B1S3P1", "B1S3P2"],
  real_attention: { B1S3P1: 0.41, B1S3P2: 0.22 },
  synth_attention: { B1S3P1: 0.37, B1S3P2: 0.28 },
  attention_spearman: 0.482,
  purchase_share_mae: 0.0134,
  real_purchase_share: { SKU_1: 0.6, SKU_2: 0.4 },
  synth_purchase_share: { SKU_1: 0.55, SKU_2: 0.45 },
};

class FakeResizeObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

interface Mounted {
  container: HTMLDivElement;
  unmount: () => void;
}

const mounted: Mounted[] = [];

function mount(node: ReactNode): Mounted {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(node);
  });
  const view: Mounted = {
    container,
    unmount: () => {
      act(() => root.unmount());
      container.remove();
    },
  };
  mounted.push(view);
  return view;
}

function find(container: HTMLElement, testId: string): HTMLElement {
  const element = container.querySelector<HTMLElement>(`[data-testid="${testId}"]`);
  if (element === null) throw new Error(`no element with data-testid="${testId}"`);
  return element;
}

/** The href a link actually carries, unresolved - `#/home` must stay relative. */
function href(container: HTMLElement, testId: string): string | null {
  return find(container, testId).getAttribute("href");
}

/** Flushes the fetch -> res.json() -> setState microtask chain. */
async function settle(): Promise<void> {
  await act(async () => {
    for (let n = 0; n < 10; n += 1) await Promise.resolve();
  });
}

beforeEach(() => {
  vi.resetModules();
  window.localStorage.clear();
  // main.tsx mounts into #root on import, and the tests below that drive it
  // import it dynamically once the environment it reads is in place - the same
  // shape as web/tests/hashRouting.test.tsx.
  document.body.innerHTML = '<div id="root"></div>';
  window.history.replaceState({}, "", "/");
  // recharts' ResponsiveContainer constructs one unconditionally on mount, and
  // jsdom does not implement it.
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
});

afterEach(() => {
  // Unmounted before the stubs go, so anything torn down on the way out still
  // finds the fake it was opened against.
  for (const view of mounted.splice(0)) view.unmount();
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
  window.history.replaceState({}, "", "/");
});

// ---------------------------------------------------------------------------
// The two operator screens
// ---------------------------------------------------------------------------

describe("the operator screens link back to the launcher", () => {
  it("puts an All screens link in the what-if header", () => {
    const view = mount(
      <WhatIfPanel
        planogram={demoAisle()}
        // Deliberately never answered: the header, and the way out of this
        // page, must exist before any run does - a viewer who opened the wrong
        // screen should not have to wait for a simulation to leave it.
        runWhatIf={() => new Promise<WhatIfResponse>(() => {})}
        schedule={fakeClock().schedule}
        reducedMotion
      />,
    );

    expect(href(view.container, "whatif-home-link")).toBe("#/home");
    expect(find(view.container, "whatif-home-link").textContent).toContain("All screens");
  });

  it("puts an All screens link in the dashboard header", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async (): Promise<Response> =>
          ({
            ok: true,
            status: 200,
            statusText: "OK",
            json: async () => EXPERIMENT_RESPONSE,
            text: async () => "",
          }) as Response,
      ),
    );
    window.history.replaceState({}, "", "/?experiment=exp_20260904_1a2b3c4d5e6f");

    const view = mount(<Experiment />);
    await settle();

    // The experiment really did load, so this is the header and not the
    // error screen the next test is about.
    expect(find(view.container, "experiment-id").textContent).toBe(
      EXPERIMENT_RESPONSE.experiment_id,
    );
    expect(href(view.container, "experiment-home-link")).toBe("#/home");
    expect(find(view.container, "experiment-home-link").textContent).toContain(
      "All screens",
    );
  });

  it("puts one on the dashboard's which-experiment error screen too", async () => {
    // The state an operator is actually stuck in: `#/dashboard` with nothing
    // named. The message says the URL needs `?session=` and `?variant=` in it,
    // and the launcher is the page that writes those URLs - so this is the
    // screen where the way back matters most, and it is a different root from
    // the header above.
    const view = mount(<Experiment />);
    await settle();

    expect(find(view.container, "experiment-error").textContent).toContain(
      "Experiment needs either",
    );
    expect(href(view.container, "experiment-home-link")).toBe("#/home");
  });
});

// ---------------------------------------------------------------------------
// The two states that are explicitly not a measurement
// ---------------------------------------------------------------------------

function renderSpectator(sessionId: string | null): Mounted {
  const createSocket = (_url: string, _handlers: SpectatorHandlers): SpectatorSocketLike => ({
    readyState: 1,
    close: () => {},
  });
  return mount(
    <SpectatorView
      sessionId={sessionId}
      fake={false}
      lock={NO_LOCK}
      fetchPrediction={() => Promise.resolve(NO_LOCK)}
      readStoredSession={() => null}
      createSocket={createSocket}
      now={() => 10_000}
      wallClock={() => new Date(2026, 8, 14, 10, 32, 41)}
      screen={{ w: 1_440, h: 900 }}
      fadeIntervalMs={null}
    />,
  );
}

describe("the not-a-measurement states point at the launcher", () => {
  it("offers the launcher from the spectator's no-session panel", () => {
    const view = renderSpectator(null);

    // Inside that panel, not loose on the page: the live spectator canvas is a
    // second monitor beside a person being measured, and it is filmed.
    const panel = find(view.container, "spectator-no-session");
    expect(panel.querySelector('[data-testid="spectator-home-link"]')).not.toBe(null);
    expect(href(view.container, "spectator-home-link")).toBe("#/home");
    // The instruction that was already there has not been replaced by a link.
    expect((panel.textContent ?? "").toLowerCase()).toContain("?session=");
  });

  it("offers the launcher from the store's load-failure screen", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async (): Promise<Response> =>
          ({
            ok: false,
            status: 503,
            statusText: "Service Unavailable",
            json: async () => ({}),
            text: async () => "",
          }) as Response,
      ),
    );

    await boot("/?variant=B&skip_capture=1");

    expect(document.body.textContent).toContain("The store could not load.");
    const link = document.querySelector<HTMLElement>('[data-testid="store-home-link"]');
    expect(link).not.toBe(null);
    expect(link?.getAttribute("href")).toBe("#/home");
  });
});

// ---------------------------------------------------------------------------
// main.tsx, driven the way hashRouting.test.tsx drives it
// ---------------------------------------------------------------------------

const bootedRoots: { unmount(): void }[] = [];

async function boot(url: string): Promise<void> {
  window.history.replaceState({}, "", url);
  await act(async () => {
    bootedRoots.push((await import("@/main")).appRoot);
  });
  await settle();
}

afterEach(() => {
  for (const root of bootedRoots.splice(0)) {
    act(() => root.unmount());
  }
});

describe("navigation chrome stays off the screens being measured", () => {
  it("puts no link on a store that actually loaded", async () => {
    vi.stubGlobal(
      "WebSocket",
      class {
        readonly readyState = 3;
        onclose: ((event: { code: number }) => void) | null = null;
        onerror: (() => void) | null = null;
        onmessage: ((event: { data: string }) => void) | null = null;
        send(): void {}
        close(): void {}
      },
    );
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: unknown, init?: RequestInit): Promise<Response> => {
        const url = String(input);
        const body =
          init?.body === undefined
            ? {}
            : (JSON.parse(String(init.body)) as Record<string, unknown>);
        const payload = url.endsWith("/sessions")
          ? { ...body, prediction_id: "pred-1" }
          : { ...demoAisle() };
        return {
          ok: true,
          status: 200,
          statusText: "OK",
          json: async () => payload,
          text: async () => "",
        } as Response;
      }),
    );

    await boot("/?variant=A&skip_capture=1");

    // The shopper's screen. A menu in the corner is one more thing to look at
    // that is not a product.
    expect(document.body.textContent).not.toContain("The store could not load.");
    expect(document.querySelectorAll("a").length).toBe(0);
  });

  it("puts no link on a spectator that has a session to watch", async () => {
    const view = renderSpectator("sess-1");
    // The injected prediction fetch resolves a microtask after mount; settling
    // it here means the assertion is about the page as it is actually filmed,
    // badge and all, rather than about its first paint.
    await settle();
    expect(view.container.querySelectorAll("a").length).toBe(0);
  });
});

describe("#/home stays an additional route and never the default", () => {
  it("sends the bare participant link straight to the store", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async (): Promise<Response> =>
          ({
            ok: true,
            status: 200,
            statusText: "OK",
            json: async () => ({}),
            text: async () => "",
          }) as Response,
      ),
    );

    // Exactly what `scripts/collect_link.py:build_url` writes: a variant in the
    // query and no fragment at all.
    await boot("/?variant=B");

    expect(document.querySelector('[data-testid="consent-agree"]')).not.toBe(null);
    expect(document.querySelector('[data-testid="launcher"]')).toBe(null);
  });
});
