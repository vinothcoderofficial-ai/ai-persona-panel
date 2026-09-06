import { describe, expect, it } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { AiPanel } from "@/ai/AiPanel";
import { readFileSync } from "node:fs";
import { resolve as resolvePath } from "node:path";
import type { FetchLike } from "@/ai/client";

/**
 * `#/ai` - the screen that answers "where is the AI, and how do I trigger it".
 *
 * Before this screen, nothing under `api/` or `web/` imported `sim/llm_client.py`:
 * the model's work sat in `data/cache/` and was rendered by nothing, so a viewer
 * had no way to tell a language model had ever been involved. This suite pins
 * the four things that make the claim checkable rather than asserted:
 *
 *  * the model that is configured **and whether it can actually be called**,
 *    with the server's own reason when it cannot;
 *  * the rendered prompt, beside the policy it produced - provenance, not just
 *    eleven numbers;
 *  * a re-ask that reports which fields moved, and never claims to have adopted
 *    anything (`data/cache/policies/` is pre-registered input);
 *  * the trace: the model's own stated reasons, and carts named as products
 *    rather than as `SKU_008`.
 */

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

const PLANOGRAM = JSON.parse(
  readFileSync(resolvePath(__dirname, "../../data/planograms/demo_aisle.json"), "utf-8"),
) as unknown;

const STATUS = {
  provider: "ollama",
  model: "deepseek-v4-pro:cloud",
  base_url: "https://ollama.com/v1",
  offline: false,
  api_key_set: true,
  can_call: true,
  reason: null as string | null,
  planogram_id: "demo_aisle",
  prompt_template: "sim/prompts/persona_policy.md",
  personas: [
    {
      persona_id: "mission",
      archetype: "mission",
      share_of_population: 0.35,
      description: "Comes with a list, time-pressed.",
      policy_cached: true,
      trace_cached: true,
      trace_model: "deepseek-v4-pro:cloud",
      trace_n_shoppers: 20,
      trace_n_turns: 99,
      trace_temperature: 0.7,
    },
    {
      persona_id: "browser",
      archetype: "browser",
      share_of_population: 0.25,
      description: "No list, high exploration.",
      policy_cached: true,
      trace_cached: false,
      trace_model: null,
      trace_n_shoppers: null,
      trace_n_turns: null,
      trace_temperature: null,
    },
  ],
};

const POLICY = {
  persona_id: "mission",
  planogram_id: "demo_aisle",
  source: "cache",
  policy: {
    persona_id: "mission",
    goal_categories: ["chips", "cola"],
    time_budget_s: { mean: 45, sd: 10 },
    exploration: 0.05,
    brand_affinity: { _default: 0.5, Crunch: 0.55 },
    price_sensitivity: 0.4,
    promo_sensitivity: 0.2,
    ad_receptivity: 0.15,
    purchase_threshold: 0.25,
    dwell_ms: { mu: 6, sigma: 0.45 },
    fixations_per_station: { lam: 2.5 },
  },
  prompt: "Archetype: Comes with a list, time-pressed.\nBrands: Crunch, Nimbus",
};

const TRACE = {
  trace_version: 1,
  persona_id: "mission",
  archetype: "mission",
  description: "Comes with a list, time-pressed.",
  planogram_id: "demo_aisle",
  n_shoppers: 1,
  seed: 42,
  temperature: 0.7,
  model: "deepseek-v4-pro:cloud",
  n_turns: 2,
  n_rejections: 0,
  end_reasons: { checkout: 1 },
  carts: { SKU_001: 1 },
  shoppers: [
    {
      shopper_index: 0,
      n_turns: 2,
      n_rejections: 0,
      end_reason: "checkout",
      end_note: null,
      stations_visited: ["B1"],
      rejections: [],
      cart: ["SKU_001"],
      cart_detail: [
        {
          sku_id: "SKU_001",
          name: "Crunch Chips 100g",
          brand: "Crunch",
          category: "chips",
          price: 25,
          slot_id: "B1S1P1",
        },
      ],
      turns: [
        {
          turn: 1,
          station_id: "B1",
          action: "pickup",
          target: "B1S1P1",
          reason: "Need chips; this one is cheap and on promotion.",
          time_left_s: 39,
        },
        {
          turn: 2,
          station_id: "B1",
          action: "checkout",
          target: null,
          reason: "I have what I came for.",
          time_left_s: 20,
        },
      ],
    },
  ],
};

interface Route {
  status?: unknown;
  /** Non-200 to simulate a planogram the panel cannot fetch. */
  planogramStatus?: number;
  policy?: unknown;
  trace?: unknown;
  /** [status, body] for the POST re-ask. */
  regenerate?: [number, unknown];
}

