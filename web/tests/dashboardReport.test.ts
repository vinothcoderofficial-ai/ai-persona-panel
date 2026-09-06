import { describe, expect, it, vi } from "vitest";
import {
  NOT_APPLICABLE,
  realPanelCaptured,
  type ExperimentResult,
} from "@/dashboard/experimentResult";
import {
  GAZE_MEASURED,
  GAZE_NOT_MEASURED,
  MODE_NOT_RECORDED,
  NO_LOCK_NOTICE,
  NO_PREDICTION_LOCK,
  ORDERING_GUARANTEE,
  REAL_PANEL_ABSENT,
  buildReportHtml,
  buildReportJson,
  fetchPredictionLock,
  lockFromPredictionResponse,
  reportFilename,
  type PredictionLock,
} from "@/dashboard/report";

/**
 * The session report is a document somebody keeps. Months later, with no app
 * running, it has to still say what was and was not measured -- so these tests
 * are about the claims the report makes, not about its markup.
 *
 * Five of them are the whole reason the feature exists:
 *
 *  1. The pre-registration reaches the page: the hash and the lock time.
 *  2. A `cursor_only` session says, in words, that it did not measure gaze.
 *  3. A session with no recorded events reports the real side as ABSENT --
 *     never as a table of 0.000 that reads like a measurement of zero.
 *  4. A non-finite metric reads as "not applicable", never as a fabricated 0,
 *     while a genuine computed 0 still prints as 0.
 *  5. The JSON export carries exactly the values the HTML displays, so the
 *     machine-readable evidence and the printed evidence cannot disagree.
 */

const LOCK: PredictionLock = {
  prediction_id: "pred_4c9a1f77b0e2",
  sha256_prefix: "9f3ab21c",
  created_at: "2026-09-04T10:32:07.412Z",
  sim_run_id: "run_71b0c2d4",
};

const GENERATED_AT = "2026-09-04T11:04:19.000Z";

/** A finished webcam session with a real panel behind it. */
const MEASURED: ExperimentResult = {
  experiment_id: "exp_20260904_1a2b3c",
  variant_id: "var_eye_level_shift",
  session_id: "sess_9f8e7d6c5b4a3928",
  mode: "webcam",
  n_synth: 10_000,
  seed: 42,
  slot_ids: ["B1S3P1", "B1S3P2", "B1S3P3"],
  real_attention: { B1S3P1: 0.41, B1S3P2: 0.22, B1S3P3: 0.11 },
  synth_attention: { B1S3P1: 0.37, B1S3P2: 0.28, B1S3P3: 0.09 },
  attention_spearman: 0.482,
  purchase_share_mae: 0.0134,
  real_purchase_share: { SKU_001: 0.6, SKU_002: 0.4 },
  synth_purchase_share: { SKU_001: 0.55, SKU_002: 0.45 },
};

/** The same session, captured with the eye tracker never having started. */
const CURSOR_ONLY: ExperimentResult = { ...MEASURED, mode: "cursor_only" };

/**
 * Exactly what `_build_experiment` returns for a session that recorded no
 * events: `fuse_session` fills every slot with 0.0 and `_real_purchase_share`
 * returns {}, and `analytics/metrics.py` guards its ratios and hands back 0.0
 * rather than an undefined one. Nothing here was measured.
 */
const NO_EVENTS: ExperimentResult = {
  ...MEASURED,
  real_attention: { B1S3P1: 0, B1S3P2: 0, B1S3P3: 0 },
  real_purchase_share: {},
  attention_spearman: 0,
  purchase_share_mae: 0,
};

function report(result: ExperimentResult, lock: PredictionLock = LOCK): string {
  return buildReportHtml({ result, lock, generatedAt: GENERATED_AT });
}

function json(result: ExperimentResult, lock: PredictionLock = LOCK) {
  return buildReportJson({ result, lock, generatedAt: GENERATED_AT });
}

