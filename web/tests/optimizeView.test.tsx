import { describe, expect, it } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { FetchLike } from "@/ai/client";
import { OptimizeView } from "@/optimize/OptimizeView";

/**
 * `#/optimize` — the recommendation, on a screen.
 *
 * `analytics/optimizer.py` has ranked every placement since S24 and had no
 * route and no UI, so the strongest claim this project makes — that it is a
 * recommendation engine rather than an A/B tool, because it models purchase
 * and not only attention — was invisible from the running product.
 *
 * What this suite holds is the honesty of the presentation, because a ranking
 * is very easy to over-sell:
 *
 *  * a placement with **no** objective reads as undefined, never as 0%;
 *  * the range under the winner is called a **seed spread**, with its seeds,
 *    and never a confidence interval;
 *  * when the top pick is **not separated** from the rest, the screen says so
 *    instead of printing a clean winner;
 *  * the placement running **today** is marked, and its rank stated — "best" is
 *    meaningless without "compared to what you are doing now";
 *  * candidates the planogram could not express are **listed**, because a
 *    missing row reads as a move that was tried and lost;
 *  * **a question that was not asked is not answered "no".** `beats_current`
 *    is null when the comparison could never be made — nothing in the space
 *    reproduces today's planogram, or the current placement has no seed range
 *    for anything to clear. This screen rendered the empty list as "No
 *    placement clears the current one's spread either", and the server sent
 *    `[]` for both cases, so on the committed aisle an unanswered question was
 *    printed as a definite negative for 23 of the 24 focal SKUs;
 *  * **the number says which estimator produced it.** There are two Brand
 *    Lifts, they differ several-fold on this aisle, and a percentage with no
 *    estimator beside it is an unlabelled percentage — so the server's own
 *    caveat is printed verbatim rather than paraphrased here;
 *  * **a row whose spread contains no effect says UNRESOLVED**, whatever it
 *    outranked. Sorting descending is not evidence that the winner does
 *    anything.
 */

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

