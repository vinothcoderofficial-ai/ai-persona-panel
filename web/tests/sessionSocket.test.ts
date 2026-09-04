import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Event as ShopperEvent } from "@/contracts/event.schema";
import {
  SOCKET_OPEN,
  SessionSocket,
  WS_FLUSH_INTERVAL_MS,
  type SocketHandlers,
  type WebSocketLike,
} from "@/capture/SessionSocket";

/**
 * PLAN 13 cut the ack protocol: plain WS, a local buffer, and a REST fallback.
 * Everything below is therefore about one property - every event reaches the
 * server down exactly one of the two channels, exactly once - because a
 * double-sent event silently inflates every attention number downstream, and a
 * dropped one silently deflates it.
 */

const CONNECTING = 0;
const CLOSED = 3;

interface Fake {
  readonly url: string;
  readonly sent: string[];
  readonly socket: WebSocketLike;
  readonly closedByClient: boolean;
  open(): void;
  drop(code: number): void;
  fail(): void;
  breakSending(): void;
}

function fakeSocket(url: string, handlers: SocketHandlers): Fake {
  const sent: string[] = [];
  let readyState = CONNECTING;
  let broken = false;
  let closedByClient = false;

  const socket: WebSocketLike = {
    get readyState() {
      return readyState;
    },
    send(data: string) {
      if (broken) throw new Error("socket died between the check and the send");
      sent.push(data);
    },
    close() {
      closedByClient = true;
      readyState = CLOSED;
    },
  };

  return {
    url,
    sent,
    socket,
    get closedByClient() {
      return closedByClient;
    },
    open() {
      readyState = SOCKET_OPEN;
    },
    drop(code: number) {
      readyState = CLOSED;
      handlers.onClose(code);
    },
    fail() {
      handlers.onError();
      readyState = CLOSED;
      handlers.onClose(1006);
    },
    breakSending() {
      broken = true;
    },
  };
}

type Harness = ReturnType<typeof harness>;

function harness(options: { post?: (events: ShopperEvent[]) => Promise<void> } = {}) {
  const fakes: Fake[] = [];
  const posted: { events: ShopperEvent[]; keepalive: boolean }[] = [];

  const post = vi.fn(
    async (
      _sessionId: string,
      events: ShopperEvent[],
      opts?: { keepalive?: boolean },
    ): Promise<void> => {
      if (options.post !== undefined) await options.post(events);
      posted.push({ events, keepalive: opts?.keepalive === true });
    },
  );

  const sink = new SessionSocket("s-1", {
    createSocket: (url, handlers) => {
      const fake = fakeSocket(url, handlers);
      fakes.push(fake);
      return fake.socket;
    },
    post,
  });

  return { sink, fakes, posted, post };
}

/** The `n` of every event that reached the server, whichever channel carried it. */
function delivered(h: Harness): number[] {
  const overSocket = h.fakes.flatMap((fake) =>
    fake.sent.flatMap((frame) => (JSON.parse(frame) as { events: ShopperEvent[] }).events),
  );
  const overRest = h.posted.flatMap((batch) => batch.events);
  return [...overSocket, ...overRest].map((event) => event.payload.n as number);
}

function overSocket(h: Harness): number[] {
  return h.fakes
    .flatMap((fake) =>
      fake.sent.flatMap(
        (frame) => (JSON.parse(frame) as { events: ShopperEvent[] }).events,
      ),
    )
    .map((event) => event.payload.n as number);
}

function overRest(h: Harness): number[] {
  return h.posted.flatMap((batch) => batch.events).map((event) => event.payload.n as number);
}

let live: SessionSocket[] = [];

function start(h: Harness): Harness {
  h.sink.start();
  live.push(h.sink);
  return h;
}

beforeEach(() => {
  vi.useFakeTimers();
  live = [];
});

afterEach(() => {
  for (const sink of live) sink.stop();
  vi.useRealTimers();
});

