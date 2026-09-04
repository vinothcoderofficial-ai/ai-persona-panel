import type { Event as ShopperEvent } from "@/contracts/event.schema";
import type { Planogram } from "@/contracts/planogram.schema";
import type { Session } from "@/contracts/session.schema";

/**
 * The API serves SPEC's root paths; the vite dev proxy strips this prefix.
 */
const BASE = "/api";

const JSON_HEADERS = { "Content-Type": "application/json" };

/**
 * POST /sessions validates the whole session document against
 * session.schema.json, so the id and start time come from the client.
 */
export interface CreateSessionBody {
  session_id: string;
  variant_id: string;
  consent: boolean;
  started_at: string;
  screen_w: number;
  screen_h: number;
  mode: Session["mode"];
  /** S10: the capture flow fills these three in. A skipped flow leaves them out. */
  calibration_error_px?: number | null;
  intake?: Session["intake"];
  archetype_label?: Session["archetype_label"];
}

/**
 * POST /sessions/{id}/finish — exactly `api/app/routers/sessions.py`'s
 * `_FINISH_FIELDS`, and no more: the router merges these four into the stored
 * session and re-validates the whole document, and session.schema.json sets
 * `additionalProperties: false`, so an extra key here is a 422 on the finish
 * call rather than a field that is quietly ignored.
 *
 * All four are required, not optional. The verdict is the point of finishing:
 * a session closed without `accepted`/`reject_reason` is invisible to
 * `scripts/eval.py` (S19), which loads *accepted* sessions, and that is exactly
 * the state every session was left in before this. Making them mandatory means
 * a caller cannot regress to it without the compiler saying so.
 *
 * The values come from `SessionGate.evaluate`, which is the only thing that
 * decides them.
 */
export interface FinishSessionBody {
  ended_at: string;
  quality: NonNullable<Session["quality"]>;
  accepted: boolean;
  /** One of the schema's five reasons, or null exactly when `accepted` is true. */
  reject_reason: NonNullable<Session["reject_reason"]> | null;
}

async function failure(method: string, path: string, res: Response): Promise<Error> {
  const body = await res.text().catch(() => "");
  const detail = body.trim().length > 0 ? ` — ${body.trim().slice(0, 200)}` : "";
  return new Error(`${method} ${path} failed: ${res.status} ${res.statusText}${detail}`);
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw await failure("GET", path, res);
  return (await res.json()) as T;
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify(body),
  });
  if (!res.ok) throw await failure("POST", path, res);
  return (await res.json()) as T;
}

/** resolve() is server-side only: the scene renders exactly what this returns. */
export function getResolvedVariant(variantId: string): Promise<Planogram> {
  return getJson<Planogram>(`/variants/${encodeURIComponent(variantId)}/resolved`);
}

/** The server writes the prediction lock here, before it accepts any event. */
export function createSession(body: CreateSessionBody): Promise<Session> {
  return postJson<Session>("/sessions", body);
}

export async function postEvents(
  sessionId: string,
  events: ShopperEvent[],
  opts: { keepalive?: boolean } = {},
): Promise<void> {
  const path = `/sessions/${encodeURIComponent(sessionId)}/events`;
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify(events),
    keepalive: opts.keepalive === true,
  });
  if (!res.ok) throw await failure("POST", path, res);
}

export function finishSession(
  sessionId: string,
  body: FinishSessionBody,
): Promise<Session> {
  return postJson<Session>(`/sessions/${encodeURIComponent(sessionId)}/finish`, body);
}
