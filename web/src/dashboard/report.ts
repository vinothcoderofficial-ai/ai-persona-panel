import {
  NOT_APPLICABLE,
  formatMetric,
  realPanelCaptured,
  type ExperimentResult,
} from "@/dashboard/experimentResult";
import {
  PAPER,
  PAPER_INK,
  PAPER_MUTED,
  PAPER_RULE,
  REAL_PRINT,
  SYNTH_PRINT,
} from "@/dashboard/styles";

/**
 * The session report: one finished session, as a document somebody keeps.
 *
 * A dashboard is a screen. This is the artefact a participant walks away with
 * and a client files, read months later by someone who has never seen the app
 * and cannot re-run anything -- so every claim in it has to be legible on its
 * own, and every number has to arrive with the conditions it was measured
 * under attached.
 *
 * What it leads with is not the correlation. Anyone can print a correlation.
 * What this platform can put in a document that a predictive-attention vendor
 * cannot is the **pre-registration**: the synthetic prediction was simulated,
 * hashed and written to disk on `POST /sessions`, before the session row
 * existed and therefore before it could record an event, and the API refuses
 * events for a session with no lock. That ordering is the project's central
 * claim, so it is section one and the hash is the largest thing on the page.
 *
 * Three rules govern everything below, and they are the reason this module
 * exists rather than a template:
 *
 *  1. **A figure that is not a real number is never printed as one.** The rule
 *     is `experimentResult.ts:formatMetric`, shared with the live page.
 *  2. **A session that recorded nothing says so.** `realPanelCaptured` gates
 *     the entire real side; when it is false the metrics are withheld rather
 *     than shown as the guarded 0 the endpoint hands back, because `0.000` in
 *     a printed document reads as a measurement of zero attention.
 *  3. **The capture mode is stated, not implied.** A `cursor_only` session
 *     measured a mouse pointer. A report whose headline Spearman silently
 *     describes pointer movement is precisely the artefact this project exists
 *     not to produce.
 *
 * Both exports -- HTML for printing, JSON for machine-readable evidence -- are
 * built from the same `ReportInput` by the same pair of decisions, so the
 * printed document and the JSON beside it cannot disagree.
 *
 * No dependencies, no `fetch` at render time, no external asset: the HTML is a
 * single self-contained file with its stylesheet inline, and it prints.
 */

// ---------------------------------------------------------------------------
// The prediction lock
// ---------------------------------------------------------------------------

/**
 * The locked prediction, as `GET /sessions/{session_id}/prediction` serves it.
 *
 * Every field may be absent: a session whose lock file is gone, or an API that
 * is not running, produces `NO_PREDICTION_LOCK` and the report says so in as
 * many words. It never falls back to a placeholder digest -- a report showing
 * eight characters of something that was never a hash would be exactly the
 * false evidence the lock mechanism exists to prevent.
 *
 * `sha256_prefix` and not the whole digest because that is what the endpoint
 * serves (SPEC 4.6: the spectator badge shows eight characters). The full
 * 64-character digest lives in the committed lock file, and the report says
 * where.
 *
 * The spectator screen reads the same endpoint through `src/spectator/lock.ts`
 * and this is deliberately not that module. `src/dashboard/styles.ts` records
 * the convention: nothing under `src/dashboard/` imports from `src/spectator/`
 * or `src/whatif/`, which keeps the dashboard out of the import graph
 * `web/tests/spectatorIsolation.test.ts` guards. The two also want different
 * things -- the spectator needs the locked per-slot vector to draw a heatmap
 * column and tracks which of three sources a badge came from; the report needs
 * the provenance fields and nothing else.
 */
export interface PredictionLock {
  prediction_id: string | null;
  /** The first 8 hex characters of the lock's sha256. */
  sha256_prefix: string | null;
  /** SPEC 4.6 `created_at`: UTC ISO-8601 with milliseconds and a trailing Z. */
  created_at: string | null;
  sim_run_id: string | null;
}

export const NO_PREDICTION_LOCK: PredictionLock = {
  prediction_id: null,
  sha256_prefix: null,
  created_at: null,
  sim_run_id: null,
};

