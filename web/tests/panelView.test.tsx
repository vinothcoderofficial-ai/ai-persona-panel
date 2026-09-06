import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { resolve as resolvePath } from "node:path";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { FetchLike } from "@/ai/client";
import { PanelView } from "@/panel/PanelView";

/**
 * `#/panel` — the synthetic half of the demo, which did not exist.
 *
 * The store let a person shop and the dashboard scored them afterwards, but the
 * AI panel's counterpart — a synthetic shopper actually shopping — was nowhere:
 * the traces sat in `data/cache/traces/` as JSON and the only synthetic output
 * on any screen was an aggregate number. This screen replays one persona's trip
 * over the same shelf the human sees, turn by turn, with the model's own reason
 * for each move.
 *
 * What this suite holds:
 *
 *  * **shelves are named as products.** The whole point of building it on
 *    `aisleMap` — a replay that highlighted `B1S3P2` would be as unreadable as
 *    everything it was built to replace.
 *  * **the cart is what the trace says it is.** A player that fills the cart on
 *    `pickup`, or carries an add across a shopper change, is showing a purchase
 *    that never happened on the screen meant to make the synthetic panel
 *    believable.
 *  * **a missing trace says how to make one**, rather than rendering an empty
 *    aisle that looks like a shopper who did nothing.
 */

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

const PLANOGRAM = JSON.parse(
  readFileSync(resolvePath(__dirname, "../../data/planograms/demo_aisle.json"), "utf-8"),
) as unknown;

const STATUS = {
  provider: "ollama",
  model: "m",
  offline: false,
  api_key_set: true,
  can_call: true,
  reason: null,
  planogram_id: "demo_aisle",
  prompt_template: "sim/prompts/persona_policy.md",
  personas: [
    {
      persona_id: "mission",
      archetype: "mission",
      share_of_population: 0.35,
      description: "Comes with a list.",
      policy_cached: true,
      trace_cached: true,
      trace_model: "m",
      trace_n_shoppers: 1,
      trace_n_turns: 5,
      trace_temperature: 0.7,
    },
    {
      persona_id: "browser",
      archetype: "browser",
      share_of_population: 0.25,
      description: "No list.",
      policy_cached: true,
      trace_cached: true,
      trace_model: "m",
      trace_n_shoppers: 1,
      trace_n_turns: 2,
      trace_temperature: 0.7,
    },
  ],
};

function turn(
  n: number,
  action: string,
  target: string | null,
  reason: string,
  station = "B1",
) {
  return { turn: n, station_id: station, action, target, reason, time_left_s: 40 - n * 4 };
}

const MISSION_TRACE = {
  trace_version: 1,
  persona_id: "mission",
  archetype: "mission",
  description: "Comes with a list.",
  planogram_id: "demo_aisle",
  n_shoppers: 1,
  seed: 42,
  temperature: 0.7,
  model: "deepseek-v4-pro:cloud",
  n_turns: 5,
  n_rejections: 0,
  end_reasons: { checkout: 1 },
  carts: { SKU_001: 1 },
  shoppers: [
    {
      shopper_index: 0,
      n_turns: 5,
      n_rejections: 0,
      end_reason: "checkout",
      end_note: null,
      stations_visited: ["B1", "B2"],
      rejections: [],
      cart: ["SKU_001"],
      cart_detail: [],
      turns: [
        turn(1, "look", "B1S1P1", "A promotion caught my eye."),
        turn(2, "pickup", "B1S1P1", "Checking the pack."),
        turn(3, "add_to_cart", "B1S1P1", "Taking these."),
        turn(4, "next_station", null, "Moving along."),
        turn(5, "checkout", null, "Got what I came for.", "B2"),
      ],
    },
  ],
};

const BROWSER_TRACE = {
  ...MISSION_TRACE,
  persona_id: "browser",
  n_turns: 2,
  shoppers: [
    {
      ...MISSION_TRACE.shoppers[0],
      n_turns: 2,
      turns: [
        turn(1, "look", "B2S1P1", "Wandering."),
        turn(2, "checkout", null, "Nothing today."),
      ],
    },
  ],
};