describe("pre-registration", () => {
  it("prints the locked prediction's hash and the time it was locked", () => {
    const html = report(MEASURED);

    expect(html).toContain(LOCK.sha256_prefix as string);
    expect(html).toContain(LOCK.created_at as string);
    expect(html).toContain(LOCK.prediction_id as string);
    expect(html).toContain(LOCK.sim_run_id as string);
  });

  it("states the ordering guarantee in plain words", () => {
    expect(report(MEASURED)).toContain(ORDERING_GUARANTEE);
  });

  it("says so plainly when no lock could be read, and invents no hash", () => {
    const html = report(MEASURED, NO_PREDICTION_LOCK);

    expect(html).toContain(NO_LOCK_NOTICE);
    expect(html).not.toContain(LOCK.sha256_prefix as string);
    // The ordering guarantee is evidence about a lock. With no lock there is
    // nothing for it to be evidence about, so it must not be asserted.
    expect(html).not.toContain(ORDERING_GUARANTEE);
  });

  it("reads the lock out of the prediction endpoint's own response shape", () => {
    const lock = lockFromPredictionResponse({
      prediction_id: LOCK.prediction_id,
      sim_run_id: LOCK.sim_run_id,
      created_at: LOCK.created_at,
      sha256_prefix: LOCK.sha256_prefix,
      population_fixation_prob: { B1S3P1: 0.2 },
    });

    expect(lock).toEqual(LOCK);
  });

  it("returns no lock rather than a truncated string when the hash is not hex", () => {
    const lock = lockFromPredictionResponse({
      prediction_id: "pred_x",
      created_at: LOCK.created_at,
      sha256_prefix: "not-a-hash",
    });

    expect(lock.sha256_prefix).toBeNull();
  });

  it("returns no lock when the endpoint is unreachable, never a fabricated one", async () => {
    const failing = vi.fn(async () => {
      throw new Error("connection refused");
    });

    await expect(
      fetchPredictionLock("sess_1", failing as unknown as typeof fetch),
    ).resolves.toEqual(NO_PREDICTION_LOCK);
  });
});

describe("capture mode", () => {
  it("says in plain words that a cursor_only session did not measure gaze", () => {
    const html = report(CURSOR_ONLY);

    expect(html).toContain("cursor_only");
    expect(html).toContain(GAZE_NOT_MEASURED);
    expect(html).not.toContain(GAZE_MEASURED);
  });

  it("says that a webcam session's real attention includes gaze", () => {
    const html = report(MEASURED);

    expect(html).toContain(GAZE_MEASURED);
    expect(html).not.toContain(GAZE_NOT_MEASURED);
  });

  it("says the mode is unrecorded rather than assuming one", () => {
    const { mode, ...withoutMode } = MEASURED;
    void mode;
    const html = report(withoutMode as ExperimentResult);

    expect(html).toContain(MODE_NOT_RECORDED);
    expect(html).not.toContain(GAZE_MEASURED);
    expect(html).not.toContain(GAZE_NOT_MEASURED);
  });

  it("carries the mode and whether gaze was measured into the JSON export", () => {
    expect(json(MEASURED).capture).toEqual({ mode: "webcam", gaze_measured: true });
    expect(json(CURSOR_ONLY).capture).toEqual({
      mode: "cursor_only",
      gaze_measured: false,
    });

    const { mode, ...withoutMode } = MEASURED;
    void mode;
    expect(json(withoutMode as ExperimentResult).capture).toEqual({
      mode: null,
      gaze_measured: null,
    });
  });
});

describe("a session that recorded nothing", () => {
  it("is detected as having no real panel behind it", () => {
    expect(realPanelCaptured(MEASURED)).toBe(true);
    expect(realPanelCaptured(NO_EVENTS)).toBe(false);
  });

  it("reports the real side as absent rather than as zeros", () => {
    const html = report(NO_EVENTS);

    expect(html).toContain(REAL_PANEL_ABSENT);
    // The slot table must not carry a real column of 0.0000 that a reader
    // could mistake for a measurement of zero attention.
    expect(html).not.toContain("0.0000");
  });

  it("withholds both headline metrics rather than printing their guarded 0", () => {
    const html = report(NO_EVENTS);
    const body = json(NO_EVENTS);

    expect(body.metrics.attention_spearman).toBeNull();
    expect(body.metrics.purchase_share_mae).toBeNull();
    expect(html).toContain(NOT_APPLICABLE);
    expect(html).not.toContain("0.000<");
  });

  it("leaves the real column of every slot row null in the JSON export", () => {
    const rows = json(NO_EVENTS).attention_by_slot;

    expect(rows).toHaveLength(3);
    for (const row of rows) {
      expect(row.real).toBeNull();
      expect(typeof row.synth).toBe("number");
    }
  });

  it("still reports the synthetic side, which was genuinely computed", () => {
    const html = report(NO_EVENTS);
    expect(html).toContain("0.3700");
  });
});

