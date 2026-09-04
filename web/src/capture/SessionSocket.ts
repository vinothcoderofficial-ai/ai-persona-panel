import type { Event as ShopperEvent } from "@/contracts/event.schema";
import { postEvents } from "@/api/client";
import { EventLogger, type EventSender } from "@/capture/EventLogger";

/**
 * The shopper's event stream, over a WebSocket when there is one and over REST
 * when there is not.
 *
 * **It wraps `EventLogger`; it does not replace or duplicate it.** The logger
 * owns the one buffer, the one flush timer and the one retry, and this class
 * supplies it with a `send` that prefers the socket. A batch therefore leaves
 * the buffer exactly once and travels down exactly one channel. That is not an
 * incidental property: an event delivered twice would be folded twice by
 * `api/app/live.py` and summed twice by `analytics/fusion.py`, silently
 * inflating that slot's attention with no error anywhere to notice.
 *
 * **No acks.** `docs/PLAN.md` 13 overrides SPEC M2: "Plain WS + local buffer +
 * REST fallback." Nothing on this socket is numbered, confirmed or
 * retransmitted, and `api/app/routers/ws.py` sends nothing back but an error
 * diagnostic for a batch it could not parse.
 *
 * **A dropped socket is never reopened.** Once the socket closes - dropped
 * mid-session, or refused at connect with 4404 (unknown session) or 4409 (no
 * prediction lock) - every batch from then on goes by REST. No data is lost;
 * only the live spectator feed goes quiet, and reopening would need a
 * reconnect-and-replay policy that PLAN 13 deliberately does not have.
 */

/** SPEC M2: the store streams its batch every 500 ms. */
export const WS_FLUSH_INTERVAL_MS = 500;

/** `WebSocket.OPEN`, written down so this module needs no live WebSocket to test. */
export const SOCKET_OPEN = 1;

/** `api/app/routers/ws.py` refuses a session it has never heard of. */
export const CLOSE_UNKNOWN_SESSION = 4404;

/** ...and one whose prediction was never locked. Events must never precede the lock. */
export const CLOSE_NO_PREDICTION_LOCK = 4409;

/** The slice of `WebSocket` this module uses. */
export interface WebSocketLike {
  readonly readyState: number;
  send(data: string): void;
  close(): void;
}

/**
 * Callbacks, rather than `onclose`/`onerror` properties, so a test double is a
 * three-line object literal and the real `WebSocket` still satisfies
 * `WebSocketLike` structurally.
 */
export interface SocketHandlers {
  onClose(code: number): void;
  onError(): void;
}

export type SocketFactory = (url: string, handlers: SocketHandlers) => WebSocketLike;

export interface SessionSocketOptions {
  /** Defaults to `ws(s)://<host>/ws/session/<id>`; the vite proxy forwards /ws unrewritten. */
  url?: string;
  flushIntervalMs?: number;
  createSocket?: SocketFactory;
  post?: EventSender;
  /** The session clock, shared with the wrapped `EventLogger`. Tests inject it. */
  now?: () => number;
}

/**
 * What the store needs from its event stream.
 *
 * `events` is the session's own record of everything it logged, and it exists
 * for one caller: `SessionGate.summarise` at checkout. The gate has to see the
 * whole session to decide `accepted`/`reject_reason`, and this is the only
 * place every event already passes - tallying them a second time inside
 * `PlanogramScene` would be gate logic written twice, which
 * `capture/SessionGate.ts` is explicitly the single definition of.
 */
export interface EventSink {
  readonly sessionId: string;
  /** Everything logged so far, oldest first. A copy: the caller cannot mutate it. */
  readonly events: readonly ShopperEvent[];
  log(
    type: ShopperEvent["type"],
    stationId: string | null,
    payload?: Record<string, unknown>,
  ): void;
  flush(): Promise<void>;
}

