import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import demoAisleJson from "../../data/planograms/demo_aisle.json";

/**
 * main.tsx read `window.location.hash` **once**, at module scope, before
 * `createRoot(...).render(...)`. Changing the hash therefore did nothing at all
 * until the page was reloaded: typing `#/whatif` while the store was open, or
 * clicking an `<a href="#/dashboard">`, left the previous screen on screen.
 * These tests drive main.tsx through real `hashchange` events and assert the
 * screen actually changes.
 *
 * The two rules they defend, beyond "routing works":
 *
 *   * A bare URL is still the store. `scripts/collect_link.py` hands
 *     participants `https://host/?variant=X` with no fragment at all, so the
 *     no-hash route may never become a launcher or a menu.
 *   * Re-rendering must not re-open sessions. The store POSTs /sessions on
 *     mount and the server writes a prediction lock before it will accept a
 *     single event (CLAUDE.md), so a remount that nobody asked for costs a
 *     junk session document and a junk lock file in `predictions/`.
 *
 * main.tsx renders on import, so it is imported dynamically, once, after the
 * environment it reads (location, #root, fetch, WebSocket) is in place - the
 * same shape as web/tests/devSessionFinish.test.tsx.
 */

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

let sessionPosts: string[] = [];

/**
 * The roots mounted by `boot()`, so they can be taken down again.
 *
 * jsdom hands every test in this file the same `window`, and `Root` only
 * unregisters its `hashchange` listener when it unmounts. A root left mounted
 * by an earlier test therefore still routes itself on the next hash change, and
 * every store it remounted would POST its own session into `sessionPosts` -
 * which is exactly what the session-counting tests below are measuring. A
 * browser tab has one root; this file has one per test, and must clean up.
 */
const mountedRoots: { unmount(): void }[] = [];

beforeEach(() => {
  sessionPosts = [];
  vi.resetModules();
  window.localStorage.clear();

  document.body.innerHTML = '<div id="root"></div>';
  window.history.replaceState({}, "", "/");

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
      let payload: unknown = {};

      if (url.includes("/variants/")) {
        payload = demoAisleJson;
      } else if (url.endsWith("/sessions")) {
        const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
        sessionPosts.push(String(body.session_id));
        payload = { ...body, prediction_id: `pred-${sessionPosts.length}` };
      }

      return {
        ok: true,
        status: 200,
        statusText: "OK",
        json: async () => payload,
        text: async () => "",
      } as Response;
    }),
  );
});

afterEach(() => {
  // Unmounted before the stubs go, so a socket or a fetch torn down on the way
  // out still finds the fake it was opened against.
  for (const root of mountedRoots.splice(0)) {
    act(() => root.unmount());
  }
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
  window.history.replaceState({}, "", "/");
});

/** Let every queued promise settle, then let React commit what they caused. */
async function settle(): Promise<void> {
  await act(async () => {
    for (let n = 0; n < 10; n += 1) await Promise.resolve();
  });
}

async function boot(url: string): Promise<void> {
  window.history.replaceState({}, "", url);
  await act(async () => {
    mountedRoots.push((await import("@/main")).appRoot);
  });
  await settle();
}

/** What a browser does when someone types a hash or clicks an in-page link. */
async function goTo(hash: string): Promise<void> {
  window.location.hash = hash;
  await act(async () => {
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  });
  await settle();
}

function has(testId: string): boolean {
  return document.querySelector(`[data-testid="${testId}"]`) !== null;
}

function bodyText(): string {
  return document.body.textContent ?? "";
}

describe("a bare URL is the store, before and after any routing change", () => {
  it("renders the capture flow for the participant link shape", async () => {
    await boot("/?variant=B");
    expect(has("consent-agree")).toBe(true);
  });

  it("renders the store, not a launcher, when there is no fragment at all", async () => {
    await boot("/");
    expect(has("launcher")).toBe(false);
    expect(has("consent-agree")).toBe(true);
  });
});

describe("changing the hash changes the screen, with no reload", () => {
  it("swaps the store for the what-if panel", async () => {
    await boot("/");
    expect(has("consent-agree")).toBe(true);

    await goTo("#/whatif");
    expect(has("whatif-panel")).toBe(true);
    expect(has("consent-agree")).toBe(false);
  });

  it("swaps the what-if panel for the spectator screen", async () => {
    await boot("/#/whatif");
    expect(has("whatif-panel")).toBe(true);

    await goTo("#/spectator");
    expect(has("spectator-view")).toBe(true);
    expect(has("whatif-panel")).toBe(false);
  });

  it("reaches the launcher at #/home", async () => {
    await boot("/");
    await goTo("#/home");
    expect(has("launcher")).toBe(true);
    expect(has("consent-agree")).toBe(false);
  });

  it("reaches the dashboard at #/dashboard", async () => {
    await boot("/");
    await goTo("#/dashboard");
    // Nothing was stored and nothing was named, so Experiment says exactly what
    // it has always said. The point here is that it is on screen at all.
    expect(bodyText()).toContain("Experiment needs either");
  });

  it("comes back to the store when the hash is cleared", async () => {
    await boot("/");
    await goTo("#/home");
    expect(has("launcher")).toBe(true);

    await goTo("#/");
    expect(has("consent-agree")).toBe(true);
    expect(has("launcher")).toBe(false);
  });
});

describe("re-rendering does not re-open sessions", () => {
  it("opens exactly one session for one store visit", async () => {
    await boot("/?skip_capture=1");
    expect(sessionPosts).toHaveLength(1);
  });

  it("opens no further session when a hash change lands on the same screen", async () => {
    await boot("/?skip_capture=1");
    expect(sessionPosts).toHaveLength(1);

    // Every one of these is the store route: a fresh POST /sessions here would
    // be a second prediction lock written for one shopper.
    await goTo("#/");
    await goTo("#");
    await goTo("#/store");
    expect(sessionPosts).toHaveLength(1);
  });

  it("opens one more only when the store is actually left and re-entered", async () => {
    await boot("/?skip_capture=1");
    expect(sessionPosts).toHaveLength(1);

    await goTo("#/home");
    expect(sessionPosts).toHaveLength(1);

    // A real second visit to the store is a real second session. That is the
    // one remount this routing change may cause, and it is deliberate.
    await goTo("#/");
    expect(sessionPosts).toHaveLength(2);
    expect(new Set(sessionPosts).size).toBe(2);
  });
});

describe("navigation chrome stays off the measured screens", () => {
  it("puts no links on the shopper's store screen", async () => {
    // CLAUDE.md: the store is what a person being measured looks at. A menu in
    // the corner is one more thing to look at that is not a product.
    await boot("/?skip_capture=1");
    expect(has("launcher")).toBe(false);
    expect(document.querySelectorAll("a").length).toBe(0);
  });

  it("puts no links on the filmed spectator screen", async () => {
    await boot("/#/spectator?session=sess-1");
    expect(has("spectator-view")).toBe(true);
    expect(document.querySelectorAll("a").length).toBe(0);
  });
});
