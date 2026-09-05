import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { act } from "react";
import { createRoot } from "react-dom/client";
import Experiment, { NOT_APPLICABLE, formatMetric } from "@/dashboard/Experiment";
import { REAL, SYNTH, mono } from "@/dashboard/styles";

/**
 * The dashboard used to render as unstyled default HTML -- correct numbers,
 * no presentation. This restyle must not change a single one of those
 * numbers, so these tests are about the three things a restyle can actually
 * get wrong:
 *
 *  1. The two headline metrics (Attention Spearman, Purchase-share MAE) must
 *     still both reach the screen as real figures.
 *  2. A metric that is absent must read as absent, never as a fabricated 0 --
 *     the same rule `web/src/whatif/lift.ts:formatLift` enforces for the
 *     what-if panel, and a computed 0 (a real result) must not be caught by
 *     the same net.
 *  3. Real and synthetic must be distinguishable the same way on every
 *     screen: real in blue, synthetic in amber.
 *
 * `Experiment` fetches on mount from `window.location.search` (no props), so
 * these tests set the query string with `history.replaceState` and stub
 * `fetch`, the same way `web/tests/devSessionFinish.test.tsx` drives main.tsx.
 * `ResizeObserver` is stubbed too: the chart is wrapped in recharts'
 * `ResponsiveContainer`, which constructs one unconditionally on mount, and
 * jsdom does not provide one.
 */

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

/** Exactly what `api/app/routers/experiments.py:_build_experiment` returns. */
const BASE_RESPONSE = {
  experiment_id: "exp_20260904_1a2b3c4d5e6f",
  variant_id: "var_eye_level_shift",
  session_id: "sess_9f8e7d6c5b4a3928",
  n_synth: 10_000,
  seed: 42,
  slot_ids: ["B1S3P1", "B1S3P2", "B1S3P3"],
  real_attention: { B1S3P1: 0.41, B1S3P2: 0.22, B1S3P3: 0.11 },
  synth_attention: { B1S3P1: 0.37, B1S3P2: 0.28, B1S3P3: 0.09 },
  attention_spearman: 0.482,
  purchase_share_mae: 0.0134,
  real_purchase_share: { SKU_1: 0.6, SKU_2: 0.4 },
  synth_purchase_share: { SKU_1: 0.55, SKU_2: 0.45 },
};

class FakeResizeObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

interface Mounted {
  container: HTMLDivElement;
  unmount: () => void;
}

function mount(node: ReactNode): Mounted {
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

function find(container: HTMLElement, testId: string): HTMLElement {
  const element = container.querySelector<HTMLElement>(`[data-testid="${testId}"]`);
  if (element === null) throw new Error(`no element with data-testid="${testId}"`);
  return element;
}

/** Round-trips a hex colour through the DOM's own CSSOM serialisation. */
function hexToRgb(hex: string): string {
  const value = hex.replace("#", "");
  const r = parseInt(value.slice(0, 2), 16);
  const g = parseInt(value.slice(2, 4), 16);
  const b = parseInt(value.slice(4, 6), 16);
  return `rgb(${r}, ${g}, ${b})`;
}

function stubFetch(payload: Record<string, unknown>): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: unknown): Promise<Response> => {
      const url = String(input);
      if (!url.includes("/experiments/")) {
        throw new Error(`dashboardExperiment test: unexpected fetch ${url}`);
      }
      return {
        ok: true,
        status: 200,
        statusText: "OK",
        json: async () => payload,
        text: async () => "",
      } as Response;
    }),
  );
}

function renderExperiment(experimentId: string): Mounted {
  window.history.replaceState({}, "", `/?experiment=${experimentId}`);
  return mount(<Experiment />);
}

/** Flushes the fetch -> res.json() -> setState microtask chain. */
async function settle(): Promise<void> {
  await act(async () => {
    for (let n = 0; n < 10; n += 1) await Promise.resolve();
  });
}

beforeEach(() => {
  // recharts' ResponsiveContainer constructs one of these unconditionally on
  // mount, and jsdom does not implement it.
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
});

