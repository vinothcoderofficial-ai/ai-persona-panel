/**
 * SPEC 4.7 - the one message `ws/spectator/{id}` ever sends.
 *
 * ```json
 * {"session_id":"uuid","t_ms":41200,"n_fixations":37,"n_cursor_dwells":12,
 *  "evidence_count":37,"evidence_kind":"fixations","stations_visited":2,
 *  "attention":{"B1S3P1":0.11},"latest_gaze":{"x":812,"y":344},
 *  "spearman":0.58,"meaningful":true,"prediction_id":"uuid"}
 * ```
 *
 * `api/app/live.py:LiveState.snapshot` is the authority for the field set; this
 * module is its browser-side reader. Parsing is strict on purpose: a frame that
 * is not a 4.7 message is discarded whole rather than rendered with holes,
 * because a heatmap that silently reverts to zeros looks exactly like a shopper
 * who stopped looking at anything.
 *
 * **The counts, and why there are three of them.** SPEC 4.7 says `meaningful`
 * is `n_fixations >= 15`, which was written for a webcam session. There is no
 * webcam panel, and a `cursor_only` session has no fixations at all, so taken
 * literally the agreement meter never turns on for the only sessions the demo
 * can produce. `api/app/live.py` therefore counts each mode's own attention
 * channel - fixations for webcam, cursor dwells for cursor_only - and says
 * which in the frame. `n_fixations` and `n_cursor_dwells` keep their literal
 * meanings; `evidence_count` is whichever of them the 15 threshold was applied
 * to and `evidence_kind` names it, so nothing on screen is ever labelled as
 * something it is not.
 */

/** `api/app/live.py:MEANINGFUL_MIN_EVIDENCE` - the meter stays grey below this. */
export const MEANINGFUL_MIN_EVIDENCE = 15;

/** `api/app/live.py:_EVIDENCE_BY_MODE` - which count `meaningful` was read off. */
export type EvidenceKind = "fixations" | "cursor_dwells";

const EVIDENCE_KINDS: readonly EvidenceKind[] = ["fixations", "cursor_dwells"];

/** How each kind is written on screen. `cursor_dwells` is not English. */
export const EVIDENCE_LABEL: Record<EvidenceKind, string> = {
  fixations: "fixations",
  cursor_dwells: "cursor dwells",
};

/**
 * The two constants `api/app/routers/ws.py:fake_stream` stamps on every
 * synthetic frame, alongside `"fake": true`. Any one of the three is enough to
 * refuse to present a frame as a measurement.
 */
export const FAKE_SESSION_ID = "fake-session";
export const FAKE_PREDICTION_ID = "fake-prediction";

export interface GazePosition {
  x: number;
  y: number;
}

export interface LiveUpdate {
  session_id: string;
  t_ms: number;
  /** `fixation` events so far. Always 0 in a cursor_only session. */
  n_fixations: number;
  /** `cursor_dwell` events so far. */
  n_cursor_dwells: number;
  /** Whichever of the two above `meaningful` was decided from. */
  evidence_count: number;
  /** Which one that was. */
  evidence_kind: EvidenceKind;
  stations_visited: number;
  /** Fused per-slot attention, from analytics/fusion.py by way of live.py. */
  attention: Record<string, number>;
  latest_gaze: GazePosition | null;
  /**
   * Spearman against `fuse_synthetic` of the LOCKED prediction - the same
   * vector `scripts/eval.py` scores against. Null when the server sent none.
   */
  spearman: number | null;
  /** `evidence_count >= 15`, decided by the server. Never recomputed here. */
  meaningful: boolean;
  prediction_id: string;
  /** True for a frame from ws.py's synthetic demo stream. Never a measurement. */
  fake: boolean;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function finiteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

/** Only numeric entries survive; a slot with a non-number is not a slot at 0. */
function numericMap(value: unknown): Record<string, number> | null {
  if (!isRecord(value)) return null;
  const out: Record<string, number> = {};
  for (const [key, entry] of Object.entries(value)) {
    if (finiteNumber(entry)) out[key] = entry;
  }
  return out;
}

function evidenceKind(value: unknown): EvidenceKind | null {
  return EVIDENCE_KINDS.includes(value as EvidenceKind) ? (value as EvidenceKind) : null;
}

function gaze(value: unknown): GazePosition | null {
  if (!isRecord(value)) return null;
  const { x, y } = value;
  if (!finiteNumber(x) || !finiteNumber(y)) return null;
  return { x, y };
}

/**
 * Parse one frame. `raw` is the string off the socket, or an already-decoded
 * value. Returns null for anything that is not a SPEC 4.7 message - including
 * the `{"error": ...}` diagnostic `ws/session` sends, which must never be
 * mistaken for data.
 */
export function parseLiveUpdate(raw: unknown): LiveUpdate | null {
  let body: unknown = raw;
  if (typeof raw === "string") {
    try {
      body = JSON.parse(raw);
    } catch {
      return null;
    }
  }
  if (!isRecord(body)) return null;

  const attention = numericMap(body.attention);
  const kind = evidenceKind(body.evidence_kind);
  if (
    typeof body.session_id !== "string" ||
    typeof body.prediction_id !== "string" ||
    typeof body.meaningful !== "boolean" ||
    !finiteNumber(body.t_ms) ||
    !finiteNumber(body.n_fixations) ||
    !finiteNumber(body.n_cursor_dwells) ||
    !finiteNumber(body.evidence_count) ||
    kind === null ||
    !finiteNumber(body.stations_visited) ||
    attention === null
  ) {
    return null;
  }

  return {
    session_id: body.session_id,
    t_ms: body.t_ms,
    n_fixations: body.n_fixations,
    n_cursor_dwells: body.n_cursor_dwells,
    evidence_count: body.evidence_count,
    evidence_kind: kind,
    stations_visited: body.stations_visited,
    attention,
    latest_gaze: gaze(body.latest_gaze),
    spearman: finiteNumber(body.spearman) ? body.spearman : null,
    meaningful: body.meaningful,
    prediction_id: body.prediction_id,
    fake:
      body.fake === true ||
      body.session_id === FAKE_SESSION_ID ||
      body.prediction_id === FAKE_PREDICTION_ID,
  };
}

/** `t_ms` as `m:ss`, for the elapsed-time readout on the spectator screen. */
export function formatElapsed(tMs: number): string {
  const total = Math.max(0, Math.floor(tMs / 1000));
  const seconds = total % 60;
  return `${Math.floor(total / 60)}:${String(seconds).padStart(2, "0")}`;
}