interface Routes {
  status?: unknown;
  trace?: unknown;
  browserTrace?: unknown;
  traceStatus?: number;
}

interface Harness {
  container: HTMLDivElement;
  settle: () => Promise<void>;
  unmount: () => void;
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

async function mount(routes: Routes = {}): Promise<Harness> {
  const container = document.createElement("div");
  document.body.appendChild(container);

  const fetchImpl: FetchLike = async (input) => {
    const path = String(input);
    if (path.includes("/ai/status")) return json(200, routes.status ?? STATUS);
    if (path.includes("/resolved")) return json(200, PLANOGRAM);
    if (path.includes("/personas/browser/trace")) {
      return json(200, routes.browserTrace ?? BROWSER_TRACE);
    }
    if (path.includes("/trace")) {
      const code = routes.traceStatus ?? 200;
      if (code !== 200) {
        return json(code, {
          detail: "no trace ... Generate one with: python -m sim.slow_agent --all",
        });
      }
      return json(200, routes.trace ?? MISSION_TRACE);
    }
    return json(404, { detail: `unrouted ${path}` });
  };

  let root: Root | null = null;
  await act(async () => {
    root = createRoot(container);
    root.render(<PanelView fetchImpl={fetchImpl} />);
  });

  const settle = async () => {
    await act(async () => {
      for (let n = 0; n < 8; n += 1) await Promise.resolve();
    });
  };
  await settle();

  return {
    container,
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

function click(harness: Harness, testId: string): void {
  const button = node(harness, testId);
  act(() => button.dispatchEvent(new MouseEvent("click", { bubbles: true })));
}

describe("the shelf is drawn as products", () => {
  it("names every occupied slot by its product, never by its slot id", async () => {
    const harness = await mount();
    try {
      const slot = text(harness, "panel-slot-B1S1P1");
      expect(slot).toContain("Crunch Chips 100g");
      expect(slot).not.toContain("B1S1P1");
    } finally {
      harness.unmount();
    }
  });

  it("draws the empty positions too, and says they are empty", async () => {
    // `B1S3P2` is where variant B moves the focal SKU. A map that dropped it
    // would delete the thing the known-effect arm is about.
    const harness = await mount();
    try {
      expect(text(harness, "panel-slot-B1S3P2").toLowerCase()).toContain("empty");
    } finally {
      harness.unmount();
    }
  });

  it("labels each shelf with the level the planogram gives it", async () => {
    const harness = await mount();
    try {
      expect(text(harness, "panel-shelf-B1S3")).toContain("eye level");
    } finally {
      harness.unmount();
    }
  });
});

describe("replaying one shopper's trip", () => {
  it("starts on the first turn and shows the model's own reason", async () => {
    const harness = await mount();
    try {
      expect(text(harness, "panel-progress")).toContain("1");
      expect(text(harness, "panel-caption")).toContain("A promotion caught my eye.");
      expect(text(harness, "panel-caption")).toContain("look");
    } finally {
      harness.unmount();
    }
  });

  it("marks the slot the current turn is about", async () => {
    const harness = await mount();
    try {
      expect(node(harness, "panel-slot-B1S1P1").dataset.target).toBe("true");
      expect(node(harness, "panel-slot-B1S2P1").dataset.target).toBe("false");
    } finally {
      harness.unmount();
    }
  });

  it("marks the bay the shopper is standing at", async () => {
    const harness = await mount();
    try {
      expect(node(harness, "panel-bay-B1").dataset.current).toBe("true");
      expect(node(harness, "panel-bay-B2").dataset.current).toBe("false");
    } finally {
      harness.unmount();
    }
  });

  it("advances a turn at a time", async () => {
    const harness = await mount();
    try {
      click(harness, "panel-step-forward");
      expect(text(harness, "panel-caption")).toContain("Checking the pack.");
      expect(text(harness, "panel-progress")).toContain("2");
    } finally {
      harness.unmount();
    }
  });

  it("goes back, and stops at the first turn", async () => {
    const harness = await mount();
    try {
      click(harness, "panel-step-forward");
      click(harness, "panel-step-back");
      click(harness, "panel-step-back");
      expect(text(harness, "panel-progress")).toContain("1");
    } finally {
      harness.unmount();
    }
  });

  it("follows the shopper to the next bay", async () => {
    const harness = await mount();
    try {
      for (let n = 0; n < 4; n += 1) click(harness, "panel-step-forward");
      expect(node(harness, "panel-bay-B2").dataset.current).toBe("true");
      expect(node(harness, "panel-bay-B1").dataset.current).toBe("false");
    } finally {
      harness.unmount();
    }
  });

  it("highlights nothing when the turn has no target", async () => {
    const harness = await mount();
    try {
      for (let n = 0; n < 3; n += 1) click(harness, "panel-step-forward");
      expect(text(harness, "panel-caption")).toContain("next_station");
      expect(node(harness, "panel-slot-B1S1P1").dataset.target).toBe("false");
    } finally {
      harness.unmount();
    }
  });
});

describe("the cart is what the trace says it is", () => {
  it("is empty at the start", async () => {
    const harness = await mount();
    try {
      expect(text(harness, "panel-cart").toLowerCase()).toContain("nothing");
    } finally {
      harness.unmount();
    }
  });

  it("does not fill on pickup", async () => {
    const harness = await mount();
    try {
      click(harness, "panel-step-forward"); // turn 2: pickup
      expect(text(harness, "panel-cart")).not.toContain("Crunch Chips 100g");
    } finally {
      harness.unmount();
    }
  });

  it("fills on add_to_cart, with the product's name and price", async () => {
    const harness = await mount();
    try {
      click(harness, "panel-step-forward");
      click(harness, "panel-step-forward"); // turn 3: add_to_cart
      const cart = text(harness, "panel-cart");
      expect(cart).toContain("Crunch Chips 100g");
      expect(cart).toContain("25");
    } finally {
      harness.unmount();
    }
  });
});

describe("changing what is being replayed", () => {
  it("loads another persona's trace and restarts from its first turn", async () => {
    const harness = await mount();
    try {
      click(harness, "panel-step-forward");
      click(harness, "panel-step-forward");

      click(harness, "panel-persona-browser");
      await harness.settle();

      expect(text(harness, "panel-caption")).toContain("Wandering.");
      expect(text(harness, "panel-progress")).toContain("1");
      // The cart must not carry across: it belongs to the trip that filled it.
      expect(text(harness, "panel-cart").toLowerCase()).toContain("nothing");
    } finally {
      harness.unmount();
    }
  });
});

describe("playing on its own", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("advances without being clicked, and stops at the end of the trip", async () => {
    const harness = await mount();
    try {
      click(harness, "panel-play");
      await act(async () => {
        vi.advanceTimersByTime(10_000);
      });

      expect(text(harness, "panel-progress")).toContain("5");
      expect(text(harness, "panel-caption")).toContain("Got what I came for.");
      // Stopped, not looping: the button offers to play again rather than pause.
      expect(node(harness, "panel-play").textContent?.toLowerCase()).toContain("play");
    } finally {
      harness.unmount();
    }
  });
});

describe("when there is nothing to replay", () => {
  it("says how to generate a trace rather than drawing an idle shopper", async () => {
    const harness = await mount({ traceStatus: 404 });
    try {
      expect(text(harness, "panel-caption")).toContain("sim.slow_agent");
    } finally {
      harness.unmount();
    }
  });
});

describe("the panel is a way back, not a dead end", () => {
  it("links to the operator launcher", async () => {
    const harness = await mount();
    try {
      expect(node(harness, "panel-home-link").getAttribute("href")).toBe("#/home");
    } finally {
      harness.unmount();
    }
  });
});
