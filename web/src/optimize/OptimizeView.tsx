import { useCallback, useEffect, useState } from "react";
import type { CSSProperties } from "react";
import { AiRequestError, type FetchLike } from "@/ai/client";
import * as style from "@/ai/styles";

/**
 * `#/optimize` — the recommendation, on a screen.
 *
 * `analytics/optimizer.py` has scored every placement, ranked them, named the
 * best and reported where the current one sits since S24, and
 * `analytics/slot_value.py` could price the difference. Neither had a route or
 * a screen, so the strongest claim this project makes — that it is a
 * recommendation engine rather than an A/B testing tool, because it models
 * purchase and not only attention — was invisible from the running product.
 *
 * The whole difficulty here is not showing a ranking; it is showing one without
 * over-claiming. A table of percentages sorted descending reads as certainty,
 * and at any run size the leaders routinely sit inside each other's Monte Carlo
 * spread. So this screen is built around four refusals, each of which the
 * server already reports and a careless table would drop:
 *
 *  1. **An undefined objective reads as undefined.** A creative taken down has
 *     no ad-to-purchase lift. Printing 0% would rank "no advertising" as a
 *     measured, mediocre option instead of an unanswerable question.
 *  2. **The range is a seed spread, and says so.** Not a confidence interval —
 *     `SeedSpread` and docs/METHODOLOGY.md §12.7 explain why there is no honest
 *     CI available — and it carries the seeds, because a range quoted without
 *     its count says nothing.
 *  3. **An unresolved winner is announced as unresolved.** If the top pick's
 *     spread overlaps another row's, the two are not ranked against each other
 *     and the screen says which rows, by name.
 *  4. **Skipped candidates are listed.** A shelf level a bay does not have is
 *     not a move that lost.
 *  5. **A question that was not asked is not answered "no".** `beats_current`
 *     is null when the comparison could never be made — nothing in the space
 *     reproduces today's planogram, or the current placement has no seed range
 *     of its own for anything to clear. This screen printed the empty list as
 *     "No placement clears the current one's spread either", and the server
 *     sent `[]` for both cases, so on the committed aisle an unanswered
 *     question was rendered as a definite negative for 23 of the 24 focal
 *     SKUs. Null now travels all the way here, `normalise` preserves it, and
 *     there is a third branch that says the comparison was not made.
 *  6. **The number says which estimator produced it.** `analytics/lift.py`
 *     carries two Brand Lifts; on this aisle the within-run split reports
 *     roughly five times what the between-arm comparison does, because
 *     within-run "ad exposed" is a selection and not a randomisation. A
 *     percentage with no estimator beside it is an unlabelled percentage, so
 *     `objective_caveat` is printed verbatim from the library that made the
 *     ranking — not paraphrased here, for the same reason the summary is not.
 *  7. **Each row carries its own spread, and says UNRESOLVED when that spread
 *     contains no effect at all.** A row can outrank six others and still not
 *     have been shown to do anything; sorting descending is not evidence. A
 *     row with no spread at all says so instead, because "not measured" and
 *     "measured and straddling zero" are different answers.
 *
 * The summary sentence is printed verbatim from `optimizer.summary()` rather
 * than composed here: the CLI, `RESULTS.md` and this screen have to say the
 * same thing, and three renderings of one result is how they stop.
 */

export interface OptimizeViewProps {
  fetchImpl?: FetchLike;
  /** Which arm to rank against. Defaults to the unpatched baseline. */
  variantId?: string;
  creativeId?: string;
}

const defaultFetch: FetchLike = (input, init) => fetch(input, init);

interface SeedSpread {
  seeds: number[];
  values: number[];
  low: number;
  high: number;
  n_seeds: number;
  width: number;
}

