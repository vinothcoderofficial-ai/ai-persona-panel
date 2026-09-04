import { describe, expect, it } from "vitest";
import type { ReactNode } from "react";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { HeatmapDiff } from "@/whatif/HeatmapDiff";
import { LiftBars } from "@/whatif/LiftBars";
import { WhatIfControls } from "@/whatif/WhatIfControls";
import { NOT_APPLICABLE, type PersonaLiftRow } from "@/whatif/lift";
import { CLEAR_CREATIVE, EMPTY_SELECTION, type WhatIfSelection } from "@/whatif/patches";
import { chooseOption, demoAisle } from "./whatifFixture";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

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

function text(container: HTMLElement, testId: string): string {
  return (find(container, testId).textContent ?? "").trim();
}

// ---------------------------------------------------------------------------
// HeatmapDiff
// ---------------------------------------------------------------------------

/** A frame scheduler the test drives by hand, so no clock is ever waited on. */
function manualFrames() {
  let pending: (() => void) | null = null;
  let clock = 0;
  return {
    requestFrame: (cb: () => void) => {
      pending = cb;
      return () => {
        pending = null;
      };
    },
    now: () => clock,
    frames: () => (pending === null ? 0 : 1),
    advance(ms: number) {
      clock += ms;
      const due = pending;
      pending = null;
      if (due !== null) act(() => due());
    },
  };
}

describe("HeatmapDiff", () => {
  const previous = { B1S3P2: 0.01, B1S5P1: 0.05 };
  const next = { B1S3P2: 0.09, B1S5P1: 0.02 };

  it("shows the new numbers with the animation switched off entirely", () => {
    // PLAN section 9 drops this animation first and keeps the number, so the
    // number has to be right with nothing driving it.
    const view = mount(<HeatmapDiff previous={previous} next={next} durationMs={0} />);
    expect(find(view.container, "heat-diff-row-B1S3P2").dataset.value).toBe("0.09");
    expect(text(view.container, "heat-diff-value-B1S3P2")).toBe("0.090");
    expect(find(view.container, "heat-diff-row-B1S3P2").dataset.frame).toBe("0.09");
    view.unmount();
  });

  it("jumps straight to the final frame when the viewer asked for reduced motion", () => {
    const driver = manualFrames();
    const view = mount(
      <HeatmapDiff
        previous={previous}
        next={next}
        reducedMotion
        requestFrame={driver.requestFrame}
        now={driver.now}
      />,
    );
    expect(find(view.container, "heat-diff-row-B1S3P2").dataset.frame).toBe("0.09");
    expect(driver.frames()).toBe(0);
    view.unmount();
  });

  it("sweeps from the previous vector to the new one over the animation window", () => {
    const driver = manualFrames();
    const view = mount(
      <HeatmapDiff
        previous={previous}
        next={next}
        durationMs={600}
        reducedMotion={false}
        requestFrame={driver.requestFrame}
        now={driver.now}
      />,
    );
    const row = () => find(view.container, "heat-diff-row-B1S3P2");

    expect(row().dataset.frame).toBe("0.01");
    driver.advance(300);
    expect(Number(row().dataset.frame)).toBeCloseTo(0.05, 10);
    driver.advance(300);
    expect(Number(row().dataset.frame)).toBeCloseTo(0.09, 10);
    expect(driver.frames()).toBe(0);
    view.unmount();
  });

  it("shows the final figure from the first frame - the sweep is the bar, not the number", () => {
    const driver = manualFrames();
    const view = mount(
      <HeatmapDiff
        previous={previous}
        next={next}
        durationMs={600}
        reducedMotion={false}
        requestFrame={driver.requestFrame}
        now={driver.now}
      />,
    );
    expect(text(view.container, "heat-diff-value-B1S3P2")).toBe("0.090");
    expect(text(view.container, "heat-diff-delta-B1S3P2")).toBe("+0.080");
    view.unmount();
  });

  it("says a slot is new rather than claiming it rose from zero", () => {
    const view = mount(<HeatmapDiff previous={{}} next={{ B2S3P2: 0.04 }} durationMs={0} />);
    expect(text(view.container, "heat-diff-delta-B2S3P2")).toBe("new");
    view.unmount();
  });
});

// ---------------------------------------------------------------------------
// LiftBars
// ---------------------------------------------------------------------------

const ROWS: PersonaLiftRow[] = [
  { personaId: "browser", baseline: 0.02, patched: 0.03, lift: 0.5 },
  { personaId: "mission", baseline: 0, patched: 0.01, lift: null },
];

