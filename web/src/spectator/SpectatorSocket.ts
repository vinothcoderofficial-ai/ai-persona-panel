import { parseLiveUpdate, type LiveUpdate } from "@/spectator/liveMessage";

/**
 * The spectator's read-only feed: `ws/spectator/{id}`.
 *
 * **Deliberately not `capture/SessionSocket`.** That class is the shopper's
 * *ingest* path - it buffers events, falls back to REST and must never lose a
 * batch. This is the opposite direction and the opposite guarantee: it only
 * receives, it may drop frames without consequence, and a spectator window that
 * dies must not be able to disturb the person being measured. Entangling the
 * two would put a second monitor on the critical path of a measurement.
 *
 * The socket is created through an injected factory, the way
 * `capture/SessionSocket` and `capture/GazeTracker` are, so the whole thing is
 * driveable in jsdom with no server.
 */

/** The slice of `WebSocket` this module needs. Note: no `send`. */
export interface SpectatorSocketLike {
  readonly readyState: number;
  close(): void;
}

export interface SpectatorHandlers {
  onOpen(): void;
  onMessage(data: string): void;
  onClose(code: number): void;
  onError(): void;
}

export type SpectatorSocketFactory = (
  url: string,
  handlers: SpectatorHandlers,
) => SpectatorSocketLike;

/**
 * `connecting` - asked for, nothing received yet.
 * `live` - the socket is open; what is on screen is current.
 * `disconnected` - dropped, refused, or never opened. Whatever is on screen is
 * stale, and the view says so rather than letting a frozen heatmap pass for a
 * live one on camera.
 */
export type SpectatorStatus = "connecting" | "live" | "disconnected";

export interface SpectatorSocketOptions {
  /** Defaults to `ws(s)://<host>/ws/spectator/<id>`; the dev proxy forwards /ws unrewritten. */
  url?: string;
  /** `?fake=1` asks ws.py for its synthetic demo stream. Never a measurement. */
  fake?: boolean;
  createSocket?: SpectatorSocketFactory;
  onUpdate(update: LiveUpdate): void;
  onStatus(status: SpectatorStatus): void;
}

export function spectatorUrl(sessionId: string, options: { fake?: boolean } = {}): string {
  const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
  const path = `/ws/spectator/${encodeURIComponent(sessionId)}`;
  const query = options.fake === true ? "?fake=1" : "";
  return `${scheme}//${window.location.host}${path}${query}`;
}

const openWebSocket: SpectatorSocketFactory = (url, handlers) => {
  const socket = new WebSocket(url);
  socket.onopen = () => handlers.onOpen();
  socket.onmessage = (event) => {
    if (typeof event.data === "string") handlers.onMessage(event.data);
  };
  socket.onclose = (event) => handlers.onClose(event.code);
  socket.onerror = () => handlers.onError();
  return socket;
};

export class SpectatorSocket {
  private readonly url: string;
  private readonly createSocket: SpectatorSocketFactory;
  private readonly onUpdate: (update: LiveUpdate) => void;
  private readonly onStatus: (status: SpectatorStatus) => void;

  private socket: SpectatorSocketLike | null = null;
  private state: SpectatorStatus = "disconnected";
  private closeCode: number | null = null;
  /**
   * Frames from a socket this object has already let go are ignored. A
   * `close` handler can still fire after `stop()`, and a stale frame arriving
   * after the window was told the feed is dead would put a live-looking number
   * back on a screen that has just announced it is stale.
   */
  private generation = 0;

  constructor(sessionId: string, options: SpectatorSocketOptions) {
    this.url = options.url ?? spectatorUrl(sessionId, { fake: options.fake });
    this.createSocket = options.createSocket ?? openWebSocket;
    this.onUpdate = options.onUpdate;
    this.onStatus = options.onStatus;
  }

  get status(): SpectatorStatus {
    return this.state;
  }

  /** Why the feed is gone, for the on-screen diagnostic. */
  get lastCloseCode(): number | null {
    return this.closeCode;
  }

  start(): void {
    if (this.socket !== null) return;
    const generation = ++this.generation;
    const live = () => generation === this.generation;

    this.closeCode = null;
    this.setStatus("connecting");
    try {
      this.socket = this.createSocket(this.url, {
        onOpen: () => {
          if (live()) this.setStatus("live");
        },
        onMessage: (data) => {
          if (!live()) return;
          const update = parseLiveUpdate(data);
          // A frame that is not a SPEC 4.7 message is dropped, not half-drawn.
          if (update !== null) this.onUpdate(update);
        },
        onClose: (code) => {
          if (!live()) return;
          this.closeCode = code;
          this.socket = null;
          this.setStatus("disconnected");
        },
        onError: () => {
          if (!live()) return;
          this.socket = null;
          this.setStatus("disconnected");
        },
      });
    } catch {
      // No WebSocket at all (a blocked URL, or an environment without one).
      // The view shows "disconnected"; nothing about the shopper is affected.
      this.socket = null;
      this.setStatus("disconnected");
    }
  }

  stop(): void {
    this.generation += 1;
    const socket = this.socket;
    this.socket = null;
    this.state = "disconnected";
    socket?.close();
  }

  private setStatus(status: SpectatorStatus): void {
    if (this.state === status) return;
    this.state = status;
    this.onStatus(status);
  }
}
