import { hitTest, type ScreenRect } from "@/store/SlotMapper";

/** SPEC M2: a dwell counts once the cursor has held one slot for 300 ms. */
export const CURSOR_DWELL_MIN_MS = 300;

/**
 * The cursor is exact, unlike gaze: "inside one slot rect" means inside it, so
 * dwell does not use the gaze padding.
 */
const HIT_PAD_PX = 0;

/** SPEC 4.3: `cursor_dwell {slot_id, dur_ms}`. */
export type CursorDwell = {
  slot_id: string;
  dur_ms: number;
};

interface OpenDwell {
  slotId: string;
  enteredAt: number;
}

/**
 * Turns a stream of pointer positions into `cursor_dwell` payloads.
 *
 * Pure and clock-free: every method takes the timestamp, so the caller (and the
 * test) decides what time it is. Hit testing goes through the same SlotMapper
 * rectangles the rest of the store uses, and only product slots count —
 * analytics/fusion.py is keyed on the occupied-slot vocabulary, so ad slots and
 * shelf fallbacks must not produce a dwell.
 */
export class CursorTracker {
  private open: OpenDwell | null = null;

  /**
   * Feed one pointer position. Returns the dwell that this sample just ended,
   * if it lasted long enough.
   */
  sample(
    rects: ScreenRect[],
    x: number,
    y: number,
    tMs: number,
  ): CursorDwell | null {
    const hit = hitTest(rects, x, y, HIT_PAD_PX);
    const slotId = hit?.slot_id ?? null;

    if (this.open !== null && this.open.slotId === slotId) return null;

    const completed = this.end(tMs);
    // Re-entering a slot opens a new dwell: fusion.py sums them per slot.
    if (slotId !== null) this.open = { slotId, enteredAt: tMs };
    return completed;
  }

  /** Close any open dwell — the station changed, or the session is over. */
  end(tMs: number): CursorDwell | null {
    const open = this.open;
    this.open = null;
    if (open === null) return null;

    const durMs = Math.round(Math.max(0, tMs - open.enteredAt));
    if (durMs < CURSOR_DWELL_MIN_MS) return null;
    return { slot_id: open.slotId, dur_ms: durMs };
  }
}
