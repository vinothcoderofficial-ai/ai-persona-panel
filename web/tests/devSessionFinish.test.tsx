import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import demoAisleJson from "../../data/planograms/demo_aisle.json";

/**
 * `?skip_capture=1` end to end, through main.tsx itself.
 *
 * web/tests/sessionFinish.test.tsx proves the scene turns `consent: false` into
 * a `no_consent` finish. This proves the other half: that main.tsx's
 * DEV_SKIP_FIELDS actually reaches the scene, so a development session really
 * is kept out of the real panel instead of merely being able to be.
 *
 * main.tsx renders on import, so it is imported dynamically, once, after the
 * environment it reads (location, #root, fetch, WebSocket) is in place.
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

interface FinishCall {
  url: string;
  body: Record<string, unknown>;
}

let finishCalls: FinishCall[] = [];

beforeEach(() => {
  finishCalls = [];
  vi.resetModules();

  document.body.innerHTML = '<div id="root"></div>';
  window.history.replaceState({}, "", "/?skip_capture=1");

  // The store never opens a real socket in a test; REST carries everything,
  // which SessionSocket already treats as a normal, lossless mode.
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

      if (url.endsWith("/finish")) {
        finishCalls.push({
          url,
          body: JSON.parse(String(init?.body)) as Record<string, unknown>,
        });
      } else if (url.includes("/variants/")) {
        payload = demoAisleJson;
      } else if (url.endsWith("/sessions")) {
        // POST /sessions echoes the document back with its prediction lock.
        payload = {
          ...(JSON.parse(String(init?.body)) as Record<string, unknown>),
          prediction_id: "pred-1",
        };
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
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
  window.history.replaceState({}, "", "/");
});

describe("a ?skip_capture=1 development session", () => {
  it("finishes as no_consent, so it can never enter the real panel", async () => {
    await act(async () => {
      await import("@/main");
    });
    // The store's two fetches, then the state update that mounts the scene.
    await act(async () => {
      for (let n = 0; n < 10; n += 1) await Promise.resolve();
    });

    const button = [...document.querySelectorAll("button")].find(
      (candidate) => candidate.textContent === "Checkout",
    );
    if (button === undefined) {
      throw new Error(
        `the dev store never reached the shop screen; body was: ${document.body.textContent}`,
      );
    }

    await act(async () => {
      button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await act(async () => {
      for (let n = 0; n < 10; n += 1) await Promise.resolve();
    });

    expect(finishCalls).toHaveLength(1);
    expect(finishCalls[0].body.accepted).toBe(false);
    expect(finishCalls[0].body.reject_reason).toBe("no_consent");
    // The verdict is reported, not merely implied: S19's noise dashboard plots
    // the reject reasons and needs the quality block on rejected sessions too.
    expect(finishCalls[0].body.quality).toBeTypeOf("object");
  });

  it("records consent: false on the session it opens", async () => {
    await act(async () => {
      await import("@/main");
    });
    await act(async () => {
      for (let n = 0; n < 10; n += 1) await Promise.resolve();
    });

    const calls = (globalThis.fetch as unknown as { mock: { calls: unknown[][] } }).mock
      .calls;
    const created = calls.find(([url]) => String(url).endsWith("/sessions"));
    if (created === undefined) throw new Error("POST /sessions was never made");

    const body = JSON.parse(
      String((created[1] as RequestInit).body),
    ) as Record<string, unknown>;

    // Nobody sat down and agreed to anything, and the session says so.
    expect(body.consent).toBe(false);
    expect(body.mode).toBe("cursor_only");
  });
});
