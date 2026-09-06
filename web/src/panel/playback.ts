import type { AiTraceTurn } from "@/ai/client";

/**
 * Stepping through one synthetic shopper's trip, one turn at a time.
 *
 * Pure and separate from the screen, because what has to be right here is
 * arithmetic rather than layout: what is in the cart at step seven, which bay
 * the shopper is standing at, whether the trip is over. Getting any of those
 * wrong shows a persona buying something it did not buy, on the one screen
 * whose whole job is to be believable evidence that the synthetic panel shops
 * the same store the real panel does.
 *
 * `index` is the turn being *shown*, and its effects have already happened —
 * so the add on turn three is in the cart at index two. That is what a viewer
 * expects from a player: the caption and the state describe the same moment.
 */

/** The actions `sim/slow_agent.py` allows that put something in the cart. */
const PURCHASE_ACTIONS = new Set(["add_to_cart"]);

export interface PlaybackState {
  /** The turn on screen, clamped into range. */
  index: number;
  turn: AiTraceTurn | null;
  /** The bay the shopper is at, or null for a trip with no turns. */
  stationId: string | null;
  /**
   * The slot this turn is about, or null. `next_station` and `checkout` are
   * untargeted, and carrying the previous slot through them would show the
   * shopper still staring at a shelf it has walked away from.
   */
  targetSlotId: string | null;
  action: string | null;
  reason: string | null;
  /**
   * Every `add_to_cart` up to and including this turn, in order, repeats kept.
   * Two of the same pack is two lines, because that is what happened.
   */
  cartSlotIds: string[];
  done: boolean;
}

const EMPTY: PlaybackState = {
  index: 0,
  turn: null,
  stationId: null,
  targetSlotId: null,
  action: null,
  reason: null,
  cartSlotIds: [],
  // A trip with no turns is over before it starts. Reporting it as still
  // running would leave a player spinning on nothing.
  done: true,
};

/**
 * The state of the trip at `index`, with the index clamped into range.
 *
 * Clamped rather than rejected: a player's "next" at the end and "previous" at
 * the start are ordinary, and the honest answer to both is the turn at that
 * end of the trip.
 */
export function stateAt(turns: AiTraceTurn[], index: number): PlaybackState {
  if (turns.length === 0) return EMPTY;

  const clamped = Math.min(turns.length - 1, Math.max(0, index));
  const turn = turns[clamped];

  const cartSlotIds: string[] = [];
  for (let n = 0; n <= clamped; n += 1) {
    const step = turns[n];
    if (PURCHASE_ACTIONS.has(step.action) && step.target !== null) {
      cartSlotIds.push(step.target);
    }
  }

  return {
    index: clamped,
    turn,
    stationId: turn.station_id,
    targetSlotId: turn.target,
    action: turn.action,
    reason: turn.reason,
    cartSlotIds,
    done: clamped === turns.length - 1,
  };
}

/** The bays this shopper stood at, in order, without repeats. */
export function stationsOf(turns: AiTraceTurn[]): string[] {
  const seen: string[] = [];
  for (const turn of turns) {
    if (seen[seen.length - 1] !== turn.station_id) seen.push(turn.station_id);
  }
  return seen;
}
