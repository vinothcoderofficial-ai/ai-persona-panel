import type { Event as ShopperEvent } from "@/contracts/event.schema";
import { postEvents } from "@/api/client";

/** SPEC M1: the store posts its event batch every 2 s. */
export const FLUSH_INTERVAL_MS = 2000;

export type EventSender = (
  sessionId: string,
  events: ShopperEvent[],
  opts?: { keepalive?: boolean },
) => Promise<void>;

export interface EventLoggerOptions {
  flushIntervalMs?: number;
  send?: EventSender;
  now?: () => number;
}

/**
 * Buffers shopper events and POSTs them in batches.
 *
 * A failed batch stays in the buffer and goes out with the next one, so nothing
 * is lost while the API is briefly unreachable. The WebSocket path and its REST
 * fallback arrive in S11.
 */
export class EventLogger {
  readonly sessionId: string;

  private readonly flushIntervalMs: number;
  private readonly send: EventSender;
  private readonly now: () => number;
  private readonly startedAt: number;

  private buffer: ShopperEvent[] = [];
  private timer: ReturnType<typeof setInterval> | null = null;
  private inFlight = false;

  constructor(sessionId: string, options: EventLoggerOptions = {}) {
    this.sessionId = sessionId;
    this.flushIntervalMs = options.flushIntervalMs ?? FLUSH_INTERVAL_MS;
    this.send = options.send ?? postEvents;
    this.now = options.now ?? (() => performance.now());
    this.startedAt = this.now();
  }

  /** `t_ms` is whole milliseconds since the session started, not a wall clock. */
  log(
    type: ShopperEvent["type"],
    stationId: string | null,
    payload: Record<string, unknown> = {},
  ): void {
    this.buffer.push({
      t_ms: Math.round(this.now() - this.startedAt),
      type,
      station_id: stationId,
      payload,
    });
  }

  get pending(): number {
    return this.buffer.length;
  }

  start(): void {
    if (this.timer !== null) return;
    this.timer = setInterval(() => void this.flush(), this.flushIntervalMs);
    window.addEventListener("beforeunload", this.onUnload);
  }

  stop(): void {
    if (this.timer !== null) {
      clearInterval(this.timer);
      this.timer = null;
    }
    window.removeEventListener("beforeunload", this.onUnload);
  }

  async flush(keepalive = false): Promise<void> {
    if (this.inFlight || this.buffer.length === 0) return;
    const batch = this.buffer;
    this.buffer = [];
    this.inFlight = true;
    try {
      await this.send(this.sessionId, batch, { keepalive });
    } catch {
      // Keep the batch: it goes out again with whatever arrives next.
      this.buffer = batch.concat(this.buffer);
    } finally {
      this.inFlight = false;
    }
  }

  private readonly onUnload = (): void => {
    void this.flush(true);
  };
}