const HEX_8 = /^[0-9a-f]{8}$/;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

/**
 * The eight characters the report prints, from the endpoint's `sha256_prefix`.
 *
 * Anything that is not hex returns null rather than a truncated string, for
 * the reason `src/spectator/lock.ts:hashPrefix` gives: printed evidence that
 * was never a digest is worse than no evidence.
 */
function hashPrefix(value: unknown): string | null {
  if (typeof value !== "string" || value.length < 8) return null;
  const prefix = value.slice(0, 8).toLowerCase();
  return HEX_8.test(prefix) ? prefix : null;
}

/** Read a `PredictionLock` out of the prediction endpoint's response body. */
export function lockFromPredictionResponse(body: unknown): PredictionLock {
  if (!isRecord(body)) return NO_PREDICTION_LOCK;
  return {
    prediction_id: text(body.prediction_id),
    sha256_prefix: hashPrefix(body.sha256_prefix),
    created_at: text(body.created_at),
    sim_run_id: text(body.sim_run_id),
  };
}

/** The API serves SPEC's root paths; the vite dev proxy strips this prefix. */
const API_BASE = "/api";

/**
 * This session's locked prediction, for the report's first section.
 *
 * A 404 (no such session, or a session with no lock) and an API that is not
 * running both come back as `NO_PREDICTION_LOCK`, and the report then states
 * that it carries no pre-registration evidence. It never substitutes a fresh
 * simulation: `GET /experiments/{id}` re-runs the simulator, and a digest of
 * that would be a hash computed after the shopping, which is not a
 * pre-registration at all.
 */
export async function fetchPredictionLock(
  sessionId: string,
  fetchImpl: typeof fetch = fetch,
): Promise<PredictionLock> {
  const path = `${API_BASE}/sessions/${encodeURIComponent(sessionId)}/prediction`;
  try {
    const response = await fetchImpl(path);
    if (!response.ok) return NO_PREDICTION_LOCK;
    return lockFromPredictionResponse(await response.json());
  } catch {
    return NO_PREDICTION_LOCK;
  }
}

// ---------------------------------------------------------------------------
// The statements the report makes
// ---------------------------------------------------------------------------

/**
 * The load-bearing sentences, exported so they are first-class reviewable
 * objects rather than string literals buried in a template, and so
 * `web/tests/dashboardReport.test.ts` asserts on the claim rather than on the
 * markup that happens to carry it.
 *
 * None of them may contain `&`, `<`, `>` or `"`: they pass through `escape`
 * on the way into the document, and a test that asserts the report says a
 * thing has to be able to find the thing it said.
 */

/** Printed only when a lock was actually read. It is a claim about evidence. */
export const ORDERING_GUARANTEE =
  "The synthetic prediction was simulated, serialised and SHA-256 hashed when this session " +
  "was registered, before the session existed in the database and therefore before it could " +
  "record a single event. The API refuses events for a session that has no lock, so the " +
  "ordering is structural rather than procedural.";

export const NO_LOCK_NOTICE =
  "No prediction lock could be read for this session, so this report carries no " +
  "pre-registration evidence. Without it, nothing here shows that the synthetic prediction " +
  "predates the shopping it is compared against, and the comparison below should be read as " +
  "an unregistered one.";

export const GAZE_MEASURED =
  "Webcam gaze contributed to the real attention figures below, alongside cursor dwell and " +
  "interaction.";

export const GAZE_NOT_MEASURED =
  "This session measured cursor dwell, not gaze. The real attention figures below were fused " +
  "from pointer movement and interaction only, and no eye tracking contributed to any number " +
  "in this report. Nothing here supports a claim about where this shopper looked.";

export const MODE_NOT_RECORDED =
  "The capture mode of this session was not recorded, so this report cannot say whether eye " +
  "tracking contributed to the figures below. Treat every real attention figure as being of " +
  "unknown provenance.";