describe("the streaming cadence", () => {
  it("flushes every 500 ms", () => {
    expect(WS_FLUSH_INTERVAL_MS).toBe(500);
  });

  it("sends nothing before the interval and one batch on it", async () => {
    const h = start(harness());
    h.fakes[0].open();
    h.sink.log("hover", "B1", { n: 1 });

    await vi.advanceTimersByTimeAsync(WS_FLUSH_INTERVAL_MS - 1);
    expect(h.fakes[0].sent).toHaveLength(0);

    await vi.advanceTimersByTimeAsync(1);
    expect(h.fakes[0].sent).toHaveLength(1);
    expect(h.post).not.toHaveBeenCalled();
  });

  it("connects to ws/session/{id} and sends the batch shape ws.py parses", async () => {
    const h = start(harness());
    expect(h.fakes[0].url).toContain("/ws/session/s-1");
    expect(h.fakes[0].url.startsWith("ws:") || h.fakes[0].url.startsWith("wss:")).toBe(
      true,
    );

    h.fakes[0].open();
    h.sink.log("fixation", "B1", { n: 1, x: 10, y: 20, dur_ms: 200 });
    await vi.advanceTimersByTimeAsync(WS_FLUSH_INTERVAL_MS);

    const frame = JSON.parse(h.fakes[0].sent[0]) as { events: ShopperEvent[] };
    expect(Object.keys(frame)).toEqual(["events"]);
    expect(frame.events[0].type).toBe("fixation");
    expect(frame.events[0].station_id).toBe("B1");
  });

  it("batches everything logged inside one interval into one frame", async () => {
    const h = start(harness());
    h.fakes[0].open();
    for (let n = 1; n <= 5; n += 1) h.sink.log("gaze", "B1", { n });

    await vi.advanceTimersByTimeAsync(WS_FLUSH_INTERVAL_MS);
    expect(h.fakes[0].sent).toHaveLength(1);
    expect(delivered(h)).toEqual([1, 2, 3, 4, 5]);
  });
});

describe("a socket that drops mid-session", () => {
  it("loses zero events - the rest arrive over REST", async () => {
    const h = start(harness());
    h.fakes[0].open();

    h.sink.log("hover", "B1", { n: 1 });
    await vi.advanceTimersByTimeAsync(WS_FLUSH_INTERVAL_MS);
    expect(overSocket(h)).toEqual([1]);

    h.fakes[0].drop(1006);

    h.sink.log("pickup", "B1", { n: 2 });
    h.sink.log("add_to_cart", "B1", { n: 3 });
    await vi.advanceTimersByTimeAsync(WS_FLUSH_INTERVAL_MS);

    expect(delivered(h)).toEqual([1, 2, 3]);
    expect(overRest(h)).toEqual([2, 3]);
  });

  it("never sends the same event down both channels", async () => {
    const h = start(harness());
    h.fakes[0].open();

    for (let n = 1; n <= 3; n += 1) h.sink.log("gaze", "B1", { n });
    await vi.advanceTimersByTimeAsync(WS_FLUSH_INTERVAL_MS);

    h.fakes[0].drop(1006);

    for (let n = 4; n <= 6; n += 1) h.sink.log("gaze", "B1", { n });
    await vi.advanceTimersByTimeAsync(WS_FLUSH_INTERVAL_MS * 3);

    const all = delivered(h);
    expect(all).toEqual([1, 2, 3, 4, 5, 6]);
    expect(new Set(all).size).toBe(all.length);
  });

  it("does not go back to a socket that already closed", async () => {
    const h = start(harness());
    h.fakes[0].open();
    h.fakes[0].drop(1006);
    // A closed socket that starts reporting OPEN again is not a live socket.
    h.fakes[0].open();

    h.sink.log("hover", "B1", { n: 1 });
    await vi.advanceTimersByTimeAsync(WS_FLUSH_INTERVAL_MS);

    expect(overSocket(h)).toEqual([]);
    expect(overRest(h)).toEqual([1]);
  });

  it("falls back when the socket errors", async () => {
    const h = start(harness());
    h.fakes[0].open();
    h.fakes[0].fail();

    h.sink.log("hover", "B1", { n: 1 });
    await vi.advanceTimersByTimeAsync(WS_FLUSH_INTERVAL_MS);

    expect(overRest(h)).toEqual([1]);
  });

  it("falls back when send throws after the readyState check passed", async () => {
    const h = start(harness());
    h.fakes[0].open();
    h.fakes[0].breakSending();

    h.sink.log("hover", "B1", { n: 1 });
    await vi.advanceTimersByTimeAsync(WS_FLUSH_INTERVAL_MS);
    // The frame never left, so the batch is not lost and not duplicated.
    expect(delivered(h)).toEqual([1]);
    expect(overRest(h)).toEqual([1]);

    h.sink.log("hover", "B1", { n: 2 });
    await vi.advanceTimersByTimeAsync(WS_FLUSH_INTERVAL_MS);
    expect(delivered(h)).toEqual([1, 2]);
  });
});