interface Harness {
  container: HTMLDivElement;
  posts: string[];
  settle: () => Promise<void>;
  unmount: () => void;
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

async function mount(routes: Route): Promise<Harness> {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const posts: string[] = [];

  const fetchImpl: FetchLike = async (input, init) => {
    const path = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    if (method === "POST") {
      posts.push(path);
      const [code, body] = routes.regenerate ?? [200, {}];
      return json(code, body);
    }
    if (path.endsWith("/ai/status")) return json(200, routes.status ?? STATUS);
    if (path.includes("/resolved")) {
      const code = routes.planogramStatus ?? 200;
      if (code !== 200) return json(code, { detail: "no planogram" });
      return json(200, PLANOGRAM);
    }
    if (path.endsWith("/policy")) return json(200, routes.policy ?? POLICY);
    if (path.endsWith("/trace")) {
      if (routes.trace === null) {
        return json(404, { detail: "no trace ... Generate one with: python -m sim.slow_agent --all" });
      }
      return json(200, routes.trace ?? TRACE);
    }
    return json(404, { detail: `unrouted ${path}` });
  };

  let root: Root | null = null;
  await act(async () => {
    root = createRoot(container);
    root.render(<AiPanel fetchImpl={fetchImpl} />);
  });

  const settle = async () => {
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
  };
  await settle();

  return {
    container,
    posts,
    settle,
    unmount: () => {
      act(() => root?.unmount());
      container.remove();
    },
  };
}

function text(harness: Harness, testId: string): string {
  const node = harness.container.querySelector(`[data-testid="${testId}"]`);
  return node?.textContent ?? "";
}

function click(harness: Harness, testId: string): void {
  const node = harness.container.querySelector<HTMLButtonElement>(
    `[data-testid="${testId}"]`,
  );
  if (node === null) throw new Error(`no [data-testid="${testId}"] to click`);
  act(() => {
    node.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

describe("the AI panel names the model it is configured to use", () => {
  it("shows the provider and the model", async () => {
    const harness = await mount({});
    try {
      const status = text(harness, "ai-status");
      expect(status).toContain("ollama");
      expect(status).toContain("deepseek-v4-pro:cloud");
    } finally {
      harness.unmount();
    }
  });

  it("shows the server's reason and disables the button when it cannot call", async () => {
    const reason = "LLM_API_KEY is not set. Set it in .env, or set LLM_PROVIDER=ollama.";
    const harness = await mount({
      status: { ...STATUS, can_call: false, api_key_set: false, reason },
    });
    try {
      expect(text(harness, "ai-cannot-call")).toContain("LLM_API_KEY");
      const button = harness.container.querySelector<HTMLButtonElement>(
        '[data-testid="ai-ask"]',
      );
      expect(button?.disabled).toBe(true);
    } finally {
      harness.unmount();
    }
  });

  it("enables the button when the server says a call would go out", async () => {
    const harness = await mount({});
    try {
      const button = harness.container.querySelector<HTMLButtonElement>(
        '[data-testid="ai-ask"]',
      );
      expect(button?.disabled).toBe(false);
      expect(harness.container.querySelector('[data-testid="ai-cannot-call"]')).toBeNull();
    } finally {
      harness.unmount();
    }
  });

  it("says a credential is configured, never that a call is known to work", async () => {
    // `GET /ai/status` makes no request, so it cannot know the key is accepted.
    // It was wrong in the first live run against Ollama Cloud: the key was set
    // and expired, this line said "available", and the button then failed with
    // a 401. The screen must not claim what the server did not check.
    const harness = await mount({});
    try {
      const status = text(harness, "ai-status");
      expect(status).toContain("configured");
      expect(status).not.toContain("available");
    } finally {
      harness.unmount();
    }
  });

  it("survives a status body that carries no personas", async () => {
    // A proxy answering 200 with something else, an older API, a half-written
    // response: the screen must render and say what it knows, not throw and
    // leave a blank page. Found by `web/tests/hashRouting.test.tsx`, whose
    // fetch stub returns exactly this.
    const harness = await mount({ status: { provider: "ollama", model: "m" } });
    try {
      expect(harness.container.querySelector('[data-testid="ai-panel"]')).not.toBeNull();
      expect(text(harness, "ai-status")).toContain("ollama");
    } finally {
      harness.unmount();
    }
  });
});

describe("the AI panel shows what the model was asked", () => {
  it("renders the rendered prompt, not a summary of it", async () => {
    const harness = await mount({});
    try {
      expect(text(harness, "ai-prompt")).toContain("Archetype: Comes with a list");
      expect(text(harness, "ai-prompt")).toContain("Crunch");
    } finally {
      harness.unmount();
    }
  });

  it("renders the policy the prompt produced", async () => {
    const harness = await mount({});
    try {
      const policy = text(harness, "ai-policy");
      expect(policy).toContain("exploration");
      expect(policy).toContain("0.05");
      expect(policy).toContain("chips");
    } finally {
      harness.unmount();
    }
  });
});

describe("re-asking the model", () => {
  it("POSTs to the selected persona and reports which fields moved", async () => {
    const harness = await mount({
      regenerate: [
        200,
        {
          persona_id: "mission",
          source: "llm",
          provider: "ollama",
          model: "deepseek-v4-pro:cloud",
          elapsed_s: 1.25,
          policy: { ...POLICY.policy, exploration: 0.09 },
          committed: POLICY.policy,
          differs: true,
          changed_fields: ["exploration"],
          written_to: "data/cache/policies/preview/mission_demo_aisle.json",
          adopted: false,
        },
      ],
    });
    try {
      click(harness, "ai-ask");
      await harness.settle();

      expect(harness.posts).toEqual(["/api/ai/personas/mission/policy"]);
      const diff = text(harness, "ai-diff");
      expect(diff).toContain("exploration");
      expect(diff).toContain("0.05");
      expect(diff).toContain("0.09");
    } finally {
      harness.unmount();
    }
  });

  it("says plainly that nothing was adopted", async () => {
    const harness = await mount({
      regenerate: [
        200,
        {
          persona_id: "mission",
          source: "llm",
          model: "m",
          elapsed_s: 1,
          policy: { ...POLICY.policy, exploration: 0.09 },
          committed: POLICY.policy,
          differs: true,
          changed_fields: ["exploration"],
          written_to: "data/cache/policies/preview/mission_demo_aisle.json",
          adopted: false,
        },
      ],
    });
    try {
      click(harness, "ai-ask");
      await harness.settle();

      // The simulator is still running the committed policy, and a screen that
      // let anyone believe otherwise would be claiming a result it did not have.
      expect(text(harness, "ai-not-adopted").toLowerCase()).toContain("not");
      expect(text(harness, "ai-not-adopted")).toContain("preview");
    } finally {
      harness.unmount();
    }
  });

  it("reports an identical answer as identical rather than inventing a diff", async () => {
    const harness = await mount({
      regenerate: [
        200,
        {
          persona_id: "mission",
          source: "llm",
          model: "m",
          elapsed_s: 1,
          policy: POLICY.policy,
          committed: POLICY.policy,
          differs: false,
          changed_fields: [],
          written_to: "x",
          adopted: false,
        },
      ],
    });
    try {
      click(harness, "ai-ask");
      await harness.settle();

      expect(text(harness, "ai-diff").toLowerCase()).toContain("same");
    } finally {
      harness.unmount();
    }
  });

  it("shows the server's own sentence when the call fails", async () => {
    const detail =
      "policy for 'mission' names brand(s) not in planogram 'demo_aisle': ['Fictional']";
    const harness = await mount({ regenerate: [422, { detail }] });
    try {
      click(harness, "ai-ask");
      await harness.settle();

      expect(text(harness, "ai-ask-error")).toContain("Fictional");
    } finally {
      harness.unmount();
    }
  });
});

describe("the trace is the evidence that a model reasoned", () => {
  it("renders each turn's action and the model's own reason", async () => {
    const harness = await mount({});
    try {
      const trace = text(harness, "ai-trace");
      expect(trace).toContain("pickup");
      expect(trace).toContain("Need chips; this one is cheap and on promotion.");
      expect(trace).toContain("checkout");
    } finally {
      harness.unmount();
    }
  });

  it("names the cart in products, never in bare sku ids", async () => {
    const harness = await mount({});
    try {
      const trace = text(harness, "ai-trace");
      expect(trace).toContain("Crunch Chips 100g");
    } finally {
      harness.unmount();
    }
  });

  it("names the model that produced the trace", async () => {
    const harness = await mount({});
    try {
      expect(text(harness, "ai-trace-header")).toContain("deepseek-v4-pro:cloud");
    } finally {
      harness.unmount();
    }
  });

  it("says how to generate a trace that does not exist yet", async () => {
    const harness = await mount({ trace: null });
    try {
      expect(text(harness, "ai-trace")).toContain("sim.slow_agent");
    } finally {
      harness.unmount();
    }
  });
});

describe("the panel is a way back, not a dead end", () => {
  it("links to the operator launcher", async () => {
    const harness = await mount({});
    try {
      const link = harness.container.querySelector<HTMLAnchorElement>(
        '[data-testid="ai-home-link"]',
      );
      expect(link?.getAttribute("href")).toBe("#/home");
    } finally {
      harness.unmount();
    }
  });
});

describe("the trace names the shelf positions it moved through", () => {
  it("shows the product a turn is about, not only its slot id", async () => {
    // Asserted on the turn itself rather than on the trace as a whole: the cart
    // line already carried a product name from `cart_detail`, so a whole-trace
    // assertion passes without the turns being named at all. It did, on the
    // first run of this test.
    const harness = await mount({});
    try {
      const turn = text(harness, "ai-turn-0-1");
      expect(turn).toContain("Crunch Chips 100g");
      expect(turn).toContain("Need chips; this one is cheap and on promotion.");
    } finally {
      harness.unmount();
    }
  });

  it("still renders the turns when the planogram cannot be fetched", async () => {
    const harness = await mount({ planogramStatus: 500 });
    try {
      const turn = text(harness, "ai-turn-0-1");
      expect(turn).toContain("B1S1P1");
      expect(turn).toContain("Need chips; this one is cheap and on promotion.");
    } finally {
      harness.unmount();
    }
  });
});
