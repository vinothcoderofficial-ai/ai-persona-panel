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
 *    missing row reads as a move that was tried and lost.
 */

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

const RESPONSE = {
  variant_id: "A",
  planogram_id: "demo_aisle",
  creative_id: "AD_1",
  focal_sku_id: null as string | null,
  objective_name: "ad-to-purchase lift for creative AD_1",
  n_synth: 10000,
  seed: 42,
  spread_seeds: [42, 43, 44, 45, 46],
  elapsed_ms: 7031,
  n_candidates: 4,
  current_rank: 3,
  top_pick_is_resolved: false,
  beats_current: [] as string[],
  summary_lines: [
    "Best of 4 placements on ad-to-purchase lift for creative AD_1 (10000 shoppers, seed 42): AD_1 on B1_TALKER at +12.7%.",
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
    },
    {
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
      seed_spread: null,
      unresolved_against: [],
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
      expect(text(harness, "optimize-objective")).toContain("ad-to-purchase lift");
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
  it("offers each placement's patches to the what-if screen", async () => {
    // A recommendation nobody can try is a slogan. The link carries the
    // candidate so the what-if panel opens on the move being recommended.
    const harness = await mount();
    try {
      const link = harness.container.querySelector<HTMLAnchorElement>(
        '[data-testid="optimize-try-1"]',
      );
      expect(link?.getAttribute("href")).toContain("#/whatif");
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
