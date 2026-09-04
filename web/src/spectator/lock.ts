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
 * `POST /sessions` returns the badge to the page that created the session:
 * `{"prediction": {"prediction_id", "sha256_prefix", "created_at", "sim_run_id"}}`.
 * The spectator is a *different* window - it is opened on a second monitor and
 * never creates a session - and the API has no endpoint that hands a lock back
 * by session id. So the spectator is given its evidence in its own URL:
 *
 *     #/spectator?session=<id>&sha256=<hex>&locked_at=<created_at>
 *
 * and, when the whole locked vector is wanted beside the live heatmap, a URL to
 * the lock document itself:
 *
 *     #/spectator?session=<id>&lock=<url of predictions/{id}.json>
 *
 * Both are read here. Neither is invented: with no lock supplied the badge says
 * so in as many words, because a badge that fills itself in from nothing is
 * worse than no badge at all.
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
  source: "query" | "file" | "none";
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

/** The badge from the URL, filled in by the lock document when one is offered. */
export function mergeLocks(fromQuery: LockView, fromFile: LockView): LockView {
  if (fromFile.source === "none") return fromQuery;
  if (fromQuery.source === "none") return fromFile;
  return {
    prediction_id: fromFile.prediction_id ?? fromQuery.prediction_id,
    sha256_prefix: fromFile.sha256_prefix ?? fromQuery.sha256_prefix,
    created_at: fromFile.created_at ?? fromQuery.created_at,
    population_fixation_prob: fromFile.population_fixation_prob,
    source: "file",
  };
}