const RESPONSE = {
  variant_id: "A",
  planogram_id: "demo_aisle",
  creative_id: "AD_1",
  focal_sku_id: null as string | null,
  objective: "between_arm_lift",
  objective_name: "between-arm brand lift for creative AD_1",
  objective_caveat:
    "Between-arm comparison: each placement's whole synthetic population against a control run of the same shelf with AD_1 taken down.",
  no_effect_value: 0 as number | null,
  n_synth: 10000,
  seed: 42,
  spread_seeds: [42, 43, 44, 45, 46],
  elapsed_ms: 7031,
  n_candidates: 4,
  current_rank: 3,
  top_pick_is_resolved: false,
  beats_current: [] as string[] | null,
  summary_lines: [
    "Best of 4 placements on between-arm brand lift for creative AD_1 (10000 shoppers, seed 42): AD_1 on B1_TALKER at +12.7%.",
  ],
  spread_caveat:
    "This range is Monte Carlo run-to-run variability across seeds — the same simulation re-rolled — and is not a confidence interval.",
  skipped: [
    {
      candidate_id: "sku:SKU_008@top",
      kind: "sku_shelf_level",
      reason: "bay B1 has no shelf at level 'top' with a free slot",
      detail: {},
    },
  ],
  entries: [
    {
      rank: 1,
      candidate_id: "ad:AD_1@B1_TALKER",
      kind: "ad_placement",
      label: "AD_1 on B1_TALKER (shelf_talker, bay B1)",
      detail: {},
      patches: [{ op: "set_ad_creative", ad_slot_id: "B1_TALKER", creative_id: "AD_1" }],
      objective: 0.127,
      objective_text: "+12.7%",
      is_current: false,
      variant_id: "wi_1",
      sim_run_id: "sim_1",
      seed_spread: {
        seeds: [42, 43, 44, 45, 46],
        values: [0.127, 0.062, 0.142, 0.101, 0.09],
        low: 0.062,
        high: 0.142,
        n_seeds: 5,
        width: 0.08,
      },
      unresolved_against: ["ad:AD_1@B2_DECAL"],
      spread_clears_no_effect: true,
    },
    {
      // Ranked second and not shown to do anything: its range crosses zero.
      // This is the ordinary case for the honest estimator at 10,000 shoppers,
      // not a contrived one.
      rank: 2,
      candidate_id: "ad:AD_1@B2_DECAL",
      kind: "ad_placement",
      label: "AD_1 on B2_DECAL (floor_decal, bay B2)",
      detail: {},
      patches: [{ op: "set_ad_creative", ad_slot_id: "B2_DECAL", creative_id: "AD_1" }],
      objective: 0.101,
      objective_text: "+10.1%",
      is_current: false,
      variant_id: "wi_2",
      sim_run_id: "sim_2",
      seed_spread: {
        seeds: [42, 43, 44, 45, 46],
        values: [0.101, -0.004, 0.05, 0.02, 0.031],
        low: -0.004,
        high: 0.101,
        n_seeds: 5,
        width: 0.105,
      },
      unresolved_against: ["ad:AD_1@B1_TALKER"],
      spread_clears_no_effect: false,
    },
    {
      rank: 3,
      candidate_id: "ad:AD_1@B3_ENDCAP",
      kind: "ad_placement",
      label: "AD_1 on B3_ENDCAP (endcap_header, bay B3)",
      detail: {},
      patches: [{ op: "set_ad_creative", ad_slot_id: "B3_ENDCAP", creative_id: "AD_1" }],
      objective: 0.045,
      objective_text: "+4.5%",
      is_current: true,
      variant_id: "wi_3",
      sim_run_id: "sim_3",
      seed_spread: null,
      unresolved_against: [],
      // Outside spread_top_n: no range at all. "Not measured" is a third
      // answer, not the same as "measured and overlapping zero".
      spread_clears_no_effect: null as boolean | null,
    },
    {
      rank: 4,
      candidate_id: "ad:AD_1@none",
      kind: "ad_placement",
      label: "AD_1 unplaced (no ad slot carries it)",
      detail: {},
      patches: [{ op: "set_ad_creative", ad_slot_id: "B3_ENDCAP", creative_id: null }],
      objective: null,
      objective_text: null,
      is_current: false,
      variant_id: "wi_4",
      sim_run_id: "sim_4",
      seed_spread: null,
      unresolved_against: [],
      spread_clears_no_effect: null as boolean | null,
    },
  ],
};

