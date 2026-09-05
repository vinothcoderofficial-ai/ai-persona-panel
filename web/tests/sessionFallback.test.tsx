import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot } from "react-dom/client";
import demoAisleJson from "../../data/planograms/demo_aisle.json";
import { SpectatorView } from "@/spectator/SpectatorView";
import type { SpectatorSocketLike } from "@/spectator/SpectatorSocket";
import { readLastSession, rememberSession, type LastSession } from "@/session/lastSession";

/**
 * The session id is made in the browser - `crypto.randomUUID()` in main.tsx -
 * and the two screens that need it are opened in *other windows*: the spectator
 * on the second monitor, the dashboard after the run. Before this there was no
 * way to hand the id across except reading it off a network tab and typing it.
 *
 * The rule these tests defend is not "be convenient", it is **never show one
 * session's data under another session's name**:
 *
 *   * an explicit `?session=` always wins - a fallback may only fill a gap;
 *   * a screen that is following the remembered session says so on screen;
 *   * with nothing remembered, the existing "no session" message stays exactly
 *     as it was. A blank screen or an invented id would both be worse than the
 *     message, and the dashboard's message is asserted here verbatim.
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

const STORED: LastSession = {
  session_id: "3f6b1c2e-9a44-4d0e-8c11-77a0b5e2d913",
  variant_id: "C",
  started_at: "2026-09-14T10:32:07.412Z",
};

interface ExperimentPost {
  variant_id: string;
  session_id: string;
}

let sessionPosts: Record<string, unknown>[] = [];
let experimentPosts: ExperimentPost[] = [];
/** GET /experiments/{id} — the other half of what Experiment can be asked for. */
let experimentGets: string[] = [];

/**
 * The roots mounted by `boot()`. jsdom shares one `window` across a file, and a
 * root left mounted keeps its `hashchange` listener and its in-flight requests,
 * so each test takes down the app it started.
 */
const mountedRoots: { unmount(): void }[] = [];

beforeEach(() => {
  sessionPosts = [];
  experimentPosts = [];
  experimentGets = [];
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

      if (url.endsWith("/experiments") || url.includes("/experiments/")) {
        if (url.endsWith("/experiments")) {
          experimentPosts.push(JSON.parse(String(init?.body)) as ExperimentPost);
        } else {
          experimentGets.push(url);
        }
        // Answered with a failure on purpose: what is under test is which ids
        // reached the server, and a 500 keeps the recharts canvas - which jsdom
        // cannot size - out of a test about routing.
        return {
          ok: false,
          status: 500,
          statusText: "Internal Server Error",
          json: async () => ({}),
          text: async () => "simulation unavailable",
        } as Response;
      }
      if (url.includes("/variants/")) {
        payload = demoAisleJson;
      } else if (url.endsWith("/sessions")) {
        const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
        sessionPosts.push(body);
        payload = { ...body, prediction_id: "pred-1" };
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
  for (const root of mountedRoots.splice(0)) {
    act(() => root.unmount());
  }
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
  window.history.replaceState({}, "", "/");
  window.localStorage.clear();
});

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

function has(testId: string): boolean {
  return document.querySelector(`[data-testid="${testId}"]`) !== null;
}

function bodyText(): string {
  return document.body.textContent ?? "";
}

// ---------------------------------------------------------------------------
// The store leaves the note
// ---------------------------------------------------------------------------

describe("the store records the session it opened", () => {
  it("remembers the id it generated and the variant it opened", async () => {
    await boot("/?variant=C&skip_capture=1");

    expect(sessionPosts).toHaveLength(1);
    const stored = readLastSession();
    if (stored === null) throw new Error("the store recorded no session");
    expect(stored.session_id).toBe(sessionPosts[0].session_id);
    expect(stored.variant_id).toBe("C");
    expect(stored.started_at).toBe(sessionPosts[0].started_at);
  });
});

// ---------------------------------------------------------------------------
// #/spectator
// ---------------------------------------------------------------------------

interface MountedSpectator {
  container: HTMLElement;
  urls: string[];
  unmount: () => void;
}

function renderSpectator(options: {
  sessionId?: string | null;
  stored?: LastSession | null;
}): MountedSpectator {
  const urls: string[] = [];
  const createSocket = (url: string): SpectatorSocketLike => {
    urls.push(url);
    return { readyState: 1, close: () => undefined };
  };

  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(
      <SpectatorView
        sessionId={options.sessionId === undefined ? null : options.sessionId}
        readStoredSession={() => options.stored ?? null}
        createSocket={createSocket}
        fetchPrediction={async () => {
          throw new Error("no API in this test");
        }}
        now={() => 10_000}
        fadeIntervalMs={null}
      />,
    );
  });

  return {
    container,
    urls,
    unmount: () => {
      act(() => root.unmount());
      container.remove();
    },
  };
}

