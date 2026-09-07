import { describe, expect, it } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { FetchLike } from "@/ai/client";
import { CollectionStatus } from "@/launcher/CollectionStatus";

/**
 * The real panel, on the launcher.
 *
 * `RESULTS.md` has said "not yet collected" for every real-panel number since
 * the project started, and finding out why meant opening `shoppertwin.db` in a
 * SQL client. An operator running people back to back could not see the panel
 * filling up, could not see what people were being rejected for, and could not
 * tell whether what they had collected had reached the committed corpus.
 *
 * The distinction this box exists to make, and the one every test here is
 * really about: **collected is not committed.** `scripts/eval.py` reads
 * `data/sessions/anon/` and nothing else, so a session sitting in SQLite is not
 * evidence yet, however green it looks.
 */

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

const STATUS = {
  live: {
    total: 7,
    accepted: 3,
    rejected: 2,
    undecided: 1,
    no_consent: 1,
    reject_reasons: { too_short: 2 } as Record<string, number>,
  },
  committed: { sessions: 0, directory: "data/sessions/anon" },
  locks: 7,
  export_needed: true,
  export_command: "python scripts/anonymise_sessions.py",
  eval_command: "python scripts/eval.py",
  gate: {
    min_observed_slots: 6,
    min_stations: 2,
    min_interactions: 1,
    min_fixation_coverage: 0.4,
  },
};

interface Harness {
  container: HTMLDivElement;
  unmount: () => void;
}

async function mount(
  overrides: Partial<typeof STATUS> = {},
  opts: { fail?: boolean } = {},
): Promise<Harness> {
  const container = document.createElement("div");
  document.body.appendChild(container);

  const fetchImpl: FetchLike = async () => {
    if (opts.fail === true) throw new Error("connection refused");
    return new Response(JSON.stringify({ ...STATUS, ...overrides }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };

  let root: Root | null = null;
  await act(async () => {
    root = createRoot(container);
    root.render(<CollectionStatus fetchImpl={fetchImpl} />);
  });
  await act(async () => {
    for (let n = 0; n < 6; n += 1) await Promise.resolve();
  });

  return {
    container,
    unmount: () => {
      act(() => root?.unmount());
      container.remove();
    },
  };
}

function text(harness: Harness, testId: string): string {
  return harness.container.querySelector(`[data-testid="${testId}"]`)?.textContent ?? "";
}

describe("what the panel holds", () => {
  it("counts accepted, rejected and unfinished separately", async () => {
    const harness = await mount();
    try {
      const body = text(harness, "collection-status");
      expect(body).toContain("3");
      expect(body).toContain("2");
      // Unfinished is not rejected: nothing has judged those sessions yet, and
      // counting them as rejections would inflate the reject histogram with
      // people who simply closed the tab.
      expect(body.toLowerCase()).toContain("unfinished");
    } finally {
      harness.unmount();
    }
  });

  it("shows what people were rejected for", async () => {
    // The single most useful thing here. The first real session was rejected
    // as `too_short` and nobody found out until the run was over.
    const harness = await mount();
    try {
      expect(text(harness, "collection-rejects")).toContain("too_short");
      expect(text(harness, "collection-rejects")).toContain("2");
    } finally {
      harness.unmount();
    }
  });

  it("says what acceptance requires, so a rejection is diagnosable here", async () => {
    const harness = await mount();
    try {
      const gate = text(harness, "collection-gate");
      expect(gate).toContain("6");
      expect(gate).toContain("2");
      // Shelf seen, not time spent. An operator reading "45s or longer" here
      // would go on coaching participants to linger, which is the behaviour
      // the old floor selected for and the reason the mission archetype never
      // reached the panel.
      expect(gate).not.toContain("45");
      expect(gate.toLowerCase()).toContain("slot");
    } finally {
      harness.unmount();
    }
  });
});

describe("collected is not committed", () => {
  it("says so, and names the commands, when an export is outstanding", async () => {
    const harness = await mount();
    try {
      const action = text(harness, "collection-action");
      expect(action).toContain("anonymise_sessions");
      expect(action).toContain("eval");
    } finally {
      harness.unmount();
    }
  });

  it("shows the committed count beside the live one, never merged", async () => {
    const harness = await mount();
    try {
      const body = text(harness, "collection-status");
      expect(body.toLowerCase()).toContain("committed");
    } finally {
      harness.unmount();
    }
  });

  it("stops asking for an export once the corpus has caught up", async () => {
    const harness = await mount({
      committed: { sessions: 3, directory: "data/sessions/anon" },
      export_needed: false,
    });
    try {
      expect(harness.container.querySelector('[data-testid="collection-action"]')).toBeNull();
    } finally {
      harness.unmount();
    }
  });
});

describe("an empty panel", () => {
  it("says nobody has shopped yet rather than showing a wall of zeros", async () => {
    const harness = await mount({
      live: {
        total: 0,
        accepted: 0,
        rejected: 0,
        undecided: 0,
        no_consent: 0,
        reject_reasons: {},
      },
      locks: 0,
      export_needed: false,
    });
    try {
      expect(text(harness, "collection-status").toLowerCase()).toContain("nobody");
    } finally {
      harness.unmount();
    }
  });
});

describe("when the API is not running", () => {
  it("says the API could not be reached rather than showing zeros", async () => {
    // Zeros here would read as "nobody has shopped", which is a claim about the
    // panel. "The API is not running" is a claim about this browser.
    const harness = await mount({}, { fail: true });
    try {
      const body = text(harness, "collection-status").toLowerCase();
      expect(body).toContain("could not");
      expect(body).not.toContain("nobody has shopped");
    } finally {
      harness.unmount();
    }
  });
});

describe("a 200 that is not a status document", () => {
  it("is treated as unreachable rather than crashing the launcher", async () => {
    // The third component in this codebase to blank a page on an unexpected
    // 200 body. Here it took the whole launcher down - every link on the page -
    // because this box renders above them.
    const container = document.createElement("div");
    document.body.appendChild(container);
    const fetchImpl: FetchLike = async () =>
      new Response(JSON.stringify({}), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });

    let root: Root | null = null;
    await act(async () => {
      root = createRoot(container);
      root.render(<CollectionStatus fetchImpl={fetchImpl} />);
    });
    await act(async () => {
      for (let n = 0; n < 6; n += 1) await Promise.resolve();
    });

    try {
      const body =
        container.querySelector('[data-testid="collection-status"]')?.textContent ?? "";
      expect(body.toLowerCase()).toContain("could not");
    } finally {
      act(() => root?.unmount());
      container.remove();
    }
  });
});
