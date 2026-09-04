/**
 * The locked prediction, as the spectator screen knows it.
 *
 * SPEC 4.6 is the lock document `api/app/prediction.py` writes to
 * `predictions/{session_id}.json` before the session exists. The spectator
 * screen shows two things out of it - the first 8 hex characters of `sha256`
 * and `created_at` - and those two are the on-camera evidence behind the demo's
 * central sentence: *"prediction locked at 10:32:07, shopping began 10:32:41."*
 *
 * Where it comes from
 * -------------------
 * `POST /sessions` returns the badge to the page that *created* the session.
 * The spectator is a different window - opened on a second monitor, and it
 * never creates a session - so it reads the lock itself, from three sources
 * that `resolveLock` folds in this order (lowest priority first):
 *
 *   3. `GET /sessions/{id}/prediction`, the default. This is what makes
 *      `#/spectator?session=<id>` a complete instruction: the operator types
 *      nothing else and the badge and the locked heatmap column fill in.
 *   2. `?sha256=<hex>&locked_at=<created_at>` typed into the spectator URL.
 *   1. `?lock=<url of predictions/{id}.json>`, a whole lock document.
 *
 * The two URL forms are explicit overrides and beat the endpoint - they are how
 * a committed lock file is replayed against a session the running API no longer
 * knows about. `resolveLock`'s own doc comment argues the ordering.
 *
 * Nothing here is ever invented. With no lock from any source the badge says so
 * in as many words and the heatmap's right-hand column stays empty, because a
 * badge that fills itself in from nothing - or a prediction column of zeros -
 * is worse than none at all.
 */

/** What the spectator screen holds about the lock. Every field may be absent. */
export interface LockView {
  prediction_id: string | null;
  /** The first 8 hex characters of the lock's sha256. */
  sha256_prefix: string | null;
  /** SPEC 4.6 `created_at`: UTC ISO-8601 with milliseconds and a trailing Z. */
  created_at: string | null;
  /** The locked per-slot prediction, when the whole document was available. */
  population_fixation_prob: Record<string, number> | null;
  /** Which of the sources `resolveLock` folds this view actually came from. */
  source: "query" | "file" | "api" | "none";
}

export const NO_LOCK: LockView = {
  prediction_id: null,
  sha256_prefix: null,
  created_at: null,
  population_fixation_prob: null,
  source: "none",
};

const HEX_8 = /^[0-9a-f]{8}$/;

/**
 * The 8 characters the badge shows, from either the full 64-character digest or
 * a prefix that was already cut down by `POST /sessions`.
 *
 * Anything that is not hex returns null rather than a truncated string: a badge
 * showing eight characters of something that was never a digest would be
 * exactly the false evidence this whole mechanism exists to prevent.
 */
export function hashPrefix(sha256: unknown): string | null {
  if (typeof sha256 !== "string" || sha256.length < 8) return null;
  const prefix = sha256.slice(0, 8).toLowerCase();
  return HEX_8.test(prefix) ? prefix : null;
}

function nonEmpty(value: string | null): string | null {
  return value !== null && value.length > 0 ? value : null;
}