function textOf(container: HTMLElement, testId: string): string {
  const element = container.querySelector<HTMLElement>(`[data-testid="${testId}"]`);
  if (element === null) throw new Error(`no element with data-testid="${testId}"`);
  return element.textContent ?? "";
}

describe("the spectator screen finds the running session", () => {
  it("watches the last session this browser opened when the URL names none", () => {
    const view = renderSpectator({ stored: STORED });
    expect(view.urls).toEqual([
      `ws://${window.location.host}/ws/spectator/${STORED.session_id}`,
    ]);
    view.unmount();
  });

  it("says on screen that it is following, not obeying", () => {
    const view = renderSpectator({ stored: STORED });
    const note = textOf(view.container, "spectator-followed-session");
    expect(note).toContain(STORED.session_id);
    expect(note.toLowerCase()).toContain("last session");
    view.unmount();
  });

  it("lets an explicitly named session win over the remembered one", () => {
    const view = renderSpectator({ sessionId: "named-by-hand", stored: STORED });
    expect(view.urls).toEqual([`ws://${window.location.host}/ws/spectator/named-by-hand`]);
    // Nothing was substituted, so there is nothing to disclose.
    expect(view.container.querySelector('[data-testid="spectator-followed-session"]')).toBe(
      null,
    );
    view.unmount();
  });

  it("keeps the old no-session message when nothing is remembered", () => {
    const view = renderSpectator({ stored: null });
    expect(view.urls).toEqual([]);
    expect(textOf(view.container, "spectator-no-session").toLowerCase()).toContain(
      "?session=",
    );
    view.unmount();
  });
});

// ---------------------------------------------------------------------------
// #/dashboard
// ---------------------------------------------------------------------------

