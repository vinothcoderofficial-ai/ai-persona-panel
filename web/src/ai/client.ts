/**
 * The four calls `#/ai` makes, and the shapes `api/app/routers/ai.py` returns.
 *
 * Separate from `src/api/client.ts` on purpose. That module is the *shopper's*
 * path - sessions, events, the resolved variant - and it is imported by the
 * store, which is the one screen a person is measured against. This one is
 * imported only by an operator screen, and keeping it out of that import graph
 * means nothing here can ever end up loaded on the measured page.
 *
 * `fetchImpl` is injected everywhere rather than closed over, the way
 * `src/whatif/client.ts` does it, so the panel's tests need no server and no
 * global stub.
 */

export type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

/** The API serves SPEC's root paths; the vite dev proxy strips this prefix. */
const BASE = "/api";

/**
 * A failed AI call, carrying the two things the panel has to show.
 *
 * `status` distinguishes the three failures the router deliberately keeps
 * apart - 503 never reached the model, 422 it named something the store does
 * not stock, 502 it never produced valid JSON - and `detail` is the server's
 * own sentence, which is the only part a person can act on. Collapsing either
 * into "request failed" would make this screen useless for the exact situation
 * it exists to explain.
 */
export class AiRequestError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "AiRequestError";
    this.status = status;
    this.detail = detail;
  }
}

/**
 * The server's `detail`, or - when the body is not the JSON envelope FastAPI
 * normally sends, which is what a proxy error or a dead upstream looks like -
 * something that still names the status code rather than an empty string.
 */
async function detailOf(response: Response): Promise<string> {
  const raw = await response.text().catch(() => "");
  try {
    const parsed = JSON.parse(raw) as { detail?: unknown };
    if (typeof parsed.detail === "string" && parsed.detail.length > 0) {
      return parsed.detail;
    }
  } catch {
    // not JSON; fall through to the status line below
  }
  const trimmed = raw.trim().slice(0, 200);
  const suffix = trimmed.length > 0 ? ` — ${trimmed}` : "";
  return `${response.status} ${response.statusText}${suffix}`;
}

async function request<T>(
  path: string,
  fetchImpl: FetchLike,
  init?: RequestInit,
): Promise<T> {
  const response = await fetchImpl(`${BASE}${path}`, init);
  if (!response.ok) throw new AiRequestError(response.status, await detailOf(response));
  return (await response.json()) as T;
}

export interface AiPersonaSummary {
  persona_id: string;
  archetype: string | null;
  share_of_population: number | null;
  description: string | null;
  policy_cached: boolean;
  trace_cached: boolean;
  /** `null` means no trace exists — never 0, which would read as "a trace of nothing". */
  trace_model: string | null;
  trace_n_shoppers: number | null;
  trace_n_turns: number | null;
  trace_temperature: number | null;
}

export interface AiStatus {
  provider: string;
  model: string | null;
  base_url?: string | null;
  offline: boolean;
  api_key_set: boolean;
  /** Whether a request would actually go out. `reason` says why not when it would not. */
  can_call: boolean;
  reason: string | null;
  planogram_id: string;
  prompt_template: string;
  personas: AiPersonaSummary[];
}

/** `schemas/policy.schema.json`, as a value the panel prints rather than reasons about. */
export type PersonaPolicy = Record<string, unknown>;

export interface AiPolicyResponse {
  persona_id: string;
  planogram_id: string;
  source: "cache";
  policy: PersonaPolicy;
  /** The rendered instruction, exactly as `sim/policy.py` sends it. */
  prompt: string;
}

export interface AiRegenerateResponse {
  persona_id: string;
  planogram_id: string;
  source: "llm";
  provider: string;
  model: string;
  elapsed_s: number;
  prompt: string;
  policy: PersonaPolicy;
  /** What the simulator is still running. `null` when nothing was cached. */
  committed: PersonaPolicy | null;
  differs: boolean;
  changed_fields: string[];
  written_to: string;
  /** Always false: a re-ask is shown, never adopted. See `api/app/routers/ai.py`. */
  adopted: boolean;
}

