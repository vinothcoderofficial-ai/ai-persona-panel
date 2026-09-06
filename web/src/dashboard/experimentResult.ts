/**
 * What `POST /experiments` returns, and the two rules the dashboard applies to
 * it before anything reaches a screen or a document.
 *
 * This module is deliberately pure -- no React, no fetch -- because both the
 * live page (`Experiment.tsx`) and the exported session report
 * (`report.ts`) have to agree about what a figure is, and the report is read
 * months later by someone with no app running. One copy of the rule, two
 * consumers.
 */

/**
 * `POST /experiments`' response shape (S5 task brief, decision 4). This
 * deliberately does NOT satisfy schemas/metrics.schema.json -- that schema is
 * the full cross-variant evaluation (noise ceiling, decision agreement,
 * holdout variants) that is S17-S19's job, so there is no generated contract
 * type for it yet. This interface is the dashboard's own honest description of
 * what the endpoint actually returns today.
 */
export interface ExperimentResult {
  experiment_id: string;
  variant_id: string;
  session_id: string;
  /**
   * The session's capture mode -- `"cursor_only"` or `"webcam"` per
   * schemas/session.schema.json -- which `_build_experiment` used to select
   * the fusion weights for both panels.
   *
   * Optional, and that is not laziness. `GET /experiments/{id}` replays a
   * stored `ExperimentRecord` verbatim, and a record persisted before this
   * field existed comes back without it. A report that guessed a mode in that
   * case would be asserting whether gaze was measured on no evidence at all,
   * which is worse than saying nothing.
   */
  mode?: string;
  n_synth: number;
  seed: number;
  slot_ids: string[];
  real_attention: Record<string, number>;
  synth_attention: Record<string, number>;
  attention_spearman: number;
  purchase_share_mae: number;
  real_purchase_share: Record<string, number>;
  synth_purchase_share: Record<string, number>;
}

/** The words shown wherever a headline metric could not be computed. */
export const NOT_APPLICABLE = "not applicable";

/**
 * A headline metric as fixed-precision text, or `NOT_APPLICABLE` when there
 * is no real number behind it.
 *
 * `ExperimentResult.attention_spearman` and `.purchase_share_mae` are typed
 * above as required numbers, and in practice they always are one:
 * `analytics/metrics.py` guards both against `NaN` and returns 0.0 rather
 * than an undefined ratio (see that module's docstrings), so nothing in this
 * endpoint's own maths ever produces a missing value. But `ExperimentResult`
 * is -- per its docstring -- the dashboard's own honest description of the
 * endpoint, not a generated, checked contract: `(await res.json()) as
 * ExperimentResult` is a type assertion, not a validation, and an
 * `ExperimentRecord` persisted before one of these fields existed would come
 * back over the wire without it. Calling `.toFixed()` on that `undefined`
 * would crash the page.
 *
 * So this applies the same rule `web/src/whatif/lift.ts:formatLift` uses for
 * the what-if panel's own figures: only a finite number is a figure, and
 * anything else -- missing, `null`, `NaN` -- is "not applicable", never a
 * fabricated 0. A computed 0 (e.g. no rank correlation at all) is a real
 * result and is shown as one, not caught by the same net.
 */
export function formatMetric(value: number, digits: number): string {
  return Number.isFinite(value) ? value.toFixed(digits) : NOT_APPLICABLE;
}

function anyPositive(values: Record<string, number> | undefined): boolean {
  if (values === undefined || values === null) return false;
  return Object.values(values).some((value) => Number.isFinite(value) && value > 0);
}

/**
 * Whether a real shopper was actually measured in this session.
 *
 * A session that recorded no events still produces a complete-looking
 * response: `fuse_session` returns 0.0 for every slot in the vocabulary
 * (its docstring says so -- every id is a key, even at zero),
 * `_real_purchase_share` returns `{}`, and `analytics/metrics.py` guards its
 * ratios and hands back 0.0 rather than an undefined one. Every field is
 * present and every field is a number, and not one of them is a measurement.
 *
 * Printing that as `0.000` in a document somebody keeps would be the exact
 * artefact this project exists not to produce: a reader months later cannot
 * tell a shopper who looked at nothing from a shopper who was never recorded.
 * So the whole real side is gated on this predicate, and when it is false the
 * report says the real side was not captured rather than showing its zeros.
 *
 * Deliberately not "were there events" -- the response carries no event count.
 * This is the strongest honest statement the returned document supports: no
 * slot drew any real attention and nothing was bought, which is precisely what
 * an unrecorded session looks like from here.
 */
export function realPanelCaptured(result: ExperimentResult): boolean {
  return anyPositive(result.real_attention) || anyPositive(result.real_purchase_share);
}