interface Entry {
  rank: number;
  candidate_id: string;
  kind: string;
  label: string;
  /*
   * `patches` is deliberately not declared. The server sends one per entry and
   * nothing here can act on it: the only place that could is `#/whatif`, which
   * `main.tsx` renders as a bare `<WhatIfPanel />` and which reads no query
   * string, no hash and no props — its selection starts empty, whatever the
   * address bar says. Typing a field this screen cannot use was what made a
   * per-row "Try it" button look implementable when it was not, so the field
   * is left off the interface until the panel can receive it.
   */
  /** Null when the metric does not exist for this configuration. Never 0. */
  objective: number | null;
  objective_text: string | null;
  is_current: boolean;
  seed_spread: SeedSpread | null;
  unresolved_against: string[];
  /**
   * Does this row's own seed range exclude "no effect" entirely? False means
   * the placement has not been shown to do anything at all, whatever it
   * outranked. Null means the question was not answered — no spread for this
   * row, or an objective with no meaningful null — which is not the same
   * answer as false and must not render like one.
   */
  spread_clears_no_effect: boolean | null;
}

interface Ranking {
  variant_id: string;
  creative_id: string | null;
  focal_sku_id: string | null;
  /** Which estimator was asked for: the two lifts differ several-fold. */
  objective: string;
  objective_name: string;
  /** The sentence that says what the number is, from the library that made it. */
  objective_caveat: string;
  no_effect_value: number | null;
  n_synth: number;
  seed: number;
  spread_seeds: number[];
  elapsed_ms: number;
  n_candidates: number;
  current_rank: number | null;
  top_pick_is_resolved: boolean | null;
  /** Null means the comparison was never made. `[]` means it was, and nothing won. */
  beats_current: string[] | null;
  summary_lines: string[];
  spread_caveat: string;
  skipped: Array<{ candidate_id: string; kind: string; reason: string }>;
  entries: Entry[];
}

type Load =
  | { status: "idle" }
  | { status: "running" }
  | { status: "error"; detail: string }
  | { status: "ready"; value: Ranking };