function defaultUrl(sessionId: string): string {
  const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${window.location.host}/ws/session/${encodeURIComponent(sessionId)}`;
}

const openWebSocket: SocketFactory = (url, handlers) => {
  const socket = new WebSocket(url);
  socket.onclose = (event) => handlers.onClose(event.code);
  socket.onerror = () => handlers.onError();
  // The only frame ws.py ever sends back is `{"error": ...}` for a batch it
  // could not parse. That is a rejection notice, not an acknowledgement -
  // nothing waits on it and nothing is retransmitted because of it - but a
  // batch the server refused is a batch this browser has already dropped from
  // its buffer, so it must not vanish without a word during data collection.
  socket.onmessage = (event) => {
    console.warn("ws/session refused a batch:", event.data);
  };
  return socket;
};

export class SessionSocket implements EventSink {
  readonly sessionId: string;

  private readonly url: string;
  private readonly createSocket: SocketFactory;
  private readonly post: EventSender;
  private readonly logger: EventLogger;
  private readonly now: () => number;
  private readonly startedAt: number;
  /** Every event this session logged, for the gate. Never sent anywhere itself. */
  private readonly recorded: ShopperEvent[] = [];

  private socket: WebSocketLike | null = null;
  private closeCode: number | null = null;

  constructor(sessionId: string, options: SessionSocketOptions = {}) {
    this.sessionId = sessionId;
    this.url = options.url ?? defaultUrl(sessionId);
    this.createSocket = options.createSocket ?? openWebSocket;
    this.post = options.post ?? postEvents;
    this.now = options.now ?? (() => performance.now());
    this.startedAt = this.now();
    this.logger = new EventLogger(sessionId, {
      flushIntervalMs: options.flushIntervalMs ?? WS_FLUSH_INTERVAL_MS,
      send: (id, events, opts) => this.deliver(id, events, opts),
      // One clock for the record and the buffer, so the `t_ms` the gate reads
      // is the `t_ms` the server is told. Both bases are taken in this
      // constructor, so they agree to the millisecond t_ms is rounded to.
      now: this.now,
    });
  }

  /**
   * The session's events, oldest first — what `SessionGate.summarise` reads.
   *
   * Filled by `log`, not by delivery: `EventLogger.flush` is a no-op while
   * another flush is in flight, so a record that only filled up on the wire
   * could be missing the `checkout` event at exactly the moment the gate runs.
   * A copy, so the snapshot a caller is holding cannot grow under it and
   * nothing outside can push a phantom event into the record.
   */
  get events(): readonly ShopperEvent[] {
    return [...this.recorded];
  }

  /** Why the socket is gone, for diagnostics. 4404 and 4409 are the server's refusals. */
  get lastCloseCode(): number | null {
    return this.closeCode;
  }

  /** True while batches are going over the socket rather than over REST. */
  get streaming(): boolean {
    return this.socket !== null && this.socket.readyState === SOCKET_OPEN;
  }

  /** Open the socket and start the 500 ms flush. */
  start(): void {
    this.connect();
    this.logger.start();
  }

  log(
    type: ShopperEvent["type"],
    stationId: string | null,
    payload: Record<string, unknown> = {},
  ): void {
    this.logger.log(type, stationId, payload);
    // A separate object, not the one the logger buffered: the record must stay
    // exactly what was logged even though a batch is re-buffered on a failed
    // POST, and it must not be reachable through anything that goes on the wire.
    this.recorded.push({
      t_ms: Math.round(this.now() - this.startedAt),
      type,
      station_id: stationId,
      payload,
    });
  }

  flush(): Promise<void> {
    return this.logger.flush();
  }

  /** Stop the timer, send whatever is buffered, hand the socket back. */
  stop(): void {
    this.logger.stop();
    // Synchronous up to the socket send, so the last batch is queued on the
    // socket before it is closed; a REST fallback completes on its own.
    void this.logger.flush();
    const socket = this.socket;
    this.socket = null;
    socket?.close();
  }

  private connect(): void {
    try {
      this.socket = this.createSocket(this.url, {
        onClose: (code) => {
          this.closeCode = code;
          this.dropSocket();
        },
        onError: () => this.dropSocket(),
      });
    } catch {
      // No socket at all (a blocked URL, no WebSocket in this environment).
      // Not fatal: REST carries the whole session.
      this.socket = null;
    }
  }

  /**
   * The socket is out of the picture from here on. Dropping the reference,
   * rather than re-checking `readyState`, is what stops a socket that closed
   * and somehow reports OPEN again from being written to.
   */
  private dropSocket(): void {
    this.socket = null;
  }

  /**
   * `EventLogger`'s sender. Exactly one of the two channels carries the batch;
   * if `post` throws, the logger keeps the batch and it goes out with the next
   * one, still exactly once.
   */
  private async deliver(
    sessionId: string,
    events: ShopperEvent[],
    opts?: { keepalive?: boolean },
  ): Promise<void> {
    // An unloading page cannot be trusted to flush a WebSocket frame;
    // fetch(keepalive) is the only send the browser promises to finish.
    if (opts?.keepalive !== true) {
      const socket = this.socket;
      if (socket !== null && socket.readyState === SOCKET_OPEN) {
        try {
          socket.send(JSON.stringify({ events }));
          return;
        } catch {
          // It died between the check and the send, so the frame never left.
          // This batch, and every batch after it, goes by REST.
          this.dropSocket();
        }
      }
    }
    await this.post(sessionId, events, opts);
  }
}