export const REAL_PANEL_ABSENT =
  "The real side of this session was not captured. No slot drew any real attention and no " +
  "purchase was recorded, which is what a session that registered no events produces. The " +
  "headline metrics are therefore withheld rather than printed as the zero the endpoint " +
  "returns, because a zero here would read as a measurement of no attention rather than as " +
  "an absence of measurement. The synthetic panel below was genuinely computed and is shown.";

/**
 * What the document is not evidence for, carrying the discipline `RESULTS.md`
 * uses: every item is a thing that is missing, and none of it is filled in
 * with a number.
 *
 * Two of these are conditional -- there is no point telling a reader that the
 * real panel is a single session when there is no real panel at all -- so the
 * list is built, not constant.
 */
function limitsFor(result: ExperimentResult, captured: boolean): string[] {
  const limits: string[] = [];

  if (captured) {
    limits.push(
      "This is one session, not a panel. A single shopper cannot support a statement about " +
        "any population, and no confidence interval is quoted below because one session " +
        "gives no sampling distribution to draw it from.",
    );
    limits.push(
      "There is no noise ceiling. The real panel's split-half repeatability has not been " +
        "established, so there is nothing to benchmark the correlation below against: a " +
        "Spearman is neither good nor bad until it is read against how well the real panel " +
        "agrees with itself.",
    );
  } else {
    limits.push(
      "There is no real panel in this report, so it contains no measurement of accuracy. " +
        "What it does contain is a synthetic prediction and the evidence of when it was " +
        "locked.",
    );
  }

  limits.push(
    "This is a single variant, not a holdout. Agreement on the variant a model was built " +
      "over is not evidence that it transfers to one it has not seen.",
  );
  limits.push(
    "The synthetic panel is a simulation, not a sample. Its spread reflects the run size " +
      "recorded under Conditions above, not the variance of any population of shoppers, so " +
      "it must not be read as a confidence interval.",
  );
  limits.push(
    "Nothing here is a causal claim. This document compares a prediction against an " +
      "observation; it does not establish that any shelf position or ad placement caused any " +
      "behaviour.",
  );

  if (result.mode === "cursor_only") {
    limits.push(
      "No claim about gaze can be read from this document. The capture mode was cursor_only " +
        "and the eye tracker contributed nothing.",
    );
  }

  return limits;
}

// ---------------------------------------------------------------------------
// The report data, shared by both exports
// ---------------------------------------------------------------------------

export interface ReportInput {
  result: ExperimentResult;
  lock: PredictionLock;
  /** ISO-8601 instant the document was produced. */
  generatedAt: string;
}

export interface ReportSlotRow {
  slot_id: string;
  /** null when the real side was not captured, or the slot carries no figure. */
  real: number | null;
  synth: number | null;
}

export interface ReportJson {
  report_version: number;
  generated_at: string;
  experiment_id: string;
  session_id: string;
  variant_id: string;
  capture: { mode: string | null; gaze_measured: boolean | null };
  pre_registration: PredictionLock;
  conditions: { n_synth: number | null; seed: number | null };
  real_panel_captured: boolean;
  metrics: {
    attention_spearman: number | null;
    purchase_share_mae: number | null;
  };
  attention_by_slot: ReportSlotRow[];
  purchase_share: {
    real: Record<string, number> | null;
    synth: Record<string, number>;
  };
  limits: string[];
}

