import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import type { ReactElement } from "react";
import { createRoot } from "react-dom/client";
import demoAisleJson from "../../data/planograms/demo_aisle.json";
import sessionSchemaJson from "../../schemas/session.schema.json";
import type { Event as ShopperEvent } from "@/contracts/event.schema";
import type { Planogram } from "@/contracts/planogram.schema";
import type { Session } from "@/contracts/session.schema";
import type { EventSink } from "@/capture/SessionSocket";
import {
  MIN_DURATION_S,
  MIN_FIXATION_COVERAGE,
  MIN_STATIONS,
} from "@/capture/SessionGate";
import { PlanogramScene } from "@/store/PlanogramScene";

/**
 * S11 shipped `SessionGate` with 17 passing tests and no caller, so every
 * session finished with `accepted` and `reject_reason` unset - and
 * `scripts/eval.py` (S19), which loads *accepted* sessions, would have found
 * none. This file is the wiring: checkout summarises the session, evaluates the
 * gate, and POSTs the verdict to `/sessions/{id}/finish`.
 *
 * The gate's own rules are tested in web/tests/sessionGate.test.ts and are not
 * re-tested here. What is tested here is that the real verdict reaches the
 * server in a body the server will accept.
 */

// The 3D scene is not what is under test, and jsdom has no WebGL. Canvas
// renders null, so StationController, Bay and the drei texture loaders never
// mount and the only events are the ones checkout itself logs.
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

/** `api/app/routers/sessions.py:_FINISH_FIELDS` — the only fields finish persists. */
const FINISH_FIELDS = ["ended_at", "quality", "accepted", "reject_reason"];

const sessionProperties = (
  sessionSchemaJson as unknown as {
    properties: Record<string, Record<string, unknown>>;
    additionalProperties: boolean;
  }
).properties;

interface FinishCall {
  url: string;
  method: string;
  body: Record<string, unknown>;
}

let finishCalls: FinishCall[] = [];

function ev(
  t_ms: number,
  type: ShopperEvent["type"],
  station_id: string | null,
  payload: Record<string, unknown> = {},
): ShopperEvent {
  return { t_ms, type, station_id, payload };
}

/** An `EventSink` with a scripted history, standing in for the live SessionSocket. */
class FakeSink implements EventSink {
  readonly sessionId = "sess-1";
  private readonly recorded: ShopperEvent[] = [];
  /** The stamp every further `log` gets — the session clock at checkout. */
  t_ms: number;

  constructor(preset: ShopperEvent[], t_ms: number) {
    this.recorded.push(...preset);
    this.t_ms = t_ms;
  }

  get events(): readonly ShopperEvent[] {
    return [...this.recorded];
  }

  log(
    type: ShopperEvent["type"],
    stationId: string | null,
    payload: Record<string, unknown> = {},
  ): void {
    this.recorded.push({ t_ms: this.t_ms, type, station_id: stationId, payload });
  }

  flush(): Promise<void> {
    return Promise.resolve();
  }
}

/**
 * A session that clears every threshold: two stations, one interaction, and
 * 30 s of fixation across a 60 s session (coverage 0.5, against a floor of 0.4).
 */
function goodSession(): ShopperEvent[] {
  return [
    ev(0, "station_enter", "B1"),
    ev(1_000, "hover", "B1", { sku_id: "SKU_CRUNCH_1", slot_id: "B1S3P1" }),
    ev(2_000, "fixation", "B1", {
      x: 640,
      y: 400,
      dur_ms: 15_000,
      slot_id: "B1S3P1",
      shelf_id: "B1S3",
    }),
    ev(30_000, "station_enter", "B2"),
    ev(31_000, "fixation", "B2", {
      x: 700,
      y: 380,
      dur_ms: 15_000,
      slot_id: null,
      shelf_id: "B2S1",
    }),
  ];
}

/**
 * The same session, over in 10 s. Two stations, one interaction and 8 s of
 * fixation, so duration is the only threshold it misses.
 */