describe("a socket the server refuses", () => {
  it("falls back to REST on 4409 - no prediction lock", async () => {
    const h = start(harness());
    // The server closes before accepting: the batch must not be dropped just
    // because the live spectator feed is unavailable.
    h.fakes[0].drop(4409);

    h.sink.log("hover", "B1", { n: 1 });
    await vi.advanceTimersByTimeAsync(WS_FLUSH_INTERVAL_MS);

    expect(overSocket(h)).toEqual([]);
    expect(overRest(h)).toEqual([1]);
    expect(h.sink.lastCloseCode).toBe(4409);
  });

  it("falls back to REST on 4404 - unknown session", async () => {
    const h = start(harness());
    h.fakes[0].drop(4404);

    h.sink.log("hover", "B1", { n: 1 });
    await vi.advanceTimersByTimeAsync(WS_FLUSH_INTERVAL_MS);

    expect(overRest(h)).toEqual([1]);
    expect(h.sink.lastCloseCode).toBe(4404);
  });

  it("uses REST while the socket is still connecting", async () => {
    const h = start(harness());
    // Never opened. Nothing waits on the handshake; the events go now.
    h.sink.log("hover", "B1", { n: 1 });
    await vi.advanceTimersByTimeAsync(WS_FLUSH_INTERVAL_MS);

    expect(overRest(h)).toEqual([1]);
  });
});

describe("the local buffer", () => {
  it("keeps a batch a failed POST could not deliver, and sends it once", async () => {
    let attempts = 0;
    const h = start(
      harness({
        post: async () => {
          attempts += 1;
          if (attempts === 1) throw new Error("network down");
        },
      }),
    );

    h.sink.log("hover", "B1", { n: 1 });
    await vi.advanceTimersByTimeAsync(WS_FLUSH_INTERVAL_MS);
    expect(delivered(h)).toEqual([]);

    h.sink.log("pickup", "B1", { n: 2 });
    await vi.advanceTimersByTimeAsync(WS_FLUSH_INTERVAL_MS);

    const all = delivered(h);
    expect(all).toEqual([1, 2]);
    expect(new Set(all).size).toBe(all.length);
  });

  it("hands an unloading page to REST with keepalive, not to the socket", async () => {
    const h = start(harness());
    h.fakes[0].open();
    h.sink.log("checkout", "B1", { n: 1 });

    window.dispatchEvent(new Event("beforeunload"));
    await vi.advanceTimersByTimeAsync(0);

    // A WebSocket frame queued during unload is not guaranteed to leave the
    // machine; fetch(keepalive) is the only send the browser promises.
    expect(overSocket(h)).toEqual([]);
    expect(h.posted).toHaveLength(1);
    expect(h.posted[0].keepalive).toBe(true);
  });

  it("flushes what is left and closes the socket on stop", async () => {
    const h = harness();
    h.sink.start();
    h.fakes[0].open();
    h.sink.log("checkout", "B1", { n: 1 });

    h.sink.stop();
    await vi.advanceTimersByTimeAsync(0);

    expect(delivered(h)).toEqual([1]);
    expect(h.fakes[0].closedByClient).toBe(true);

    // The timer is gone: nothing else is sent after stop.
    h.sink.log("hover", "B1", { n: 2 });
    await vi.advanceTimersByTimeAsync(WS_FLUSH_INTERVAL_MS * 3);
    expect(delivered(h)).toEqual([1]);
  });

  it("awaits delivery when the caller flushes explicitly", async () => {
    const h = start(harness());
    h.fakes[0].drop(1006);
    h.sink.log("checkout", "B1", { n: 1 });

    await h.sink.flush();
    expect(overRest(h)).toEqual([1]);
  });
});
