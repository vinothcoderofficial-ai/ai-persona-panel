import { describe, expect, it } from "vitest";
import type { LiveUpdate } from "@/spectator/liveMessage";
import {
  SpectatorSocket,
  spectatorUrl,
  type SpectatorHandlers,
  type SpectatorSocketLike,
  type SpectatorStatus,
} from "@/spectator/SpectatorSocket";

const MESSAGE = {
  session_id: "s-1",
  t_ms: 1000,
  n_fixations: 3,
  n_cursor_dwells: 0,
  evidence_count: 3,
  evidence_kind: "fixations",
  stations_visited: 1,
  attention: { B1S3P1: 0.5 },
  latest_gaze: { x: 10, y: 20 },
  spearman: 0.1,
  meaningful: false,
  prediction_id: "p-1",
};

interface Harness {
  socket: SpectatorSocket;
  updates: LiveUpdate[];
  statuses: SpectatorStatus[];
  urls: string[];
  closes: number;
  handlers(): SpectatorHandlers;
}

function harness(options: { fake?: boolean; url?: string; throws?: boolean } = {}): Harness {
  const updates: LiveUpdate[] = [];
  const statuses: SpectatorStatus[] = [];
  const urls: string[] = [];
  let captured: SpectatorHandlers | null = null;
  const state = { closes: 0 };

  const socket = new SpectatorSocket("s-1", {
    fake: options.fake,
    url: options.url,
    createSocket: (url, handlers): SpectatorSocketLike => {
      urls.push(url);
      if (options.throws === true) throw new Error("no WebSocket here");
      captured = handlers;
      return {
        readyState: 1,
        close: () => {
          state.closes += 1;
        },
      };
    },
    onUpdate: (update) => updates.push(update),
    onStatus: (status) => statuses.push(status),
  });

  return {
    socket,
    updates,
    statuses,
    urls,
    get closes() {
      return state.closes;
    },
    handlers: () => {
      if (captured === null) throw new Error("the socket factory was never called");
      return captured;
    },
  };
}

describe("spectatorUrl", () => {
  it("targets ws/spectator/{id} on this origin — the dev proxy forwards /ws unrewritten", () => {
    const url = spectatorUrl("abc-123");
    expect(url).toBe(`ws://${window.location.host}/ws/spectator/abc-123`);
  });

  it("encodes a session id that is not URL-safe", () => {
    expect(spectatorUrl("a b/c")).toBe(`ws://${window.location.host}/ws/spectator/a%20b%2Fc`);
  });

  it("asks ws.py for the fake stream with ?fake=1", () => {
    expect(spectatorUrl("abc", { fake: true })).toBe(
      `ws://${window.location.host}/ws/spectator/abc?fake=1`,
    );
  });
});

describe("SpectatorSocket", () => {
  it("is connecting until the socket opens, then live", () => {
    const h = harness();
    expect(h.socket.status).toBe("disconnected");

    h.socket.start();
    expect(h.socket.status).toBe("connecting");

    h.handlers().onOpen();
    expect(h.socket.status).toBe("live");
    expect(h.statuses).toEqual(["connecting", "live"]);
  });

  it("delivers a parsed SPEC 4.7 message", () => {
    const h = harness();
    h.socket.start();
    h.handlers().onOpen();
    h.handlers().onMessage(JSON.stringify(MESSAGE));

    expect(h.updates).toHaveLength(1);
    expect(h.updates[0].n_fixations).toBe(3);
    expect(h.updates[0].fake).toBe(false);
  });

  it("ignores a frame it cannot parse and stays live", () => {
    const h = harness();
    h.socket.start();
    h.handlers().onOpen();
    h.handlers().onMessage("<html>proxy error</html>");

    expect(h.updates).toEqual([]);
    expect(h.socket.status).toBe("live");
  });

  it("reports a dropped socket as disconnected, with the close code", () => {
    const h = harness();
    h.socket.start();
    h.handlers().onOpen();
    h.handlers().onClose(1006);

    expect(h.socket.status).toBe("disconnected");
    expect(h.socket.lastCloseCode).toBe(1006);
    expect(h.statuses).toEqual(["connecting", "live", "disconnected"]);
  });

  it("reports a socket error as disconnected too", () => {
    const h = harness();
    h.socket.start();
    h.handlers().onError();
    expect(h.socket.status).toBe("disconnected");
  });

  it("delivers nothing after stop(), and hands the socket back", () => {
    const h = harness();
    h.socket.start();
    h.handlers().onOpen();
    h.socket.stop();

    expect(h.closes).toBe(1);
    expect(h.socket.status).toBe("disconnected");

    h.handlers().onMessage(JSON.stringify(MESSAGE));
    expect(h.updates).toEqual([]);
  });

  it("survives an environment with no WebSocket at all", () => {
    const h = harness({ throws: true });
    expect(() => h.socket.start()).not.toThrow();
    expect(h.socket.status).toBe("disconnected");
  });

  it("uses an explicit url verbatim, and the fake flag on the default one", () => {
    const explicit = harness({ url: "ws://example.test/feed" });
    explicit.socket.start();
    expect(explicit.urls).toEqual(["ws://example.test/feed"]);

    const fake = harness({ fake: true });
    fake.socket.start();
    expect(fake.urls[0]).toContain("?fake=1");
  });

  it("does not open a second socket when start() is called twice", () => {
    const h = harness();
    h.socket.start();
    h.socket.start();
    expect(h.urls).toHaveLength(1);
  });
});
