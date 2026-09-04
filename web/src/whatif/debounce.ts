/**
 * SPEC M9: *"debounce 300 ms in the UI."*
 *
 * A dropdown produces a change per option the pointer passes over, and each one
 * would otherwise be a full 10,000-shopper simulation. This coalesces a burst
 * into one call carrying the last value.
 *
 * The timer is a parameter, not `setTimeout` reached for from inside, so the
 * tests drive it with no real waiting - the same reason `SpectatorView` takes a
 * socket factory.
 */

/**
 * Run `fn` after `delayMs` and return a canceller.
 *
 * Returning the canceller rather than a handle keeps the handle's type where it
 * belongs (inside the implementation), so nothing here has to care whether a
 * host's `setTimeout` returns a number or a `Timeout`.
 */
export type Schedule = (fn: () => void, delayMs: number) => () => void;

/** SPEC M9's 300 ms. */
export const DEBOUNCE_MS = 300;

export interface Debouncer {
  /** Replace any pending call with this one. */
  schedule(fn: () => void): void;
  /** Drop the pending call. Nothing runs. */
  cancel(): void;
}

export const timerSchedule: Schedule = (fn, delayMs) => {
  const handle = setTimeout(fn, delayMs);
  return () => clearTimeout(handle);
};

export function createDebouncer(
  delayMs: number = DEBOUNCE_MS,
  schedule: Schedule = timerSchedule,
): Debouncer {
  let cancelPending: (() => void) | null = null;

  return {
    schedule(fn: () => void): void {
      cancelPending?.();
      cancelPending = null;
      if (delayMs <= 0) {
        // No debounce at all. Not a UI setting - it exists so a caller that
        // wants the call now does not have to reach around this object.
        fn();
        return;
      }
      cancelPending = schedule(() => {
        cancelPending = null;
        fn();
      }, delayMs);
    },
    cancel(): void {
      cancelPending?.();
      cancelPending = null;
    },
  };
}
