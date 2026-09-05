/**
 * The note the store leaves behind: which session this browser last opened.
 *
 * The session id is generated in the browser - `crypto.randomUUID()` in
 * main.tsx - and the two screens that need it are opened in *other windows*:
 * the spectator on the second monitor, the dashboard after the run. Before
 * this it existed nowhere an operator could reach, so `#/spectator?session=`
 * meant reading a uuid off a network tab and typing it back in, on camera.
 *
 * What this module is not
 * -----------------------
 * It is not a session store and it is not evidence. The session document lives
 * on the server, written by `POST /sessions` along with its prediction lock,
 * and the anonymised corpus is `data/sessions/`. This is a convenience note in
 * one operator's browser, and every screen that reads it treats it as a
 * suggestion: an explicit `?session=` in a URL always wins, and a screen that
 * followed this note says so on screen.
 *
 * Two rules it must never break
 * -----------------------------
 * **It may not throw.** `localStorage` is not always there: Safari's private
 * window historically threw on `setItem`, and Chrome with site data blocked
 * throws on the `window.localStorage` property access itself. A shopper's
 * session must never fail because a convenience note could not be written, so
 * every access here is wrapped, both ways.
 *
 * **It may not invent.** A read that finds nothing, or finds something it does
 * not recognise, is `null`. A spectator screen pointed at a fabricated id would
 * sit saying CONNECTING while a real shopper was being measured, which is worse
 * than a screen that admits it has nothing to watch.
 */

/** The slice of `Storage` used here, so tests need no real localStorage. */
export interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

/**
 * Namespaced: during the demo the API, the vite server and anything else on
 * localhost share one origin's storage, and a bare "session" would be a
 * collision waiting to happen.
 */
export const LAST_SESSION_KEY = "shoppertwin.last_session";

/**
 * Enough to open either screen: the spectator needs the id, and the dashboard
 * needs the id *and* the variant, because `POST /experiments` takes both.
 * `started_at` is what the session document was opened with, and is here so a
 * screen can say which run it is following rather than only its uuid.
 */
export interface LastSession {
  session_id: string;
  variant_id: string;
  started_at: string;
}

/**
 * `window.localStorage` when it can be reached, otherwise nothing. The access
 * itself throws when site data is blocked, which is why this is a function
 * with a `try` around it and not a module-level constant.
 */
function browserStorage(): StorageLike | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function nonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

/** Record the session the store just opened, replacing whatever came before. */
export function rememberSession(
  entry: LastSession,
  storage: StorageLike | null = browserStorage(),
): void {
  if (storage === null) return;
  try {
    storage.setItem(LAST_SESSION_KEY, JSON.stringify(entry));
  } catch {
    // A quota or a privacy mode. The store carries on: this note is a
    // convenience for the operator, never part of the measurement.
  }
}

/** The last session opened in this browser, or `null` - never a guess. */
export function readLastSession(
  storage: StorageLike | null = browserStorage(),
): LastSession | null {
  if (storage === null) return null;

  let raw: string | null;
  try {
    raw = storage.getItem(LAST_SESSION_KEY);
  } catch {
    return null;
  }
  if (raw === null) return null;

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof parsed !== "object" || parsed === null) return null;

  // Checked field by field rather than cast: this string came out of a browser
  // store that anything on the origin can write, and half a session - an id
  // with no variant - cannot open the dashboard at all.
  const { session_id, variant_id, started_at } = parsed as Record<string, unknown>;
  if (!nonEmptyString(session_id)) return null;
  if (!nonEmptyString(variant_id)) return null;
  if (!nonEmptyString(started_at)) return null;

  return { session_id, variant_id, started_at };
}