describe("metric honesty", () => {
  it("prints a genuine computed zero as a figure, not as absent", () => {
    const zeroSpearman = { ...MEASURED, attention_spearman: 0 };
    const html = report(zeroSpearman);

    expect(html).toContain("0.000");
    expect(json(zeroSpearman).metrics.attention_spearman).toBe(0);
  });

  it("prints a metric missing from the response as not applicable, never as 0", () => {
    const { attention_spearman, ...withoutSpearman } = MEASURED;
    void attention_spearman;
    const result = withoutSpearman as ExperimentResult;

    expect(report(result)).toContain(NOT_APPLICABLE);
    expect(json(result).metrics.attention_spearman).toBeNull();
    // The other metric is unaffected: one absent figure is not a broken report.
    expect(json(result).metrics.purchase_share_mae).toBe(0.0134);
  });

  it("prints a NaN metric as not applicable", () => {
    const result = { ...MEASURED, purchase_share_mae: Number.NaN };

    expect(json(result).metrics.purchase_share_mae).toBeNull();
    expect(report(result)).toContain(NOT_APPLICABLE);
  });
});

describe("the JSON export round-trips what the HTML displays", () => {
  it("carries the same identifiers, conditions, lock and metrics", () => {
    const body = json(MEASURED);
    const html = report(MEASURED);

    expect(body.experiment_id).toBe(MEASURED.experiment_id);
    expect(body.session_id).toBe(MEASURED.session_id);
    expect(body.variant_id).toBe(MEASURED.variant_id);
    expect(body.generated_at).toBe(GENERATED_AT);
    expect(body.conditions).toEqual({ n_synth: 10_000, seed: 42 });
    expect(body.pre_registration).toEqual(LOCK);
    expect(body.real_panel_captured).toBe(true);
    expect(body.metrics).toEqual({
      attention_spearman: 0.482,
      purchase_share_mae: 0.0134,
    });

    // Every value the JSON claims is on the page is on the page.
    for (const value of [
      body.experiment_id,
      body.session_id,
      body.variant_id,
      body.generated_at,
      String(body.conditions.n_synth),
      String(body.conditions.seed),
      body.pre_registration.sha256_prefix,
      body.pre_registration.created_at,
    ]) {
      expect(html).toContain(value as string);
    }
  });

  it("pairs every slot's real and synthetic attention in slot order", () => {
    const rows = json(MEASURED).attention_by_slot;

    expect(rows.map((row) => row.slot_id)).toEqual(MEASURED.slot_ids);
    expect(rows[0]).toEqual({ slot_id: "B1S3P1", real: 0.41, synth: 0.37 });
  });

  it("survives a JSON round trip unchanged", () => {
    const body = json(MEASURED);
    expect(JSON.parse(JSON.stringify(body))).toEqual(body);
  });

  it("carries the same limits the HTML prints", () => {
    const body = json(MEASURED);
    const html = report(MEASURED);

    expect(body.limits.length).toBeGreaterThan(0);
    for (const limit of body.limits) expect(html).toContain(limit);
  });
});

describe("the document stands alone", () => {
  const html = report(MEASURED);

  it("requests nothing over the network", () => {
    expect(html).not.toMatch(/<script/i);
    expect(html).not.toMatch(/<link\b/i);
    expect(html).not.toMatch(/https?:\/\//i);
    expect(html).not.toMatch(/url\(/i);
  });

  it("carries its own stylesheet, including a print stylesheet", () => {
    expect(html).toMatch(/<style>/i);
    expect(html).toContain("@media print");
    expect(html).toContain("@page");
  });

  it("is a complete HTML document", () => {
    expect(html.startsWith("<!doctype html>")).toBe(true);
    expect(html.trimEnd().endsWith("</html>")).toBe(true);
    expect(html).toContain("<title>");
  });

  it("escapes values that came from the API rather than injecting them", () => {
    const hostile = {
      ...MEASURED,
      session_id: '<script>alert("x")</script>',
    };
    const out = report(hostile);

    expect(out).not.toContain("<script>");
    expect(out).toContain("&lt;script&gt;");
  });

  it("names its file after the session it describes", () => {
    expect(reportFilename(MEASURED, "html")).toBe(
      "shoppertwin-session-sess_9f8e7d6c5b4a3928.html",
    );
    expect(reportFilename(MEASURED, "json")).toBe(
      "shoppertwin-session-sess_9f8e7d6c5b4a3928.json",
    );
    expect(reportFilename({ ...MEASURED, session_id: "a/b c" }, "html")).toBe(
      "shoppertwin-session-a_b_c.html",
    );
  });
});