describe("LiftBars", () => {
  it("renders an empty lift_vs_baseline as not applicable, never as 0%", () => {
    const view = mount(<LiftBars lift={{}} rows={[]} focalSkuId={null} />);
    for (const key of ["focal_sku_attention", "focal_sku_purchase_share"]) {
      const shown = text(view.container, `whatif-lift-${key}`);
      expect(shown).toBe(NOT_APPLICABLE);
      expect(shown).not.toContain("0%");
      expect(shown).not.toContain("%");
    }
    view.unmount();
  });

  it("renders a null key as not applicable while a real number beside it is shown", () => {
    const view = mount(
      <LiftBars
        lift={{ focal_sku_attention: null, focal_sku_purchase_share: 1.147 }}
        rows={[]}
        focalSkuId="SKU_008"
      />,
    );
    const undefinedLift = text(view.container, "whatif-lift-focal_sku_attention");
    expect(undefinedLift).toBe(NOT_APPLICABLE);
    expect(undefinedLift).not.toContain("0%");
    expect(text(view.container, "whatif-lift-focal_sku_purchase_share")).toBe("+114.7%");
    view.unmount();
  });

  it("says why a lift could not be computed, so an operator is not left guessing", () => {
    const view = mount(<LiftBars lift={{}} rows={[]} focalSkuId={null} />);
    expect(
      find(view.container, "whatif-lift-explainer-focal_sku_attention").textContent,
    ).toMatch(/focal SKU/i);
    view.unmount();
  });

  it("draws one bar per persona whose lift is defined", () => {
    const view = mount(<LiftBars lift={{}} rows={ROWS} focalSkuId="SKU_008" />);
    expect(text(view.container, "whatif-persona-lift-browser")).toBe("+50.0%");
    expect(find(view.container, "whatif-persona-lift-browser").dataset.lift).toBe("0.5");
    view.unmount();
  });

  it("gives a persona with a zero baseline the words, not a zero bar", () => {
    const view = mount(<LiftBars lift={{}} rows={ROWS} focalSkuId="SKU_008" />);
    const mission = find(view.container, "whatif-persona-lift-mission");
    expect((mission.textContent ?? "").trim()).toBe(NOT_APPLICABLE);
    expect(mission.textContent).not.toContain("%");
    expect(mission.dataset.lift).toBe("");
    view.unmount();
  });
});

// ---------------------------------------------------------------------------
// WhatIfControls
// ---------------------------------------------------------------------------

function options(container: HTMLElement, testId: string): string[] {
  return [...find(container, testId).querySelectorAll("option")].map((option) => option.value);
}

describe("WhatIfControls", () => {
  const planogram = demoAisle();

  function controls(selection: WhatIfSelection = EMPTY_SELECTION) {
    const changes: WhatIfSelection[] = [];
    const view = mount(
      <WhatIfControls
        planogram={planogram}
        selection={selection}
        onChange={(next) => changes.push(next)}
      />,
    );
    return { ...view, changes };
  }

  it("offers every SKU in the planogram, plus a no-focal-SKU option", () => {
    const view = controls();
    const values = options(view.container, "whatif-focal-sku");
    expect(values[0]).toBe("");
    expect(values).toHaveLength(25);
    expect(values).toContain("SKU_008");
    view.unmount();
  });

  it("offers the five shelf levels and a leave-it-alone option", () => {
    const view = controls();
    expect(options(view.container, "whatif-shelf-level")).toEqual([
      "",
      "top",
      "above_eye",
      "eye",
      "below_eye",
      "bottom",
    ]);
    view.unmount();
  });

  it("offers every ad slot and every creative, plus an explicit clear", () => {
    const view = controls();
    expect(options(view.container, "whatif-ad-slot")).toEqual([
      "",
      "B1_TALKER",
      "B2_DECAL",
      "B3_ENDCAP",
    ]);
    expect(options(view.container, "whatif-creative")).toEqual([
      "",
      CLEAR_CREATIVE,
      "AD_1",
      "AD_2",
    ]);
    view.unmount();
  });

  it("cannot move or re-price a SKU until one is chosen", () => {
    const view = controls();
    expect((find(view.container, "whatif-shelf-level") as HTMLSelectElement).disabled).toBe(true);
    expect((find(view.container, "whatif-promo") as HTMLSelectElement).disabled).toBe(true);
    view.unmount();

    const chosen = controls({ ...EMPTY_SELECTION, focalSkuId: "SKU_008" });
    expect((find(chosen.container, "whatif-shelf-level") as HTMLSelectElement).disabled).toBe(
      false,
    );
    chosen.unmount();
  });

  it("reports each change as a whole new selection", () => {
    const view = controls();
    chooseOption(find(view.container, "whatif-focal-sku") as HTMLSelectElement, "SKU_008");
    expect(view.changes).toEqual([{ ...EMPTY_SELECTION, focalSkuId: "SKU_008" }]);
    view.unmount();
  });
});
