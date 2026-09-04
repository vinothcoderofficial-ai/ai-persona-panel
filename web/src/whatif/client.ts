import type { SimResult } from "@/contracts/simresult.schema";
import type { WhatIfRequestBody } from "@/whatif/patches";

/**
 * `POST /whatif` (SPEC 4.8), as this page calls it.
 *
 * The failure path is the reason this is its own module rather than one line of
 * `fetch`. The endpoint answers 404 for an unknown planogram, 400 for a patch
 * naming something that does not exist, and 422 for patches that fail
 * `variant.schema.json`, and each carries a `detail` saying exactly what was
 * wrong - "move_sku: unknown to_slot_id 'B9S9P9'". That sentence is the only
 * thing that tells an operator which dropdown they just broke, so it is carried
 * onto the error and onto the screen instead of being flattened into "request
 * failed".
 */

/** The API serves SPEC's root paths; the vite dev proxy strips this prefix. */
const BASE = "/api";

export const WHATIF_PATH = "/whatif";

const JSON_HEADERS = { "Content-Type": "application/json" };

/** SPEC 4.8's response. `per_persona` is one full SimResult per persona. */
export interface WhatIfResponse {
  sim_run_id: string;
  /** Server-side compute for this call, in milliseconds. Not the round trip. */
  elapsed_ms: number;
  per_persona: Record<string, SimResult>;
  population_fixation_prob: Record<string, number>;
  /**
   * `{}` when no focal SKU was named or inferable, and a `null` value when that
   * figure's baseline was exactly 0. Neither is 0 - see `lift.ts`.
   */
  lift_vs_baseline: Record<string, number | null>;
  ad_slot_attention: Record<string, number>;
}

/** Just enough of `fetch` to post JSON and read a failure body. */
export interface HttpResponse {
  ok: boolean;
  status: number;
  statusText: string;
  text(): Promise<string>;
}

export type FetchLike = (
  url: string,
  init: { method: string; headers: Record<string, string>; body: string },
) => Promise<HttpResponse>;

export type RunWhatIf = (body: WhatIfRequestBody) => Promise<WhatIfResponse>;

export class WhatIfError extends Error {
  readonly status: number;
  /** The endpoint's own `detail`, when it sent one. */
  readonly detail: string | null;

  constructor(message: string, status: number, detail: string | null) {
    super(message);
    this.name = "WhatIfError";
    this.status = status;
    this.detail = detail;
  }
}

/** One FastAPI validation error, as the 422 body carries them. */
function validationMessage(entry: unknown): string {
  if (typeof entry !== "object" || entry === null) return String(entry);
  const record = entry as { loc?: unknown; msg?: unknown };
  const location = Array.isArray(record.loc) ? record.loc.join("/") : null;
  const message = typeof record.msg === "string" ? record.msg : JSON.stringify(entry);
  return location === null ? message : `${location}: ${message}`;
}

/**
 * The endpoint's `detail`, whatever shape it arrived in: a string for the
 * router's own HTTPExceptions, a list of objects for FastAPI's request
 * validation, or no JSON at all from a proxy or a dead server.
 */
function detailOf(body: string): string | null {
  const trimmed = body.trim();
  if (trimmed.length === 0) return null;
  try {
    const parsed: unknown = JSON.parse(trimmed);
    const detail = (parsed as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return detail.map(validationMessage).join("; ");
    if (detail !== undefined) return JSON.stringify(detail);
  } catch {
    // Not JSON: a gateway page or a stack trace. The raw text is still the
    // most informative thing available.
  }
  return trimmed.slice(0, 400);
}

export async function postWhatIf(
  body: WhatIfRequestBody,
  fetchImpl: FetchLike = fetch,
): Promise<WhatIfResponse> {
  const res = await fetchImpl(`${BASE}${WHATIF_PATH}`, {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    const detail = detailOf(await res.text().catch(() => ""));
    const suffix = detail === null ? "" : ` — ${detail}`;
    throw new WhatIfError(
      `POST ${WHATIF_PATH} failed: ${res.status} ${res.statusText}${suffix}`,
      res.status,
      detail,
    );
  }

  return JSON.parse(await res.text()) as WhatIfResponse;
}
