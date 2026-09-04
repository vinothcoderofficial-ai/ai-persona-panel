import { describe, expect, it } from "vitest";
import type { ReactNode } from "react";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { WhatIfPanel } from "@/whatif/WhatIfPanel";
import { postWhatIf, type FetchLike, type WhatIfResponse } from "@/whatif/client";
import { NOT_APPLICABLE } from "@/whatif/lift";
import type { WhatIfRequestBody } from "@/whatif/patches";
import { chooseOption, demoAisle, fakeClock, simResult } from "./whatifFixture";

/**
 * The what-if page end to end, with the endpoint and the debounce timer both
 * injected: no server, no clock, no waiting.
 *
 * Three things this file exists to hold still:
 *
 *  * the request body is exactly what `POST /whatif` allows - its model is
 *    `extra="forbid"`, so one stray key 422s the whole call;
 *  * a burst of dropdown changes is one simulation, not four (SPEC M9's 300 ms);
 *  * an uncomputed lift reads as "not applicable" and never as 0%.
 */

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

const PERSONAS = ["browser", "loyalist", "mission", "switcher"];

/** Where SKU_008 sits in the seed aisle, and where "eye level" sends it. */
const BOTTOM_SLOT = "B1S5P1";
const EYE_SLOT = "B1S3P2";

function baselineResponse(): WhatIfResponse {
  return {
    sim_run_id: "wi_baseline",
    elapsed_ms: 9,
    per_persona: Object.fromEntries(
      PERSONAS.map((personaId, index) => [
        personaId,
        simResult(personaId, { [BOTTOM_SLOT]: 0.02 + index * 0.01, [EYE_SLOT]: 0 }),
      ]),
    ),
    population_fixation_prob: { [BOTTOM_SLOT]: 0.035, [EYE_SLOT]: 0 },
    lift_vs_baseline: {},
    ad_slot_attention: { B3_ENDCAP: 0.18 },
  };
}

function movedResponse(): WhatIfResponse {
  return {
    sim_run_id: "wi_moved",
    elapsed_ms: 12,
    per_persona: Object.fromEntries(
      PERSONAS.map((personaId, index) => [
        personaId,
        simResult(personaId, { [BOTTOM_SLOT]: 0, [EYE_SLOT]: (0.02 + index * 0.01) * 1.5 }),
      ]),
    ),
    population_fixation_prob: { [BOTTOM_SLOT]: 0, [EYE_SLOT]: 0.062 },
    lift_vs_baseline: { focal_sku_attention: 0.777, focal_sku_purchase_share: null },
    ad_slot_attention: { B3_ENDCAP: 0.18 },
  };
}

interface Harness {
  container: HTMLDivElement;
  bodies: WhatIfRequestBody[];
  delays: number[];
  flush: () => Promise<void>;
  settle: () => Promise<void>;
  unmount: () => void;
}

interface HarnessOptions {
  /** Answers each request. Defaults to baseline for [], moved otherwise. */
  respond?: (body: WhatIfRequestBody) => Promise<WhatIfResponse>;
}

function defaultRespond(body: WhatIfRequestBody): Promise<WhatIfResponse> {
  return Promise.resolve(body.patches.length === 0 ? baselineResponse() : movedResponse());
}

function mount(node: ReactNode): { container: HTMLDivElement; unmount: () => void } {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(node);
  });
  return {
    container,
    unmount: () => {
      act(() => root.unmount());
      container.remove();
    },
  };
}