function finite(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/**
 * A headline metric, or null.
 *
 * Two independent reasons a metric is not a figure, and both end here:
 * it is not a finite number, or the real side it compares against was never
 * captured. The second is the one that matters -- `analytics/metrics.py`
 * guards its ratios and returns 0.0 for an empty comparison, so an unrecorded
 * session yields a perfectly finite 0 that means nothing at all.
 */
function headlineMetric(value: number, captured: boolean): number | null {
  return captured ? finite(value) : null;
}

/** Whether gaze contributed, or null when the mode was not recorded. */
function gazeMeasured(mode: string | undefined): boolean | null {
  if (mode === "webcam") return true;
  if (mode === "cursor_only") return false;
  return null;
}

function slotRows(result: ExperimentResult, captured: boolean): ReportSlotRow[] {
  const slotIds = Array.isArray(result.slot_ids) ? result.slot_ids : [];
  return slotIds.map((slot_id) => ({
    slot_id,
    real: captured ? finite((result.real_attention ?? {})[slot_id]) : null,
    synth: finite((result.synth_attention ?? {})[slot_id]),
  }));
}

/**
 * The machine-readable twin of the printed report -- the same decisions, so
 * the two cannot disagree about what was and was not measured.
 *
 * Every field is JSON-native: no `undefined`, no `NaN`. An absent figure is
 * `null`, which survives `JSON.stringify` and means the same thing there that
 * "not applicable" means on the printed page.
 */
export function buildReportJson({ result, lock, generatedAt }: ReportInput): ReportJson {
  const captured = realPanelCaptured(result);
  return {
    report_version: 1,
    generated_at: generatedAt,
    experiment_id: result.experiment_id,
    session_id: result.session_id,
    variant_id: result.variant_id,
    capture: {
      mode: result.mode ?? null,
      gaze_measured: gazeMeasured(result.mode),
    },
    pre_registration: {
      prediction_id: lock.prediction_id,
      sha256_prefix: lock.sha256_prefix,
      created_at: lock.created_at,
      sim_run_id: lock.sim_run_id,
    },
    conditions: { n_synth: finite(result.n_synth), seed: finite(result.seed) },
    real_panel_captured: captured,
    metrics: {
      attention_spearman: headlineMetric(result.attention_spearman, captured),
      purchase_share_mae: headlineMetric(result.purchase_share_mae, captured),
    },
    attention_by_slot: slotRows(result, captured),
    purchase_share: {
      real: captured ? (result.real_purchase_share ?? {}) : null,
      synth: result.synth_purchase_share ?? {},
    },
    limits: limitsFor(result, captured),
  };
}

// ---------------------------------------------------------------------------
// The printed document
// ---------------------------------------------------------------------------

/**
 * HTML-escape a value on its way into the document.
 *
 * Everything interpolated below came off the wire -- session ids, variant ids,
 * slot ids, the capture mode -- and none of it is trusted markup. Apostrophes
 * are deliberately left alone: no interpolated value lands inside a
 * single-quoted attribute (there are none in this document), and escaping them
 * would mangle the prose in the statement constants above.
 */
function escape(value: unknown): string {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** A value, or the same "not applicable" the live page uses for an absent one. */
function orAbsent(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") {
    return `<span class="absent">${NOT_APPLICABLE}</span>`;
  }
  return escape(value);
}

function metricCell(value: number | null, digits: number): string {
  if (value === null) return `<span class="absent">${NOT_APPLICABLE}</span>`;
  return escape(formatMetric(value, digits));
}

function attentionCell(value: number | null): string {
  if (value === null) return `<span class="absent">${NOT_APPLICABLE}</span>`;
  return escape(value.toFixed(4));
}

function difference(row: ReportSlotRow): string {
  if (row.real === null || row.synth === null) {
    return `<span class="absent">${NOT_APPLICABLE}</span>`;
  }
  const delta = row.real - row.synth;
  const sign = delta >= 0 ? "+" : "";
  return escape(`${sign}${delta.toFixed(4)}`);
}

/**
 * The stylesheet, inline, because the document has to render from a single
 * file on a machine that has never heard of this project.
 *
 * It is light. `styles.ts:PAPER` argues why at length: the delivery mechanism
 * for this artefact is the browser's own print-to-PDF, and the dark palette
 * every screen in this app uses either soaks a page in toner or -- because
 * browsers drop backgrounds when printing by default -- arrives as pale grey
 * text on white paper. Real stays blue and synthetic stays amber, darkened for
 * legible contrast on paper, so a reader who saw the dashboard recognises the
 * document.
 */
function stylesheet(): string {
  return `
    :root { color-scheme: light; }
    * { box-sizing: border-box; }
    html, body { margin: 0; padding: 0; }
    body {
      background: ${PAPER};
      color: ${PAPER_INK};
      font-family: system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
      font-size: 15px;
      line-height: 1.5;
    }
    .sheet { max-width: 820px; margin: 0 auto; padding: 40px 34px 64px; }
    .mono { font-family: ui-monospace, SFMono-Regular, Consolas, Menlo, monospace; }
    .absent { color: ${PAPER_MUTED}; font-style: italic; }

    .masthead { border-bottom: 2px solid ${PAPER_INK}; padding-bottom: 14px; }
    .eyebrow {
      margin: 0 0 6px; font-size: 11px; font-weight: 700;
      letter-spacing: 0.14em; text-transform: uppercase; color: ${PAPER_MUTED};
    }
    h1 { margin: 0 0 4px; font-size: 25px; line-height: 1.25; }
    h1 .mono { font-size: 20px; }
    .chip {
      display: inline-block; margin-top: 10px; padding: 3px 10px; border-radius: 999px;
      border: 1px solid ${PAPER_RULE}; font-size: 12px; font-weight: 700;
      letter-spacing: 0.06em; text-transform: uppercase;
    }
    .chip--warn { border-color: ${SYNTH_PRINT}; color: ${SYNTH_PRINT}; }
    .chip--ok { border-color: ${REAL_PRINT}; color: ${REAL_PRINT}; }

    section { margin-top: 30px; }
    h2 {
      margin: 0 0 12px; font-size: 12px; font-weight: 700;
      letter-spacing: 0.13em; text-transform: uppercase; color: ${PAPER_MUTED};
      border-bottom: 1px solid ${PAPER_RULE}; padding-bottom: 6px;
    }
    p { margin: 0 0 10px; max-width: 68ch; }
    .footnote { font-size: 12.5px; color: ${PAPER_MUTED}; }

    .hash {
      margin: 4px 0 14px; padding: 14px 16px;
      border: 2px solid ${PAPER_INK}; border-radius: 6px; background: ${PAPER};
    }
    .hash-label {
      display: block; font-size: 11px; font-weight: 700;
      letter-spacing: 0.1em; text-transform: uppercase; color: ${PAPER_MUTED};
    }
    .hash-value {
      display: block; margin-top: 4px; font-size: 30px; font-weight: 700; letter-spacing: 0.1em;
    }

    .callout {
      margin: 0 0 14px; padding: 12px 16px; border-left: 5px solid ${PAPER_RULE};
      background: #f4f6f9; border-radius: 0 6px 6px 0;
    }
    .callout p:last-child { margin-bottom: 0; }
    .callout--warn { border-left-color: ${SYNTH_PRINT}; background: #fdf3e3; }
    .callout--ok { border-left-color: ${REAL_PRINT}; background: #eef3fd; }
    .callout-head {
      margin: 0 0 6px; font-size: 11px; font-weight: 700;
      letter-spacing: 0.1em; text-transform: uppercase;
    }
    .callout--warn .callout-head { color: ${SYNTH_PRINT}; }
    .callout--ok .callout-head { color: ${REAL_PRINT}; }

    dl.facts { display: grid; grid-template-columns: max-content 1fr; gap: 6px 18px; margin: 0; }
    dl.facts dt {
      font-size: 11px; font-weight: 700; letter-spacing: 0.07em;
      text-transform: uppercase; color: ${PAPER_MUTED}; align-self: center;
    }
    dl.facts dd { margin: 0; align-self: center; }

    .figures { display: flex; flex-wrap: wrap; gap: 34px; margin-bottom: 18px; }
    .figure-label {
      font-size: 11px; font-weight: 700; letter-spacing: 0.08em;
      text-transform: uppercase; color: ${PAPER_MUTED};
    }
    .figure-value { font-size: 34px; font-weight: 700; line-height: 1.15; }
    .figure-value .absent { font-size: 17px; font-weight: 400; }

    table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
    caption {
      caption-side: top; text-align: left; padding-bottom: 8px;
      font-size: 12.5px; color: ${PAPER_MUTED};
    }
    th, td { padding: 6px 10px; text-align: right; border-bottom: 1px solid ${PAPER_RULE}; }
    th:first-child, td:first-child { text-align: left; }
    thead th {
      font-size: 11px; letter-spacing: 0.07em; text-transform: uppercase;
      border-bottom: 1.5px solid ${PAPER_INK};
    }
    th.real { color: ${REAL_PRINT}; }
    th.synth { color: ${SYNTH_PRINT}; }

    ul.limits { margin: 0; padding-left: 20px; }
    ul.limits li { margin-bottom: 9px; max-width: 68ch; }

    footer {
      margin-top: 34px; padding-top: 12px; border-top: 1px solid ${PAPER_RULE};
      font-size: 12px; color: ${PAPER_MUTED};
    }

    @page { margin: 16mm; }
    @media print {
      body { font-size: 10.5pt; }
      .sheet { max-width: none; margin: 0; padding: 0; }
      section, table, .hash, .callout { break-inside: avoid; page-break-inside: avoid; }
      h2 { break-after: avoid; page-break-after: avoid; }
      .hash-value { font-size: 22pt; }
      .figure-value { font-size: 22pt; }
      /* The mode callout is the one thing on this page that must survive a
         print. Its tint is what makes a cursor_only session visible at a
         glance, and browsers drop backgrounds when printing unless told. */
      * { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
    }
  `;
}

function preRegistrationSection(lock: PredictionLock): string {
  if (lock.sha256_prefix === null) {
    return `
      <section>
        <h2>1 &middot; Pre-registration</h2>
        <div class="callout callout--warn">
          <p class="callout-head">No pre-registration on file</p>
          <p>${escape(NO_LOCK_NOTICE)}</p>
        </div>
      </section>`;
  }

  return `
    <section>
      <h2>1 &middot; Pre-registration</h2>
      <div class="hash">
        <span class="hash-label">SHA-256 of the locked prediction, first 8 hex characters</span>
        <span class="hash-value mono">${escape(lock.sha256_prefix)}</span>
      </div>
      <p>${escape(ORDERING_GUARANTEE)}</p>
      <dl class="facts">
        <dt>Locked at</dt><dd class="mono">${orAbsent(lock.created_at)}</dd>
        <dt>Prediction id</dt><dd class="mono">${orAbsent(lock.prediction_id)}</dd>
        <dt>Simulation run id</dt><dd class="mono">${orAbsent(lock.sim_run_id)}</dd>
      </dl>
      <p class="footnote">
        The full 64-character digest, and the per-slot prediction it covers, are in the
        committed lock file for this session under the repository directory
        <span class="mono">predictions/</span>. This report reproduces the first eight
        characters, which is what the API serves.
      </p>
    </section>`;
}

function modeCallout(mode: string | undefined): string {
  if (mode === "webcam") {
    return `
      <div class="callout callout--ok">
        <p class="callout-head">Capture mode: webcam</p>
        <p>${escape(GAZE_MEASURED)}</p>
      </div>`;
  }
  if (mode === "cursor_only") {
    return `
      <div class="callout callout--warn">
        <p class="callout-head">Capture mode: cursor_only &mdash; gaze was not measured</p>
        <p>${escape(GAZE_NOT_MEASURED)}</p>
      </div>`;
  }
  return `
    <div class="callout callout--warn">
      <p class="callout-head">Capture mode: not recorded</p>
      <p>${escape(MODE_NOT_RECORDED)}</p>
    </div>`;
}

function slotTable(rows: ReportSlotRow[], captured: boolean): string {
  if (!captured) {
    const body = rows
      .map(
        (row) => `
          <tr>
            <td class="mono">${escape(row.slot_id)}</td>
            <td class="mono">${attentionCell(row.synth)}</td>
          </tr>`,
      )
      .join("");
    return `
      <table>
        <caption>
          Per-slot attention. The real column is omitted rather than filled with zeros:
          there is no real measurement for this session.
        </caption>
        <thead>
          <tr><th>Slot</th><th class="synth">Synthetic attention</th></tr>
        </thead>
        <tbody>${body}</tbody>
      </table>`;
  }

  const body = rows
    .map(
      (row) => `
        <tr>
          <td class="mono">${escape(row.slot_id)}</td>
          <td class="mono">${attentionCell(row.real)}</td>
          <td class="mono">${attentionCell(row.synth)}</td>
          <td class="mono">${difference(row)}</td>
        </tr>`,
    )
    .join("");

  return `
    <table>
      <caption>
        Per-slot attention, real against synthetic, over the shared slot vocabulary of the
        resolved planogram. The last column is real minus synthetic.
      </caption>
      <thead>
        <tr>
          <th>Slot</th>
          <th class="real">Real attention</th>
          <th class="synth">Synthetic attention</th>
          <th>Real &minus; synthetic</th>
        </tr>
      </thead>
      <tbody>${body}</tbody>
    </table>`;
}

/**
 * The self-contained HTML report for one session.
 *
 * A single file: stylesheet inline, no script, no image, no font, no network
 * request of any kind. `web/tests/dashboardReport.test.ts` asserts that, and it
 * matters more than it sounds -- the document has to open from a USB stick in
 * three years, and it has to print.
 */
export function buildReportHtml(input: ReportInput): string {
  const { result, lock, generatedAt } = input;
  const body = buildReportJson(input);
  const captured = body.real_panel_captured;

  const modeChip =
    result.mode === "webcam"
      ? `<span class="chip chip--ok">Capture mode: ${escape(result.mode)}</span>`
      : `<span class="chip chip--warn">Capture mode: ${orAbsent(result.mode)}</span>`;

  const absenceNotice = captured
    ? ""
    : `
      <div class="callout callout--warn">
        <p class="callout-head">No real panel in this session</p>
        <p>${escape(REAL_PANEL_ABSENT)}</p>
      </div>`;

  const limitItems = body.limits
    .map((limit) => `<li>${escape(limit)}</li>`)
    .join("");

  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ShopperTwin session report ${escape(result.session_id)}</title>
<style>${stylesheet()}</style>
</head>
<body>
<main class="sheet">

  <header class="masthead">
    <p class="eyebrow">ShopperTwin &middot; session report</p>
    <h1>Session <span class="mono">${escape(result.session_id)}</span></h1>
    <dl class="facts">
      <dt>Variant</dt><dd class="mono">${orAbsent(result.variant_id)}</dd>
      <dt>Experiment</dt><dd class="mono">${orAbsent(result.experiment_id)}</dd>
      <dt>Generated</dt><dd class="mono">${orAbsent(generatedAt)}</dd>
    </dl>
    ${modeChip}
  </header>

  ${preRegistrationSection(lock)}

  <section>
    <h2>2 &middot; Conditions</h2>
    ${modeCallout(result.mode)}
    <dl class="facts">
      <dt>Capture mode</dt><dd class="mono">${orAbsent(result.mode)}</dd>
      <dt>Synthetic shoppers per persona</dt>
      <dd class="mono">${orAbsent(body.conditions.n_synth)}</dd>
      <dt>Simulation seed</dt><dd class="mono">${orAbsent(body.conditions.seed)}</dd>
    </dl>
  </section>

  <section>
    <h2>3 &middot; Results</h2>
    ${absenceNotice}
    <div class="figures">
      <div>
        <div class="figure-label">Attention Spearman</div>
        <div class="figure-value mono">${metricCell(body.metrics.attention_spearman, 3)}</div>
      </div>
      <div>
        <div class="figure-label">Purchase-share MAE</div>
        <div class="figure-value mono">${metricCell(body.metrics.purchase_share_mae, 4)}</div>
      </div>
    </div>
    ${slotTable(body.attention_by_slot, captured)}
  </section>

  <section>
    <h2>4 &middot; What these numbers cannot support</h2>
    <ul class="limits">${limitItems}</ul>
  </section>

  <footer>
    Generated by the ShopperTwin dashboard from experiment
    <span class="mono">${escape(result.experiment_id)}</span>. Every figure above was computed
    by the API; nothing in this document was written by a language model.
  </footer>

</main>
</body>
</html>
`;
}

/** A filename that names the session, safe on every filesystem. */
export function reportFilename(
  result: ExperimentResult,
  extension: "html" | "json",
): string {
  const safe = String(result.session_id).replace(/[^A-Za-z0-9._-]/g, "_");
  return `shoppertwin-session-${safe}.${extension}`;
}