describe("the dashboard finds the running session", () => {
  it("runs the experiment for the remembered session and variant", async () => {
    rememberSession(STORED);
    await boot("/#/dashboard");

    expect(experimentPosts).toEqual([
      { variant_id: STORED.variant_id, session_id: STORED.session_id },
    ]);
  });

  it("says on screen which session it followed", async () => {
    rememberSession(STORED);
    await boot("/#/dashboard");

    expect(has("dashboard-followed-session")).toBe(true);
    expect(bodyText()).toContain(STORED.session_id);
    expect(bodyText().toLowerCase()).toContain("last session");
  });

  it("writes the ids it adopted into the address bar, so the URL is not a lie", async () => {
    rememberSession(STORED);
    await boot("/#/dashboard");

    const params = new URLSearchParams(window.location.search);
    expect(params.get("session")).toBe(STORED.session_id);
    expect(params.get("variant")).toBe(STORED.variant_id);
    expect(window.location.hash).toBe("#/dashboard");
  });

  it("lets an explicitly named session win over the remembered one", async () => {
    rememberSession(STORED);
    await boot("/?session=named-by-hand&variant=A#/dashboard");

    expect(experimentPosts).toEqual([
      { variant_id: "A", session_id: "named-by-hand" },
    ]);
    expect(has("dashboard-followed-session")).toBe(false);
  });

  it("keeps Experiment's own message when there is nothing to show", async () => {
    await boot("/#/dashboard");

    expect(experimentPosts).toEqual([]);
    expect(bodyText()).toContain(
      "Could not load experiment: Experiment needs either ?experiment=<id> or " +
        "?session=<id>&variant=<id> in the URL.",
    );
    expect(has("dashboard-followed-session")).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Params on either side of the `#`
// ---------------------------------------------------------------------------

/**
 * `#/dashboard?session=<id>&variant=<id>` is the spelling README's screens
 * table documents, and the spelling anyone carries over from the spectator URL
 * printed directly above it — `SpectatorView` merges both sides of the hash and
 * lets the hash win (`session/urlParams.ts`). `Experiment` reads `location.search`
 * only, so before this the hash spelling resolved to *a different session*: the
 * ids in the URL were ignored, the stored fallback was loaded in their place,
 * and the note asserted that no session had been named. That is the one thing
 * this screen must never do, and it would have done it on camera.
 *
 * The rule, matching the spectator's: params may be written on either side of
 * the `#`, and the hash wins on a collision, because it is the half that names
 * the route.
 */
describe("the dashboard reads params from either side of the hash", () => {
  it("obeys a session named after the hash, rather than following the stored one", async () => {
    rememberSession(STORED);
    await boot("/#/dashboard?session=f76c3037-46c7-4bc0-9c6c-5ddf7b6c1539&variant=A");

    expect(experimentPosts).toEqual([
      { variant_id: "A", session_id: "f76c3037-46c7-4bc0-9c6c-5ddf7b6c1539" },
    ]);
    // A session *was* named, so a note claiming otherwise would be a false
    // statement about which data is on screen.
    expect(has("dashboard-followed-session")).toBe(false);
  });

  it("lets the hash win over the search, the way the spectator screen does", async () => {
    rememberSession(STORED);
    await boot("/?session=before&variant=A#/dashboard?session=after&variant=B");

    expect(experimentPosts).toEqual([{ variant_id: "B", session_id: "after" }]);
    expect(has("dashboard-followed-session")).toBe(false);
  });

  it("promotes them into location.search, where Experiment can see them", async () => {
    await boot("/#/dashboard?session=after&variant=B");

    // Experiment reads location.search and nothing else. Promoting is also what
    // makes the URL survive a reload and a copy into another window.
    const params = new URLSearchParams(window.location.search);
    expect(params.get("session")).toBe("after");
    expect(params.get("variant")).toBe("B");
    // The fragment the operator typed is left exactly as typed.
    expect(window.location.hash).toBe("#/dashboard?session=after&variant=B");
  });

  it("honours ?experiment= written after the hash too", async () => {
    rememberSession(STORED);
    await boot("/#/dashboard?experiment=eval-1");

    expect(experimentGets).toEqual(["/api/experiments/eval-1"]);
    expect(experimentPosts).toEqual([]);
    expect(has("dashboard-followed-session")).toBe(false);
  });
});

describe("the store reads params from either side of the hash", () => {
  it("opens the variant named after the hash, not variant A by default", async () => {
    // A silently wrong arm is worse here than anywhere else on the site: the
    // session is collected, accepted, and filed under a variant nobody chose.
    await boot("/#/?variant=D&skip_capture=1");

    expect(sessionPosts).toHaveLength(1);
    expect(sessionPosts[0].variant_id).toBe("D");
    expect(sessionPosts[0].consent).toBe(false);
    expect(sessionPosts[0].mode).toBe("cursor_only");
  });

  it("still reads the participant-link spelling, which has no hash at all", async () => {
    await boot("/?variant=C&skip_capture=1");

    expect(sessionPosts).toHaveLength(1);
    expect(sessionPosts[0].variant_id).toBe("C");
  });
});