async function settle(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

function render(options: HarnessOptions = {}): Harness {
  const bodies: WhatIfRequestBody[] = [];
  const clock = fakeClock();
  const respond = options.respond ?? defaultRespond;

  const view = mount(
    <WhatIfPanel
      planogram={demoAisle()}
      runWhatIf={(body) => {
        bodies.push(body);
        return respond(body);
      }}
      schedule={clock.schedule}
      // The sweep has its own tests; this file is about the numbers.
      reducedMotion
    />,
  );

  return {
    container: view.container,
    bodies,
    delays: clock.delays,
    unmount: view.unmount,
    settle,
    flush: async () => {
      act(() => clock.flush());
      await settle();
    },
  };
}

function find(container: HTMLElement, testId: string): HTMLElement {
  const element = container.querySelector<HTMLElement>(`[data-testid="${testId}"]`);
  if (element === null) throw new Error(`no element with data-testid="${testId}"`);
  return element;
}

function text(container: HTMLElement, testId: string): string {
  return (find(container, testId).textContent ?? "").trim();
}

function pick(container: HTMLElement, testId: string, value: string): void {
  chooseOption(find(container, testId) as HTMLSelectElement, value);
}

describe("the what-if page on load", () => {
  it("asks for the exactly-neutral baseline, and for nothing else", async () => {
    const view = render();
    await view.settle();

    expect(view.bodies).toHaveLength(1);
    expect(view.bodies[0]).toEqual({
      base_planogram_id: "demo_aisle",
      patches: [],
      n_synth: 10_000,
      seed: 42,
    });
    // POST /whatif is extra="forbid": one unexpected key 422s the whole call.
    expect(Object.keys(view.bodies[0]).sort()).toEqual([
      "base_planogram_id",
      "n_synth",
      "patches",
      "seed",
    ]);
    view.unmount();
  });

  it("shows elapsed_ms, and says what it measures", async () => {
    const view = render();
    await view.settle();

    expect(text(view.container, "whatif-elapsed")).toBe("9 ms");
    expect(text(view.container, "whatif-elapsed-note")).toMatch(/server/i);
    expect(text(view.container, "whatif-elapsed-note")).toMatch(/round.?trip/i);
    view.unmount();
  });

  it("reports an empty lift_vs_baseline as not applicable, never as 0%", async () => {
    const view = render();
    await view.settle();

    const shown = text(view.container, "whatif-lift-focal_sku_attention");
    expect(shown).toBe(NOT_APPLICABLE);
    expect(shown).not.toContain("0%");
    view.unmount();
  });

  it("draws the population attention it was given", async () => {
    const view = render();
    await view.settle();

    expect(find(view.container, `heat-diff-row-${BOTTOM_SLOT}`).dataset.value).toBe("0.035");
    view.unmount();
  });
});

describe("a burst of dropdown changes", () => {
  it("is one simulation, carrying the last value", async () => {
    const view = render();
    await view.settle();
    expect(view.bodies).toHaveLength(1);

    pick(view.container, "whatif-focal-sku", "SKU_008");
    pick(view.container, "whatif-shelf-level", "bottom");
    pick(view.container, "whatif-shelf-level", "eye");
    pick(view.container, "whatif-promo", "on");
    expect(view.bodies).toHaveLength(1);

    await view.flush();

    expect(view.bodies).toHaveLength(2);
    expect(view.bodies[1]).toEqual({
      base_planogram_id: "demo_aisle",
      patches: [
        { op: "move_sku", sku_id: "SKU_008", to_slot_id: EYE_SLOT },
        { op: "set_price", sku_id: "SKU_008", price: 25, promo: true },
      ],
      n_synth: 10_000,
      seed: 42,
      focal_sku_id: "SKU_008",
    });
    view.unmount();
  });

  it("waits the 300 ms SPEC M9 asks for, every time", async () => {
    const view = render();
    await view.settle();

    pick(view.container, "whatif-focal-sku", "SKU_008");
    pick(view.container, "whatif-shelf-level", "eye");
    await view.flush();

    expect(view.delays.length).toBeGreaterThan(0);
    expect([...new Set(view.delays)]).toEqual([300]);
    view.unmount();
  });

  it("sends an ad-only change with no focal_sku_id, so no lift is claimed", async () => {
    const view = render();
    await view.settle();

    pick(view.container, "whatif-ad-slot", "B1_TALKER");
    pick(view.container, "whatif-creative", "AD_1");
    await view.flush();

    expect(view.bodies[1]).toEqual({
      base_planogram_id: "demo_aisle",
      patches: [{ op: "set_ad_creative", ad_slot_id: "B1_TALKER", creative_id: "AD_1" }],
      n_synth: 10_000,
      seed: 42,
    });
    view.unmount();
  });
});

describe("after a re-run", () => {
  it("shows the server's lift, and its null key as not applicable", async () => {
    const view = render();
    await view.settle();

    pick(view.container, "whatif-focal-sku", "SKU_008");
    pick(view.container, "whatif-shelf-level", "eye");
    await view.flush();

    expect(text(view.container, "whatif-lift-focal_sku_attention")).toBe("+77.7%");
    const undefinedLift = text(view.container, "whatif-lift-focal_sku_purchase_share");
    expect(undefinedLift).toBe(NOT_APPLICABLE);
    expect(undefinedLift).not.toContain("0%");
    view.unmount();
  });

  it("measures each persona against the baseline run it opened with", async () => {
    const view = render();
    await view.settle();

    pick(view.container, "whatif-focal-sku", "SKU_008");
    pick(view.container, "whatif-shelf-level", "eye");
    await view.flush();

    // Every persona's fixation probability on the focal SKU went up by half.
    for (const personaId of PERSONAS) {
      expect(text(view.container, `whatif-persona-lift-${personaId}`)).toBe("+50.0%");
    }
    view.unmount();
  });

  it("keeps every figure describing the same run while a change is still pending", async () => {
    const view = render();
    await view.settle();

    // The selection already says eye level; the numbers on screen are still the
    // baseline run. A lift computed across that seam - new slot, old result -
    // would describe a run nobody performed.
    pick(view.container, "whatif-focal-sku", "SKU_008");
    pick(view.container, "whatif-shelf-level", "eye");
    expect(view.container.querySelector('[data-testid="whatif-persona-lift-browser"]')).toBeNull();
    expect(find(view.container, "whatif-persona-lift-empty")).toBeTruthy();

    await view.flush();
    expect(text(view.container, "whatif-persona-lift-browser")).toBe("+50.0%");
    view.unmount();
  });

  it("animates from the run before it, not from nothing", async () => {
    const view = render();
    await view.settle();

    pick(view.container, "whatif-focal-sku", "SKU_008");
    pick(view.container, "whatif-shelf-level", "eye");
    await view.flush();

    const row = find(view.container, `heat-diff-row-${EYE_SLOT}`);
    expect(row.dataset.value).toBe("0.062");
    expect(row.dataset.previous).toBe("0");
    expect(text(view.container, "whatif-elapsed")).toBe("12 ms");
    view.unmount();
  });
});

describe("when the endpoint refuses the request", () => {
  function failing(status: number, detail: string): FetchLike {
    return () =>
      Promise.resolve({
        ok: false,
        status,
        statusText: "Bad Request",
        text: () => Promise.resolve(JSON.stringify({ detail })),
      });
  }

  it("puts the 400's own detail message on screen", async () => {
    const detail = "move_sku: unknown to_slot_id 'B9S9P9'";
    const view = render({ respond: (body) => postWhatIf(body, failing(400, detail)) });
    await view.settle();

    const alert = find(view.container, "whatif-error");
    expect(alert.getAttribute("role")).toBe("alert");
    expect(alert.textContent).toContain(detail);
    expect(alert.textContent).toContain("400");
    view.unmount();
  });

  it("marks the page stale rather than leaving numbers looking current", async () => {
    const view = render({ respond: (body) => postWhatIf(body, failing(400, "no such slot")) });
    await view.settle();

    expect(find(view.container, "whatif-panel").dataset.stale).toBe("true");
    view.unmount();
  });

  it("offers a re-run, so the same selection can be asked for twice", async () => {
    // Without this the only way out of a failed call is to change a dropdown:
    // the request is otherwise suppressed as "already on screen", which it is not.
    let fail = true;
    const view = render({
      respond: (body) => {
        if (fail) {
          fail = false;
          return postWhatIf(body, failing(400, "the API was not running"));
        }
        return defaultRespond(body);
      },
    });
    await view.settle();
    expect(view.bodies).toHaveLength(1);

    act(() => {
      find(view.container, "whatif-retry").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await settle();

    expect(view.bodies).toHaveLength(2);
    expect(view.container.querySelector('[data-testid="whatif-error"]')).toBeNull();
    expect(text(view.container, "whatif-elapsed")).toBe("9 ms");
    view.unmount();
  });

  it("clears the alert once a run succeeds again", async () => {
    let fail = true;
    const view = render({
      respond: (body) => {
        if (fail) {
          fail = false;
          return postWhatIf(body, failing(400, "no such slot"));
        }
        return defaultRespond(body);
      },
    });
    await view.settle();
    expect(view.container.querySelector('[data-testid="whatif-error"]')).not.toBeNull();

    pick(view.container, "whatif-focal-sku", "SKU_008");
    pick(view.container, "whatif-shelf-level", "eye");
    await view.flush();

    expect(view.container.querySelector('[data-testid="whatif-error"]')).toBeNull();
    expect(find(view.container, "whatif-panel").dataset.stale).toBe("false");
    view.unmount();
  });
});

describe("when the planogram itself will not load", () => {
  it("says so instead of rendering empty controls", async () => {
    const view = mount(
      <WhatIfPanel
        loadPlanogram={() => Promise.reject(new Error("GET /variants/A/resolved failed: 404"))}
        runWhatIf={() => Promise.resolve(baselineResponse())}
        reducedMotion
      />,
    );
    await settle();

    expect(find(view.container, "whatif-error").textContent).toContain(
      "GET /variants/A/resolved failed: 404",
    );
    expect(view.container.querySelector('[data-testid="whatif-focal-sku"]')).toBeNull();
    view.unmount();
  });

  it("loads it from the variant it was pointed at", async () => {
    const asked: string[] = [];
    const view = mount(
      <WhatIfPanel
        variantId="A"
        loadPlanogram={(variantId) => {
          asked.push(variantId);
          return Promise.resolve(demoAisle());
        }}
        runWhatIf={() => Promise.resolve(baselineResponse())}
        reducedMotion
      />,
    );
    await settle();

    expect(asked).toEqual(["A"]);
    expect(text(view.container, "whatif-elapsed")).toBe("9 ms");
    view.unmount();
  });
});