export interface AiTraceTurn {
  turn: number;
  station_id: string;
  action: string;
  target: string | null;
  /** The model's own words. This is what makes a trace evidence. */
  reason: string;
  time_left_s: number;
}

export interface AiTraceCartLine {
  sku_id: string;
  name: string;
  brand: string;
  category: string;
  price: number;
  slot_id: string;
}

export interface AiTraceShopper {
  shopper_index: number;
  turns: AiTraceTurn[];
  rejections: unknown[];
  cart: string[];
  cart_detail: AiTraceCartLine[];
  stations_visited: string[];
  end_reason: string;
  end_note: string | null;
  n_turns: number;
  n_rejections: number;
}

export interface AiTrace {
  trace_version: number;
  persona_id: string;
  archetype: string;
  description: string;
  planogram_id: string;
  n_shoppers: number;
  seed: number;
  temperature: number;
  model: string;
  n_turns: number;
  n_rejections: number;
  end_reasons: Record<string, number>;
  carts: Record<string, number>;
  shoppers: AiTraceShopper[];
}

/**
 * The AI configuration, with `personas` guaranteed to be an array.
 *
 * The guarantee is the point. Everything downstream maps over that list and
 * reads `[0]` to pick a default selection, and a 200 carrying anything else -
 * an older API, a proxy answering for a dead upstream, a half-written response -
 * used to throw during render and leave a blank page instead of a screen that
 * says what it does know. Normalising here rather than in the panel keeps that
 * one guarantee in one place, where every future caller inherits it.
 */
export async function getAiStatus(fetchImpl: FetchLike): Promise<AiStatus> {
  const status = await request<AiStatus>("/ai/status", fetchImpl);
  return {
    ...status,
    personas: Array.isArray(status.personas) ? status.personas : [],
  };
}

export function getPersonaPolicy(
  personaId: string,
  fetchImpl: FetchLike,
): Promise<AiPolicyResponse> {
  return request<AiPolicyResponse>(
    `/ai/personas/${encodeURIComponent(personaId)}/policy`,
    fetchImpl,
  );
}

/**
 * Ask the model again, now.
 *
 * The server writes the answer to a preview directory and returns it beside the
 * committed one; it never adopts it. Nothing in this function should ever grow
 * a flag that changes that — the committed policies are pre-registered inputs
 * and `predictions/` locks are hashed against what they produce.
 */
export function regeneratePersonaPolicy(
  personaId: string,
  fetchImpl: FetchLike,
): Promise<AiRegenerateResponse> {
  return request<AiRegenerateResponse>(
    `/ai/personas/${encodeURIComponent(personaId)}/policy`,
    fetchImpl,
    { method: "POST" },
  );
}

export function getPersonaTrace(
  personaId: string,
  fetchImpl: FetchLike,
): Promise<AiTrace> {
  return request<AiTrace>(
    `/ai/personas/${encodeURIComponent(personaId)}/trace`,
    fetchImpl,
  );
}

/**
 * The resolved planogram for one arm, through an injected fetch.
 *
 * `src/api/client.ts` has `getResolvedVariant`, and it closes over the global
 * `fetch` because it is the shopper's path, where there is nothing to inject.
 * The operator screens need the same document with a transport they control,
 * and a second copy of the *resolution* would be the thing CLAUDE.md forbids —
 * so this is a second call site, not a second resolver. `resolve()` still runs
 * only in `api/app/resolve.py`; this asks the server for its answer.
 */
export function getResolvedPlanogram(
  variantId: string,
  fetchImpl: FetchLike,
): Promise<PlanogramDocument> {
  return request<PlanogramDocument>(
    `/variants/${encodeURIComponent(variantId)}/resolved`,
    fetchImpl,
  );
}

/** Re-exported so panel modules import one client, not two. */
export type PlanogramDocument = import("@/contracts/planogram.schema").Planogram;