describe("formatMetric", () => {
  it("formats a finite number to the requested precision", () => {
    expect(formatMetric(0.48231, 3)).toBe("0.482");
    expect(formatMetric(0.0134, 4)).toBe("0.0134");
  });

  it("formats an exact zero as a real figure, not as absent", () => {
    expect(formatMetric(0, 3)).toBe("0.000");
    expect(formatMetric(0, 3)).not.toBe(NOT_APPLICABLE);
  });

  it("treats null, undefined and NaN as not applicable, never as 0", () => {
    expect(formatMetric(null as unknown as number, 3)).toBe(NOT_APPLICABLE);
    expect(formatMetric(undefined as unknown as number, 3)).toBe(NOT_APPLICABLE);
    expect(formatMetric(Number.NaN, 3)).toBe(NOT_APPLICABLE);
  });
});

describe("headline metrics", () => {
  it("renders both Attention Spearman and Purchase-share MAE as real figures", async () => {
    stubFetch(BASE_RESPONSE);
    const view = renderExperiment(BASE_RESPONSE.experiment_id);
    await settle();

    const spearman = find(view.container, "experiment-metric-attention-spearman");
    expect(spearman.textContent).toBe("0.482");
    expect(spearman.dataset.absent).toBe("false");

    const mae = find(view.container, "experiment-metric-purchase-share-mae");
    expect(mae.textContent).toBe("0.0134");
    expect(mae.dataset.absent).toBe("false");

    view.unmount();
  });

  it("treats a computed zero Spearman as a real result, never as absent", async () => {
    stubFetch({ ...BASE_RESPONSE, attention_spearman: 0 });
    const view = renderExperiment(BASE_RESPONSE.experiment_id);
    await settle();

    const spearman = find(view.container, "experiment-metric-attention-spearman");
    expect(spearman.textContent).toBe("0.000");
    expect(spearman.dataset.absent).toBe("false");

    view.unmount();
  });

  it("renders a metric missing from the response as absent, never as a fabricated 0", async () => {
    const { attention_spearman, ...withoutSpearman } = BASE_RESPONSE;
    stubFetch(withoutSpearman);
    const view = renderExperiment(BASE_RESPONSE.experiment_id);
    await settle();

    const spearman = find(view.container, "experiment-metric-attention-spearman");
    expect(spearman.textContent).toBe(NOT_APPLICABLE);
    expect(spearman.textContent).not.toContain("0");
    expect(spearman.dataset.absent).toBe("true");

    // One absent metric does not take the rest of the page down with it.
    const mae = find(view.container, "experiment-metric-purchase-share-mae");
    expect(mae.textContent).toBe("0.0134");
    expect(mae.dataset.absent).toBe("false");

    view.unmount();
  });
});

describe("real vs synthetic legend", () => {
  it("colours the real and synthetic legend distinctly, matching the shared palette", async () => {
    stubFetch(BASE_RESPONSE);
    const view = renderExperiment(BASE_RESPONSE.experiment_id);
    await settle();

    const real = find(view.container, "experiment-legend-real");
    const synth = find(view.container, "experiment-legend-synth");

    expect(real.style.color).toBe(hexToRgb(REAL));
    expect(synth.style.color).toBe(hexToRgb(SYNTH));
    expect(real.style.color).not.toBe(synth.style.color);

    view.unmount();
  });
});

describe("opaque identifiers", () => {
  it("sets the experiment id and session id in the monospace stack", async () => {
    stubFetch(BASE_RESPONSE);
    const view = renderExperiment(BASE_RESPONSE.experiment_id);
    await settle();

    const id = find(view.container, "experiment-id");
    const session = find(view.container, "experiment-session-id");

    expect(id.style.fontFamily).toBe(mono.fontFamily);
    expect(session.style.fontFamily).toBe(mono.fontFamily);
    expect(id.textContent).toBe(BASE_RESPONSE.experiment_id);
    expect(session.textContent).toBe(BASE_RESPONSE.session_id);

    view.unmount();
  });
});