/** Read the badge out of the spectator page's own query string. */
export function lockFromQuery(search: string): LockView {
  const params = new URLSearchParams(search);
  const sha256_prefix = hashPrefix(params.get("sha256"));
  const created_at = nonEmpty(params.get("locked_at"));
  const prediction_id = nonEmpty(params.get("prediction"));

  if (sha256_prefix === null && created_at === null && prediction_id === null) {
    return NO_LOCK;
  }
  return {
    prediction_id,
    sha256_prefix,
    created_at,
    population_fixation_prob: null,
    source: "query",
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function numericMap(value: unknown): Record<string, number> | null {
  if (!isRecord(value)) return null;
  const out: Record<string, number> = {};
  for (const [key, entry] of Object.entries(value)) {
    if (typeof entry === "number" && Number.isFinite(entry)) out[key] = entry;
  }
  return out;
}

/**
 * Read a whole prediction.schema.json document.
 *
 * A document without a usable digest and timestamp is not a lock, and is
 * reported as no lock at all rather than as a half-populated badge.
 */
export function lockFromDocument(document: unknown): LockView {
  if (!isRecord(document)) return NO_LOCK;
  const sha256_prefix = hashPrefix(document.sha256);
  const created_at =
    typeof document.created_at === "string" ? nonEmpty(document.created_at) : null;
  if (sha256_prefix === null || created_at === null) return NO_LOCK;

  return {
    prediction_id:
      typeof document.prediction_id === "string" ? nonEmpty(document.prediction_id) : null,
    sha256_prefix,
    created_at,
    population_fixation_prob: numericMap(document.population_fixation_prob),
    source: "file",
  };
}

/**
 * Fetch a lock document from `?lock=<url>`.
 *
 * Returns NO_LOCK on any failure - a missing file, a 404, a body that is not a
 * lock. The caller renders "no locked prediction" in that case; it never falls
 * back to zeros.
 */
export async function fetchLock(
  url: string,
  fetchImpl: typeof fetch = fetch,
): Promise<LockView> {
  try {
    const response = await fetchImpl(url);
    if (!response.ok) return NO_LOCK;
    return lockFromDocument(await response.json());
  } catch {
    return NO_LOCK;
  }
}

/**
 * Two sources, field by field: `higher` wins wherever it has something.
 *
 * The fall-through on `population_fixation_prob` matters more than it looks.
 * `?sha256=` and `?locked_at=` carry a badge and never a vector, so without it
 * a hand-typed badge would silently blank the locked heatmap column beside it -
 * the panel would drop from a real prediction to "unavailable" purely because
 * somebody pinned the hash.
 */
export function mergeLocks(lower: LockView, higher: LockView): LockView {
  if (higher.source === "none") return lower;
  if (lower.source === "none") return higher;
  return {
    prediction_id: higher.prediction_id ?? lower.prediction_id,
    sha256_prefix: higher.sha256_prefix ?? lower.sha256_prefix,
    created_at: higher.created_at ?? lower.created_at,
    population_fixation_prob:
      higher.population_fixation_prob ?? lower.population_fixation_prob,
    source: higher.source,
  };
}

/**
 * Fold every lock source into one, **lowest priority first**.
 *
 * The spectator's order is `[fetched, typed, document]`:
 *
 *   1. `?lock=<url>` — a whole lock document, named explicitly. Highest,
 *      because it is the artefact itself: its digest, its `created_at` and its
 *      vector are one internally consistent object, and letting a hand-typed
 *      hash sit beside a different document's numbers would put a badge on
 *      screen that does not describe what is beside it.
 *   2. `?sha256=` / `?locked_at=` / `?prediction=` — hand-typed badge fields.
 *      Explicit, so they beat the endpoint; useful when replaying a committed
 *      lock file against a session the running API no longer knows about.
 *   3. `GET /sessions/{id}/prediction` — the automatic default, which is what
 *      makes `#/spectator?session=<id>` a complete instruction on its own.
 */
export function resolveLock(sources: readonly LockView[]): LockView {
  return sources.reduce(mergeLocks, NO_LOCK);
}

/**
 * `GET /sessions/{session_id}/prediction`'s response body.
 *
 * Not the same shape as the lock file: the endpoint has already cut the digest
 * down to the 8 characters SPEC 4.6 says the spectator screen shows, and it
 * omits `session_id`, `variant_id` and `git_commit`. It serves the *locked*
 * vector - never anything re-simulated - which is the only reason that vector
 * can honestly be drawn beside the live column.
 */
export function lockFromPredictionEndpoint(body: unknown): LockView {
  if (!isRecord(body)) return NO_LOCK;
  const sha256_prefix = hashPrefix(body.sha256_prefix);
  const created_at =
    typeof body.created_at === "string" ? nonEmpty(body.created_at) : null;
  if (sha256_prefix === null || created_at === null) return NO_LOCK;

  return {
    prediction_id:
      typeof body.prediction_id === "string" ? nonEmpty(body.prediction_id) : null,
    sha256_prefix,
    created_at,
    population_fixation_prob: numericMap(body.population_fixation_prob),
    source: "api",
  };
}

/**
 * The API serves SPEC's root paths and the vite dev proxy strips this prefix -
 * the same constant `web/src/api/client.ts` uses. It is written again rather
 * than imported because that module is the shopper's ingest client and this one
 * is the spectator's read path; one shared string is not worth coupling them.
 */
const API_BASE = "/api";

/**
 * The session's locked prediction, for the badge and the heatmap's right-hand
 * column. This is the default source, so `#/spectator?session=<id>` is enough.
 *
 * A 404 (no such session, or a session with no lock) and an API that is not
 * running both come back as NO_LOCK, and the view then says so in as many
 * words. It never falls back to zeros, and it never substitutes a fresh
 * simulation: `GET /experiments/{id}` re-runs the simulator, and showing that
 * beside the live column would quietly discard the pre-registration.
 */
export async function fetchPredictionLock(
  sessionId: string,
  fetchImpl: typeof fetch = fetch,
): Promise<LockView> {
  const path = `${API_BASE}/sessions/${encodeURIComponent(sessionId)}/prediction`;
  try {
    const response = await fetchImpl(path);
    if (!response.ok) return NO_LOCK;
    return lockFromPredictionEndpoint(await response.json());
  } catch {
    return NO_LOCK;
  }
}