function shortSession(): ShopperEvent[] {
  return [
    ev(0, "station_enter", "B1"),
    ev(500, "hover", "B1", { sku_id: "SKU_CRUNCH_1", slot_id: "B1S3P1" }),
    ev(1_000, "fixation", "B1", {
      x: 640,
      y: 400,
      dur_ms: 4_000,
      slot_id: "B1S3P1",
      shelf_id: "B1S3",
    }),
    ev(6_000, "station_enter", "B2"),
    ev(6_500, "fixation", "B2", {
      x: 700,
      y: 380,
      dur_ms: 4_000,
      slot_id: null,
      shelf_id: "B2S1",
    }),
  ];
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

/** Mount the store, press Checkout, and return the body that reached /finish. */
async function checkout(options: {
  consent: boolean;
  mode: Session["mode"];
  events: ShopperEvent[];
  t_ms: number;
}): Promise<Record<string, unknown>> {
  const sink = new FakeSink(options.events, options.t_ms);
  const view = mount(
    <PlanogramScene
      planogram={planogram}
      logger={sink}
      tracker={null}
      consent={options.consent}
      mode={options.mode}
    />,
  );

  const button = [...view.container.querySelectorAll("button")].find(
    (candidate) => candidate.textContent === "Checkout",
  );
  if (button === undefined) throw new Error("the store has no Checkout button");

  await act(async () => {
    button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });

  view.unmount();

  if (finishCalls.length !== 1) {
    throw new Error(`expected exactly one /finish call, got ${finishCalls.length}`);
  }
  return finishCalls[0].body;
}

beforeEach(() => {
  finishCalls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: unknown, init?: RequestInit): Promise<Response> => {
      const url = String(input);
      if (url.endsWith("/finish")) {
        finishCalls.push({
          url,
          method: String(init?.method),
          body: JSON.parse(String(init?.body)) as Record<string, unknown>,
        });
      }
      return {
        ok: true,
        status: 200,
        statusText: "OK",
        json: async () => ({}),
        text: async () => "",
      } as Response;
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

describe("checkout finishes the session with the gate's verdict", () => {
  it("accepts a session that clears every threshold", async () => {
    const body = await checkout({
      consent: true,
      mode: "webcam",
      events: goodSession(),
      t_ms: 60_000,
    });

    expect(body.accepted).toBe(true);
    expect(body.reject_reason).toBeNull();
    expect(body.quality).toEqual({
      fixation_coverage: 0.5,
      stations_visited: 2,
      duration_s: 60,
    });
    // The numbers the gate decided on are the numbers it reported.
    const quality = body.quality as Record<string, number>;
    expect(quality.duration_s).toBeGreaterThanOrEqual(MIN_DURATION_S);
    expect(quality.stations_visited).toBeGreaterThanOrEqual(MIN_STATIONS);
    expect(quality.fixation_coverage).toBeGreaterThanOrEqual(MIN_FIXATION_COVERAGE);
  });

  it("rejects a short session as too_short, and still reports its quality", async () => {
    // Everything else about this session is fine; it simply did not last.
    const body = await checkout({
      consent: true,
      mode: "webcam",
      events: shortSession(),
      t_ms: 10_000,
    });

    expect(body.accepted).toBe(false);
    expect(body.reject_reason).toBe("too_short");
    // Rejecting is not deleting: the quality block is still there for S19's
    // noise dashboard to plot. Only duration_s is under the floor.
    expect(body.quality).toEqual({
      fixation_coverage: 0.8,
      stations_visited: 2,
      duration_s: 10,
    });
    expect((body.quality as Record<string, number>).duration_s).toBeLessThan(
      MIN_DURATION_S,
    );
  });

  it("rejects the ?skip_capture=1 dev session as no_consent", async () => {
    // main.tsx's DEV_SKIP_FIELDS records consent: false, which is the truth -
    // nobody sat down and agreed to anything. The gate must keep that session
    // out of the real panel even though it shopped perfectly well.
    const body = await checkout({
      consent: false,
      mode: "cursor_only",
      events: goodSession(),
      t_ms: 60_000,
    });

    expect(body.accepted).toBe(false);
    expect(body.reject_reason).toBe("no_consent");
  });

  it("does not hold a cursor_only session to the webcam coverage floor", async () => {
    // A cursor-only session has no fixations at all, so its coverage is 0 by
    // construction. Judging it on that would reject every one of them.
    const withoutFixations = goodSession().filter((e) => e.type !== "fixation");
    const body = await checkout({
      consent: true,
      mode: "cursor_only",
      events: withoutFixations,
      t_ms: 60_000,
    });

    expect((body.quality as Record<string, number>).fixation_coverage).toBe(0);
    expect(body.accepted).toBe(true);
    expect(body.reject_reason).toBeNull();
  });
});

describe("the body POST /sessions/{id}/finish is given", () => {
  it("goes to the session's own finish route, as a POST", async () => {
    await checkout({
      consent: true,
      mode: "webcam",
      events: goodSession(),
      t_ms: 60_000,
    });

    expect(finishCalls[0].url).toBe("/api/sessions/sess-1/finish");
    expect(finishCalls[0].method).toBe("POST");
  });

  it("carries exactly the four fields the router persists", async () => {
    const body = await checkout({
      consent: true,
      mode: "webcam",
      events: goodSession(),
      t_ms: 60_000,
    });

    // Anything outside _FINISH_FIELDS is silently dropped by the router;
    // anything outside session.schema.json's properties would make the merged
    // document invalid, because the schema sets additionalProperties: false.
    expect(Object.keys(body).sort()).toEqual([...FINISH_FIELDS].sort());
    for (const key of Object.keys(body)) {
      expect(Object.keys(sessionProperties)).toContain(key);
    }
  });

  it("validates against session.schema.json's shapes", async () => {
    const body = await checkout({
      consent: true,
      mode: "webcam",
      events: goodSession(),
      t_ms: 60_000,
    });

    // ended_at: a date-time string the server can store as one.
    expect(typeof body.ended_at).toBe("string");
    const endedAt = new Date(body.ended_at as string);
    expect(Number.isNaN(endedAt.getTime())).toBe(false);
    expect(endedAt.toISOString()).toBe(body.ended_at);

    // accepted: boolean|null.
    expect(typeof body.accepted).toBe("boolean");

    // reject_reason: one of the schema's own enum values (null included).
    const rejectEnum = sessionProperties.reject_reason.enum as unknown[];
    expect(rejectEnum).toContain(body.reject_reason);

    // quality: exactly the schema's three properties, each in range.
    const qualityProps = (
      sessionProperties.quality as { properties: Record<string, Record<string, number>> }
    ).properties;
    const quality = body.quality as Record<string, number>;
    expect(Object.keys(quality).sort()).toEqual(Object.keys(qualityProps).sort());
    expect(quality.fixation_coverage).toBeGreaterThanOrEqual(
      qualityProps.fixation_coverage.minimum,
    );
    expect(quality.fixation_coverage).toBeLessThanOrEqual(
      qualityProps.fixation_coverage.maximum,
    );
    expect(Number.isInteger(quality.stations_visited)).toBe(true);
    expect(quality.stations_visited).toBeGreaterThanOrEqual(
      qualityProps.stations_visited.minimum,
    );
    expect(quality.duration_s).toBeGreaterThanOrEqual(qualityProps.duration_s.minimum);
  });

  it("reports every rejected session with a reason the schema allows", async () => {
    const rejectEnum = sessionProperties.reject_reason.enum as unknown[];
    const body = await checkout({
      consent: false,
      mode: "webcam",
      events: goodSession(),
      t_ms: 1_000,
    });

    expect(body.accepted).toBe(false);
    expect(rejectEnum).toContain(body.reject_reason);
    expect(body.reject_reason).not.toBeNull();
  });
});