interface Harness {
  container: HTMLDivElement;
  bodies: unknown[];
  settle: () => Promise<void>;
  unmount: () => void;
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

async function mount(
  overrides: Partial<typeof RESPONSE> = {},
  opts: { status?: number; detail?: string } = {},
): Promise<Harness> {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const bodies: unknown[] = [];

  const fetchImpl: FetchLike = async (_input, init) => {
    if (init?.body !== undefined) bodies.push(JSON.parse(String(init.body)));
    if (opts.status !== undefined && opts.status !== 200) {
      return json(opts.status, { detail: opts.detail ?? "no" });
    }
    return json(200, { ...RESPONSE, ...overrides });
  };

  let root: Root | null = null;
  await act(async () => {
    root = createRoot(container);
    root.render(<OptimizeView fetchImpl={fetchImpl} />);
  });

  const settle = async () => {
    await act(async () => {
      for (let n = 0; n < 8; n += 1) await Promise.resolve();
    });
  };
  await settle();

  return {
    container,
    bodies,
    settle,
    unmount: () => {
      act(() => root?.unmount());
      container.remove();
    },
  };
}

function text(harness: Harness, testId: string): string {
  return harness.container.querySelector(`[data-testid="${testId}"]`)?.textContent ?? "";
}

function node(harness: Harness, testId: string): HTMLElement {
  const found = harness.container.querySelector<HTMLElement>(`[data-testid="${testId}"]`);
  if (found === null) throw new Error(`no [data-testid="${testId}"]`);
  return found;
}

describe("the ranking", () => {
  it("runs on mount and shows every placement, best first", async () => {
    const harness = await mount();
    try {
      const rows = harness.container.querySelectorAll('[data-testid^="optimize-row-"]');
      expect(rows).toHaveLength(4);
      expect(text(harness, "optimize-row-1")).toContain("B1_TALKER");
    } finally {
      harness.unmount();
    }
  });

  it("names the metric it ranked on", async () => {
    const harness = await mount();
    try {
      expect(text(harness, "optimize-objective")).toContain("between-arm brand lift");
    } finally {
      harness.unmount();
    }
  });

  it("prints the library's own summary sentence", async () => {
    // Not a third rendering of it: the CLI, RESULTS.md and this screen must all
    // say the same thing, and the only way to guarantee that is to print what
    // `optimizer.summary()` returned.
    const harness = await mount();
    try {
      expect(text(harness, "optimize-summary")).toContain("Best of 4 placements");
    } finally {
      harness.unmount();
    }
  });

  it("says how long it took and at what run size", async () => {
    const harness = await mount();
    try {
      const meta = text(harness, "optimize-meta");
      expect(meta).toContain("10000");
      expect(meta).toContain("42");
    } finally {
      harness.unmount();
    }
  });
});

describe("what the ranking will not claim", () => {
  it("shows an undefined objective as undefined, never as 0%", async () => {
    const harness = await mount();
    try {
      const row = text(harness, "optimize-row-4");
      expect(row.toLowerCase()).toContain("undefined");
      expect(row).not.toContain("0.0%");
      expect(row).not.toContain("+0%");
    } finally {
      harness.unmount();
    }
  });

  it("calls the range a seed spread and never a confidence interval", async () => {
    const harness = await mount();
    try {
      const spread = text(harness, "optimize-spread");
      expect(spread.toLowerCase()).toContain("seed");
      expect(spread.toLowerCase()).toContain("not a confidence interval");
    } finally {
      harness.unmount();
    }
  });

  it("names the seeds the spread was taken over", async () => {
    // A range over two seeds and a range over twenty are different statistics
    // of the same variability. Quoting one without its count says nothing.
    const harness = await mount();
    try {
      expect(text(harness, "optimize-spread")).toContain("5");
    } finally {
      harness.unmount();
    }
  });

  it("says plainly when the top pick is not separated from the rest", async () => {
    const harness = await mount();
    try {
      expect(text(harness, "optimize-resolution").toLowerCase()).toContain("not");
      expect(text(harness, "optimize-resolution")).toContain("B2_DECAL");
    } finally {
      harness.unmount();
    }
  });

  it("says so when the top pick IS separated", async () => {
    const harness = await mount({
      top_pick_is_resolved: true,
      beats_current: ["ad:AD_1@B1_TALKER"],
    });
    try {
      const resolution = text(harness, "optimize-resolution").toLowerCase();
      expect(resolution).toContain("clear");
    } finally {
      harness.unmount();
    }
  });

  it("lists the candidates the planogram could not express", async () => {
    const harness = await mount();
    try {
      const skipped = text(harness, "optimize-skipped");
      expect(skipped).toContain("no shelf at level");
    } finally {
      harness.unmount();
    }
  });
});

describe("a response that is not a ranking", () => {
  it("renders and says so rather than throwing", async () => {
    // A proxy answering 200 with something else, or an older API. Found by
    // `web/tests/hashRouting.test.tsx`, whose fetch stub returns `{}` for
    // everything: the screen threw on `entries[0]` and left a blank page.
    const harness = await mount({
      entries: undefined as never,
      summary_lines: undefined as never,
      skipped: undefined as never,
    });
    try {
      expect(harness.container.querySelector('[data-testid="optimize-view"]')).not.toBeNull();
      expect(text(harness, "optimize-empty")).toBeTruthy();
    } finally {
      harness.unmount();
    }
  });
});

describe("the placement running today", () => {
  it("marks the current row", async () => {
    const harness = await mount();
    try {
      expect(node(harness, "optimize-row-3").dataset.current).toBe("true");
      expect(node(harness, "optimize-row-1").dataset.current).toBe("false");
    } finally {
      harness.unmount();
    }
  });

  it("states where it ranks, because 'best' means nothing without it", async () => {
    const harness = await mount();
    try {
      const current = text(harness, "optimize-current");
      expect(current).toContain("3");
      expect(current).toContain("4");
    } finally {
      harness.unmount();
    }
  });
});

describe("acting on a recommendation", () => {
  /*
   * There used to be a "Try it" button on every row, and it was the same
   * constant `#/whatif` on all of them. The test that stood here was named for
   * a handoff — "offers each placement's patches to the what-if screen" — and
   * asserted only that the href contained `#/whatif`, which is exactly what a
   * button that carries nothing also satisfies. So the name described a
   * feature, the assertion described a constant, and the screen offered a
   * control that opened the what-if panel on the baseline whichever row you
   * pressed: press "Try it" on rank 1 and on rank 4 and you get the same empty
   * dropdowns.
   *
   * It cannot be made to work from this file. `main.tsx` routes on
   * `hash.startsWith("#/whatif")` and renders `<WhatIfPanel />` with no props;
   * nothing under `web/src/whatif/` reads `location`, a query string or a hash,
   * and the panel's selection starts at `EMPTY_SELECTION`. A placement in the
   * URL would therefore be read by nobody. Carrying `Entry.patches` across
   * would mean teaching the what-if panel to accept a selection from the
   * address bar, which is a change to that screen and not to this one.
   *
   * So the button is gone and `Entry.patches` — fetched, typed, and read by
   * nothing — went with it. What replaces it is one link that says what it
   * actually does. A control that lies about where it takes you is worse than
   * no control: the row already names the move in full, and a person who
   * follows a link expecting it to be set up and finds an empty panel learns
   * to distrust the whole screen.
   */

  it("offers no per-row link, because none of them could carry that row's move", async () => {
    const harness = await mount();
    try {
      const perRow = harness.container.querySelectorAll('[data-testid^="optimize-try-"]');
      expect(perRow).toHaveLength(0);
    } finally {
      harness.unmount();
    }
  });

  it("points at the what-if panel and says it opens on the baseline, not on the move", async () => {
    // A recommendation nobody can try is a slogan, so the route is still
    // offered — with the one sentence that stops it being a lie: the panel
    // opens unpatched and the move is chosen in its own dropdowns, from the
    // row above.
    const harness = await mount();
    try {
      expect(node(harness, "optimize-whatif-link").getAttribute("href")).toBe("#/whatif");
      const said = text(harness, "optimize-whatif").toLowerCase();
      expect(said).toContain("baseline");
      expect(said).toContain("does not carry");
    } finally {
      harness.unmount();
    }
  });
});

describe("choosing what to optimise", () => {
  it("asks the server for the creative and variant on screen", async () => {
    const harness = await mount();
    try {
      expect(harness.bodies[0]).toMatchObject({ creative_id: "AD_1", variant_id: "A" });
    } finally {
      harness.unmount();
    }
  });

  it("re-runs when asked, and says it is working", async () => {
    const harness = await mount();
    try {
      act(() => node(harness, "optimize-run").dispatchEvent(new MouseEvent("click", { bubbles: true })));
      await harness.settle();
      expect(harness.bodies).toHaveLength(2);
    } finally {
      harness.unmount();
    }
  });
});

describe("failure", () => {
  it("shows the server's own sentence", async () => {
    const harness = await mount({}, { status: 404, detail: "no creative 'AD_9'" });
    try {
      expect(text(harness, "optimize-error")).toContain("AD_9");
    } finally {
      harness.unmount();
    }
  });
});

describe("the screen is a way back, not a dead end", () => {
  it("links to the operator launcher", async () => {
    const harness = await mount();
    try {
      expect(node(harness, "optimize-home-link").getAttribute("href")).toBe("#/home");
    } finally {
      harness.unmount();
    }
  });
});


describe("which estimator produced the number", () => {
  it("prints the server's own caveat rather than a paraphrase of it", async () => {
    // There are two Brand Lifts in analytics/lift.py and on this aisle they
    // differ several-fold. Which one a column came from is not decoration, and
    // the words come from the library that produced the ranking so the CLI,
    // RESULTS.md and this screen cannot drift into saying different things.
    const harness = await mount();
    try {
      expect(text(harness, "optimize-caveat")).toContain(RESPONSE.objective_caveat);
    } finally {
      harness.unmount();
    }
  });

  it("carries the within-run warning through unchanged when that is what ran", async () => {
    const harness = await mount({
      objective: "within_run_lift",
      objective_name: "within-run ad-to-purchase lift for creative AD_1",
      objective_caveat:
        "Within-run split: ... That division is a SELECTION and not a randomisation ...",
    });
    try {
      expect(text(harness, "optimize-caveat").toUpperCase()).toContain("SELECTION");
      expect(text(harness, "optimize-objective")).toContain("within-run");
    } finally {
      harness.unmount();
    }
  });
});

describe("a comparison that was never made", () => {
  it("says the current placement was not compared, rather than that nothing beat it", async () => {
    // `beats_current` null means the question was not answered: no candidate
    // reproduces today's planogram, or the current one has no seed range for
    // anything to clear. Rendering that as "No placement clears the current
    // one's spread either" turns an unasked question into a finding.
    const harness = await mount({ beats_current: null });
    try {
      const resolution = text(harness, "optimize-resolution");
      expect(resolution.toLowerCase()).toContain("not compared");
      expect(resolution).not.toContain("No placement clears the current one");
    } finally {
      harness.unmount();
    }
  });

  it("still reports the real negative when the comparison WAS made", async () => {
    // The empty list is a finding — every placement was compared against the
    // current one's range and none cleared it — and it has to survive the fix
    // for the null case rather than being softened along with it.
    const harness = await mount({ beats_current: [] });
    try {
      expect(text(harness, "optimize-resolution")).toContain(
        "No placement clears the current one",
      );
    } finally {
      harness.unmount();
    }
  });

  it("claims nothing about clearing the current placement when the top pick IS separated but nothing was compared", async () => {
    const harness = await mount({ top_pick_is_resolved: true, beats_current: null });
    try {
      const resolution = text(harness, "optimize-resolution");
      expect(resolution.toLowerCase()).toContain("not compared");
      expect(resolution).not.toContain("also clear the current");
    } finally {
      harness.unmount();
    }
  });

  it("does not crash when an older server omits the field entirely", async () => {
    // normalise() coerced anything non-array to [], which is how the honest
    // null became a definite negative in the first place. Anything that is not
    // an array now reads as "not compared", which is what a missing field is.
    const harness = await mount({ beats_current: undefined as never });
    try {
      expect(text(harness, "optimize-resolution").toLowerCase()).toContain("not compared");
    } finally {
      harness.unmount();
    }
  });
});

describe("what each row admits about itself", () => {
  it("shows every row's own seed spread, not only the winner's", async () => {
    const harness = await mount();
    try {
      expect(text(harness, "optimize-row-1")).toContain("+6.2%");
      expect(text(harness, "optimize-row-1")).toContain("+14.2%");
      expect(text(harness, "optimize-row-2")).toContain("-0.4%");
    } finally {
      harness.unmount();
    }
  });

  it("marks a row whose spread contains no effect as UNRESOLVED", async () => {
    // Rank 2 sorted above six other placements and has still not been shown to
    // do anything: its range runs from -0.4% to +10.1%. Sorting descending is
    // not evidence.
    const harness = await mount();
    try {
      expect(text(harness, "optimize-row-2")).toContain("UNRESOLVED");
      expect(text(harness, "optimize-row-1")).not.toContain("UNRESOLVED");
    } finally {
      harness.unmount();
    }
  });

  it("does not call a row unresolved when it simply has no spread", async () => {
    // Rank 3 is outside spread_top_n. "Not measured" and "measured and
    // straddling zero" are different, and only the second is a verdict.
    const harness = await mount();
    try {
      expect(text(harness, "optimize-row-3")).not.toContain("UNRESOLVED");
      expect(text(harness, "optimize-row-3").toLowerCase()).toContain("no spread");
    } finally {
      harness.unmount();
    }
  });
});