export function OptimizeView({
  fetchImpl = defaultFetch,
  variantId = "A",
  creativeId = "AD_1",
}: OptimizeViewProps) {
  const [state, setState] = useState<Load>({ status: "running" });
  const [focalSku, setFocalSku] = useState<string>("");

  const run = useCallback(async () => {
    setState({ status: "running" });
    try {
      const response = await fetchImpl("/api/optimize", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          creative_id: creativeId,
          variant_id: variantId,
          focal_sku_id: focalSku === "" ? null : focalSku,
        }),
      });
      if (!response.ok) {
        const raw = await response.text().catch(() => "");
        let detail = `${response.status} ${response.statusText}`;
        try {
          const parsed = JSON.parse(raw) as { detail?: string };
          if (typeof parsed.detail === "string") detail = parsed.detail;
        } catch {
          // keep the status line
        }
        throw new AiRequestError(response.status, detail);
      }
      setState({ status: "ready", value: normalise((await response.json()) as Ranking) });
    } catch (error) {
      setState({
        status: "error",
        detail: error instanceof AiRequestError ? error.detail : String(error),
      });
    }
  }, [fetchImpl, creativeId, variantId, focalSku]);

  useEffect(() => {
    void run();
    // Deliberately only on mount and on an explicit re-run. Ranking the space
    // is many simulations; re-running it on every keystroke in the focal-SKU
    // box would be a very expensive autocomplete.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div data-testid="optimize-view" style={style.root}>
      <header style={{ marginBottom: 14 }}>
        <div style={{ display: "flex", gap: 14, alignItems: "baseline", flexWrap: "wrap" }}>
          <div style={{ fontSize: 26, fontWeight: 700, letterSpacing: "-0.01em" }}>
            Where should this go?
          </div>
          <a data-testid="optimize-home-link" style={style.linkButton} href="#/home">
            ← All screens
          </a>
        </div>
        <div style={{ ...style.note, marginTop: 4, maxWidth: 820 }}>
          Every placement of a creative scored against 10,000 synthetic shoppers per persona
          and ranked, with the placement running today marked so &ldquo;best&rdquo; has
          something to be better than. Exhaustive, not a search: the space is small enough to
          score whole, which is what lets this say &ldquo;ranks 3rd of 8&rdquo;.
        </div>
      </header>

      <div style={{ ...style.panel, display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
        <label style={{ ...style.note, display: "flex", gap: 8, alignItems: "center" }}>
          Also try a SKU at every shelf level
          <input
            data-testid="optimize-focal-sku"
            value={focalSku}
            placeholder="SKU_008 (optional)"
            onChange={(event) => setFocalSku(event.target.value.trim())}
            style={{ ...style.tab, padding: "6px 9px", fontFamily: style.mono, width: 190 }}
          />
        </label>
        <button
          type="button"
          data-testid="optimize-run"
          style={state.status === "running" ? style.disabledButton : style.primaryButton}
          disabled={state.status === "running"}
          onClick={() => void run()}
        >
          {state.status === "running" ? "Scoring every placement…" : "Rank the placements"}
        </button>
      </div>

      {state.status === "error" && (
        <div data-testid="optimize-error" style={style.alertBox}>
          {state.detail}
        </div>
      )}

      {state.status === "ready" && <Result ranking={state.value} />}
    </div>
  );
}

// ---------------------------------------------------------------------------

/**
 * The list fields, guaranteed to be arrays — except the one where an array is
 * itself a claim.
 *
 * The guarantee is the point: `Result` reads `entries[0]` and maps the rest,
 * and a 200 carrying anything else - an older API, a proxy answering for a dead
 * upstream - threw during render and left a blank page instead of a screen that
 * says what it does know. Normalising here rather than at each use keeps the one
 * guarantee in one place.
 *
 * `beats_current` is deliberately NOT coerced to an array. `[]` there means
 * "every placement was compared against the current one's range and none
 * cleared it", which is a finding; null means the comparison was never made.
 * Coercing null to `[]` here is precisely how the second turned into the first,
 * so anything that is not an array becomes null - the honest reading of a field
 * that is missing or the wrong shape.
 */
function normalise(ranking: Ranking): Ranking {
  return {
    ...ranking,
    entries: Array.isArray(ranking.entries) ? ranking.entries : [],
    summary_lines: Array.isArray(ranking.summary_lines) ? ranking.summary_lines : [],
    skipped: Array.isArray(ranking.skipped) ? ranking.skipped : [],
    beats_current: Array.isArray(ranking.beats_current) ? ranking.beats_current : null,
    spread_seeds: Array.isArray(ranking.spread_seeds) ? ranking.spread_seeds : [],
  };
}

function Result({ ranking }: { ranking: Ranking }) {
  const best = ranking.entries[0];

  // A ranking with no rows is not a ranking. Saying so beats an empty table
  // that reads as "we looked and there was nothing better".
  if (best === undefined) {
    return (
      <div data-testid="optimize-empty" style={{ ...style.panel, marginTop: 14 }}>
        The server returned no placements to rank. Nothing here is a result — check the API
        is the version this page expects.
      </div>
    );
  }

  return (
    <div style={{ display: "grid", gap: 14, marginTop: 14 }}>
      <div style={style.panel}>
        <div style={style.panelHeading}>The recommendation</div>
        {/* Verbatim from `optimizer.summary()`. The CLI, RESULTS.md and this
            screen have to say the same thing. */}
        <div data-testid="optimize-summary" style={{ fontSize: 17, lineHeight: 1.5 }}>
          {ranking.summary_lines.map((line, index) => (
            <p key={index} style={{ margin: index === 0 ? "0 0 8px" : "0 0 6px" }}>
              {line}
            </p>
          ))}
        </div>

        <div data-testid="optimize-objective" style={{ ...style.note, marginTop: 10 }}>
          Ranked on <strong>{ranking.objective_name}</strong>. The same space ranked on a
          different metric gives a different order, so this ranking only means anything
          together with that name.
        </div>

        <div data-testid="optimize-meta" style={{ ...style.note, marginTop: 4 }}>
          {ranking.n_candidates} placements · {ranking.n_synth} synthetic shoppers per
          persona · seed {ranking.seed} · variant {ranking.variant_id} ·{" "}
          {(ranking.elapsed_ms / 1000).toFixed(1)}s
        </div>
      </div>

      {/* What that metric actually is, printed verbatim from the library that
          produced the ranking. There are two Brand Lifts in analytics/lift.py
          and on this aisle they differ several-fold, so a percentage with no
          estimator beside it is an unlabelled percentage. Paraphrasing it here
          would be a third wording of one fact, which is how three wordings
          start to disagree — the same reason the summary is printed verbatim. */}
      {ranking.objective_caveat ? (
        <div data-testid="optimize-caveat" style={style.cautionBox}>
          <strong>What this number is:</strong> {ranking.objective_caveat}
        </div>
      ) : null}

      {best?.seed_spread != null && (
        <div data-testid="optimize-spread" style={style.cautionBox}>
          <strong>Seed spread on the top pick:</strong>{" "}
          {formatValue(best.seed_spread.low)} to {formatValue(best.seed_spread.high)} over{" "}
          {best.seed_spread.n_seeds} seeds ({best.seed_spread.seeds.join(", ")}).{" "}
          {ranking.spread_caveat}
        </div>
      )}

      <Resolution ranking={ranking} />

      <div style={style.panel}>
        <div style={style.panelHeading}>Every placement, scored</div>
        <div style={{ display: "grid", gap: 6 }}>
          {ranking.entries.map((entry) => (
            <Row key={entry.candidate_id} entry={entry} />
          ))}
        </div>
        {ranking.current_rank !== null && (
          <div data-testid="optimize-current" style={{ ...style.note, marginTop: 10 }}>
            The placement running today ranks <strong>{ranking.current_rank}</strong> of{" "}
            {ranking.n_candidates}. &ldquo;Best&rdquo; is only worth reading against that.
          </div>
        )}
        {/*
          One link, and the sentence that keeps it honest. Every row named its
          own move and every row's button went to the same place; this says
          where that place is and what it will and will not have done for you
          when you get there.
        */}
        <div data-testid="optimize-whatif" style={{ ...style.note, marginTop: 6 }}>
          To try one of these,{" "}
          <a
            data-testid="optimize-whatif-link"
            style={{ color: style.ACCENT, fontWeight: 600 }}
            href="#/whatif"
          >
            open the what-if panel
          </a>{" "}
          and set the move named in the row you want. That link{" "}
          <strong>does not carry the placement</strong>: the panel opens on the
          unpatched baseline and takes its move from its own dropdowns, because nothing
          on that screen reads a placement out of the address bar.
        </div>
      </div>

      {ranking.skipped.length > 0 && (
        <div style={style.panel}>
          <div style={style.panelHeading}>Not tried, and why</div>
          {/* Listed rather than dropped: a level missing from the ranking reads
              as "we tried it and it was bad", which is a different and much
              stronger claim than "there was nowhere to put it". */}
          <div data-testid="optimize-skipped" style={{ display: "grid", gap: 5 }}>
            {ranking.skipped.map((skip) => (
              <div key={skip.candidate_id} style={{ ...style.note, fontSize: 13 }}>
                <code style={style.monoStyle}>{skip.candidate_id}</code> — {skip.reason}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * Whether the ranking actually separates its leaders — the recommendation's own
 * weakness, stated rather than left for a reader to infer from a tidy table.
 */
function Resolution({ ranking }: { ranking: Ranking }) {
  const best = ranking.entries[0];
  const unresolved = best?.unresolved_against ?? [];
  const beats = ranking.beats_current;

  if (ranking.top_pick_is_resolved === true) {
    return (
      <div data-testid="optimize-resolution" style={okBox}>
        The top pick&apos;s seed spread clears every other placement&apos;s, so the order at
        the top is settled at this run size.
        {beats !== null && beats.length > 0 && (
          <>
            {" "}
            {beats.length} placement
            {beats.length === 1 ? "" : "s"} also clear the current placement&apos;s spread
            entirely: {beats.join(", ")}.
          </>
        )}
        <NotComparedToCurrent beats={beats} />
      </div>
    );
  }

  if (ranking.top_pick_is_resolved === false) {
    return (
      <div data-testid="optimize-resolution" style={style.cautionBox}>
        <strong>This order is not settled.</strong> The top pick&apos;s seed spread overlaps{" "}
        {unresolved.join(", ")}, so it is not actually ranked against{" "}
        {unresolved.length === 1 ? "that placement" : "those placements"} at this run size.
        More seeds will not fix it — the spread is a min-max range and can only widen. Only a
        larger run size narrows it.
        {beats !== null && beats.length === 0 && (
          <> No placement clears the current one&apos;s spread either.</>
        )}
        <NotComparedToCurrent beats={beats} />
      </div>
    );
  }

  return (
    <div data-testid="optimize-resolution" style={{ ...style.panel, ...style.note }}>
      No seed spread was computed, so whether this order is settled is unknown rather than
      true.
      <NotComparedToCurrent beats={beats} />
    </div>
  );
}

/**
 * The branch that did not exist, and the reason this component was lying.
 *
 * `beats_current` has three states and the screen used to render two. Null is
 * "the comparison was never made" — either nothing in the space reproduces
 * today's planogram, or the current placement fell outside the rows re-scored
 * at extra seeds and so has no range for anything to clear. The server sent
 * `[]` for that as well as for the real negative, and `[]` printed as "No
 * placement clears the current one's spread either", which is a finding. On the
 * committed aisle the current placement ranks 5th to 8th and the default
 * re-scores the top five, so that sentence was being printed with nothing
 * behind it for 23 of the 24 focal SKUs.
 */
function NotComparedToCurrent({ beats }: { beats: string[] | null }) {
  if (beats !== null) return null;
  return (
    <>
      {" "}
      <strong>The current placement was not compared.</strong> It has no seed spread of its
      own here — either nothing in this space reproduces the planogram running today, or it
      ranked below the rows that were re-scored at the extra seeds — so &ldquo;does moving
      beat where it is now&rdquo; was not asked. That is not the same as asking and getting
      no.
    </>
  );
}

function Row({ entry }: { entry: Entry }) {
  return (
    <div
      data-testid={`optimize-row-${entry.rank}`}
      data-current={String(entry.is_current)}
      style={{
        display: "flex",
        gap: 12,
        alignItems: "center",
        padding: "8px 10px",
        borderRadius: 7,
        border: `1px solid ${entry.is_current ? style.CHANGED : style.PANEL_BORDER}`,
        background: entry.rank === 1 ? "#22304a" : "#171c24",
      }}
    >
      <div style={{ flex: "0 0 28px", fontFamily: style.mono, opacity: 0.65 }}>
        {entry.rank}.
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: entry.rank === 1 ? 700 : 500 }}>{entry.label}</div>
        {entry.is_current && (
          <div style={{ ...style.note, color: style.CHANGED, fontSize: 12 }}>
            this is what is running today
          </div>
        )}
        {/* The row's own Monte Carlo range, on the row. Only the winner's used
            to be shown, which left every other percentage looking exact. */}
        <div style={{ ...style.note, fontSize: 12 }}>
          {entry.seed_spread === null ? (
            <span>no spread computed for this row</span>
          ) : (
            <span>
              seeds {formatValue(entry.seed_spread.low)} to{" "}
              {formatValue(entry.seed_spread.high)} over {entry.seed_spread.n_seeds}
            </span>
          )}
          {/* Sorting descending is not evidence: a row can outrank six others
              and still have a range that contains no effect at all. Only false
              gets this badge — null means the question was not answered. */}
          {entry.spread_clears_no_effect === false && (
            <strong style={{ color: style.CHANGED }}>
              {" "}
              · UNRESOLVED: this range contains no effect at all
            </strong>
          )}
        </div>
      </div>
      <div
        style={{
          flex: "0 0 92px",
          textAlign: "right",
          fontFamily: style.mono,
          fontSize: 15,
          // Undefined is not a bad score, and must not be coloured like one.
          color: entry.objective === null ? style.GREY : style.INK,
        }}
      >
        {/* Never 0%. The metric does not exist for this configuration. */}
        {entry.objective_text ?? "undefined"}
      </div>
      {/*
        No "Try it" button. There was one, on every row, and it was the same
        constant `#/whatif` on all of them — so pressing it on rank 1 and on
        rank 4 opened the same unpatched panel, and the recommendation it was
        offering to try was not carried anywhere. It cannot be carried from
        here either: `main.tsx` renders `<WhatIfPanel />` with no props and
        nothing under `web/src/whatif/` reads the URL, so a candidate in the
        hash would be read by nobody. The honest route to that screen is stated
        once below the table instead, where it can say what it actually does.
      */}
    </div>
  );
}

function formatValue(value: number): string {
  const percent = value * 100;
  return `${percent >= 0 ? "+" : ""}${percent.toFixed(1)}%`;
}

const okBox: CSSProperties = {
  marginTop: 0,
  padding: "10px 12px",
  borderRadius: 8,
  border: `1px solid ${style.OK}`,
  background: "#16261c",
  color: style.INK,
  fontSize: 13,
  lineHeight: 1.5,
};
