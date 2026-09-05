import { afterEach, describe, expect, it } from "vitest";
import { act } from "react";
import type { ReactElement } from "react";
import { createRoot } from "react-dom/client";
import { AgreementMeter, relativeAgreement } from "@/spectator/AgreementMeter";
import { ClockOverlay, wallClockTime } from "@/spectator/ClockOverlay";
import { GazeTrail } from "@/spectator/GazeTrail";
import { LiveHeatmap } from "@/spectator/LiveHeatmap";
import { PredictionBadge } from "@/spectator/PredictionBadge";
import { NO_LOCK, hashPrefix, lockFromDocument, lockFromQuery } from "@/spectator/lock";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

interface Mounted {
  container: HTMLDivElement;
  unmount: () => void;
}

function mount(ui: ReactElement): Mounted {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(ui);
  });
  return {
    container,
    unmount: () => {
      act(() => {
        root.unmount();
      });
      container.remove();
    },
  };
}

function find(container: HTMLElement, testId: string): HTMLElement {
  const element = container.querySelector<HTMLElement>(`[data-testid="${testId}"]`);
  if (element === null) throw new Error(`no element with data-testid="${testId}"`);
  return element;
}

function has(container: HTMLElement, testId: string): boolean {
  return container.querySelector(`[data-testid="${testId}"]`) !== null;
}

function text(container: HTMLElement): string {
  return container.textContent ?? "";
}

afterEach(() => {
  document.body.innerHTML = "";
});

// ---------------------------------------------------------------------------
// AgreementMeter — grey until `meaningful`
// ---------------------------------------------------------------------------

const SHA = "a3f9c0d1e2b3a4958677665544332211aabbccddeeff00112233445566778899";

describe("AgreementMeter", () => {
  it("is grey and reads 'warming up' below the 15-unit threshold", () => {
    // evidence_count = 14: api/app/live.py sets meaningful = false here.
    const view = mount(
      <AgreementMeter
        spearman={0.91}
        meaningful={false}
        evidenceCount={14}
        evidenceKind="fixations"
      />,
    );
    const meter = find(view.container, "agreement-meter");

    expect(meter.dataset.state).toBe("warming_up");
    expect(text(meter).toLowerCase()).toContain("warming up");
    // The whole point: an early, spurious rho must not be on screen at all.
    expect(has(view.container, "agreement-rho")).toBe(false);
    expect(text(meter)).not.toContain("0.91");
    expect(text(meter)).toContain("14");
    expect(text(meter)).toContain("fixations");

    view.unmount();
  });

  it("labels a cursor_only session's evidence as cursor dwells, never fixations", () => {
    // The mislabelling guard: in cursor_only mode the count is cursor dwells,
    // and the screen must not call them something they are not.
    const view = mount(
      <AgreementMeter
        spearman={0.91}
        meaningful={false}
        evidenceCount={9}
        evidenceKind="cursor_dwells"
      />,
    );
    const meter = find(view.container, "agreement-meter");

    expect(text(meter)).toContain("cursor dwells");
    expect(text(meter)).not.toContain("fixations");
    expect(text(meter)).toContain("9");

    view.unmount();
  });

  it("says it is waiting rather than naming a kind it has not been told", () => {
    const view = mount(
      <AgreementMeter
        spearman={null}
        meaningful={false}
        evidenceCount={0}
        evidenceKind={null}
      />,
    );
    const meter = find(view.container, "agreement-meter");

    expect(meter.dataset.state).toBe("warming_up");
    expect(text(meter)).not.toContain("fixations");
    expect(text(meter)).not.toContain("cursor dwells");

    view.unmount();
  });

  it("shows rho once the server says the session is meaningful", () => {
    // evidence_count = 15: the boundary, and the first message live.py flags true.
    const view = mount(
      <AgreementMeter
        spearman={0.58}
        meaningful={true}
        evidenceCount={15}
        evidenceKind="cursor_dwells"
      />,
    );
    const meter = find(view.container, "agreement-meter");

    expect(meter.dataset.state).toBe("meaningful");
    expect(text(find(view.container, "agreement-rho"))).toContain("0.58");
    expect(text(meter).toLowerCase()).not.toContain("warming up");
    expect(text(meter)).toContain("cursor dwells");

    view.unmount();
  });

  it("says so rather than showing a number when the server sent no rho", () => {
    const view = mount(
      <AgreementMeter
        spearman={null}
        meaningful={true}
        evidenceCount={40}
        evidenceKind="fixations"
      />,
    );
    expect(has(view.container, "agreement-rho")).toBe(false);
    expect(text(view.container).toLowerCase()).toContain("no agreement figure");
    view.unmount();
  });

  it("shows relative-to-ceiling only when a ceiling was supplied", () => {
    const without = mount(
      <AgreementMeter
        spearman={0.58}
        meaningful={true}
        evidenceCount={20}
        evidenceKind="fixations"
      />,
    );
    expect(has(without.container, "agreement-relative")).toBe(false);
    without.unmount();

    const withCeiling = mount(
      <AgreementMeter
        spearman={0.58}
        meaningful={true}
        evidenceCount={20}
        evidenceKind="fixations"
        ceiling={0.72}
      />,
    );
    expect(text(find(withCeiling.container, "agreement-relative"))).toContain("0.81");
    withCeiling.unmount();
  });

  it("relativeAgreement is min(1, rho / ceiling), as docs/PLAN.md S17 defines it", () => {
    expect(relativeAgreement(0.5, 0.7)).toBeCloseTo(0.714_285_7, 6);
    expect(relativeAgreement(0.9, 0.7)).toBe(1);
    expect(relativeAgreement(0.7, 0.7)).toBe(1);
    expect(relativeAgreement(null, 0.7)).toBeNull();
    expect(relativeAgreement(0.5, null)).toBeNull();
    expect(relativeAgreement(0.5, 0)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// PredictionBadge — the on-camera evidence that the lock predates the shopping
// ---------------------------------------------------------------------------

describe("PredictionBadge", () => {
  it("shows the first 8 hex characters of the lock hash and its created_at", () => {
    const lock = lockFromQuery(`?sha256=${SHA}&locked_at=2026-09-14T10:32:07.412Z`);
    const view = mount(<PredictionBadge lock={lock} fake={false} />);

    expect(text(find(view.container, "prediction-hash"))).toBe("a3f9c0d1");
    expect(text(find(view.container, "prediction-created-at"))).toContain(
      "2026-09-14T10:32:07.412Z",
    );
    // The demo sentence is "prediction locked at 10:32:07" — the wall-clock
    // form has to be readable on video, not buried in an ISO string.
    expect(text(find(view.container, "prediction-locked-time"))).toBe(
      wallClockTime(new Date("2026-09-14T10:32:07.412Z")),
    );

    view.unmount();
  });

  it("says the lock was not supplied rather than showing a blank badge", () => {
    const view = mount(<PredictionBadge lock={NO_LOCK} fake={false} />);
    expect(has(view.container, "prediction-hash")).toBe(false);
    expect(text(find(view.container, "prediction-lock-missing")).length).toBeGreaterThan(0);
    view.unmount();
  });

  it("refuses to present a fake stream's badge as evidence", () => {
    const view = mount(<PredictionBadge lock={NO_LOCK} fake={true} />);
    expect(text(view.container).toUpperCase()).toContain("FAKE");
    view.unmount();
  });
});

describe("hashPrefix", () => {
  it("takes the first 8 characters, lower-cased", () => {
    expect(hashPrefix(SHA)).toBe("a3f9c0d1");
    expect(hashPrefix(SHA.toUpperCase())).toBe("a3f9c0d1");
    expect(hashPrefix("a3f9c0d1")).toBe("a3f9c0d1");
  });

  it("returns null for anything that is not a hex digest", () => {
    expect(hashPrefix(null)).toBeNull();
    expect(hashPrefix("")).toBeNull();
    expect(hashPrefix("a3f9c0")).toBeNull();
    expect(hashPrefix("zzzzzzzz")).toBeNull();
    expect(hashPrefix(42)).toBeNull();
  });
});

describe("lock sources", () => {
  it("reads the badge straight off the spectator URL", () => {
    const lock = lockFromQuery(
      `?session=s-1&sha256=${SHA}&locked_at=2026-09-14T10:32:07.412Z&prediction=p-9`,
    );
    expect(lock.sha256_prefix).toBe("a3f9c0d1");
    expect(lock.created_at).toBe("2026-09-14T10:32:07.412Z");
    expect(lock.prediction_id).toBe("p-9");
    expect(lock.population_fixation_prob).toBeNull();
    expect(lock.source).toBe("query");
  });

  it("is NO_LOCK when the URL carries nothing", () => {
    expect(lockFromQuery("?session=s-1")).toEqual(NO_LOCK);
    expect(NO_LOCK.source).toBe("none");
  });

  it("reads a whole prediction.schema.json lock document", () => {
    const lock = lockFromDocument({
      prediction_id: "p-9",
      session_id: "s-1",
      variant_id: "B",
      sim_run_id: "r-1",
      created_at: "2026-09-14T10:32:07.412Z",
      population_fixation_prob: { B1S3P1: 0.038, B1S3P2: 0.02 },
      sha256: SHA,
      git_commit: "abc1234",
    });
    expect(lock.sha256_prefix).toBe("a3f9c0d1");
    expect(lock.population_fixation_prob).toEqual({ B1S3P1: 0.038, B1S3P2: 0.02 });
    expect(lock.source).toBe("file");
  });

  it("rejects a document that is not a lock rather than half-trusting it", () => {
    expect(lockFromDocument({ sha256: "nope" })).toEqual(NO_LOCK);
    expect(lockFromDocument(null)).toEqual(NO_LOCK);
  });
});

// ---------------------------------------------------------------------------
// LiveHeatmap — real attention BESIDE the locked prediction
// ---------------------------------------------------------------------------

describe("LiveHeatmap", () => {
  it("renders the real column beside the locked one", () => {
    const view = mount(
      <LiveHeatmap
        attention={{ B1S3P1: 0.4, B1S3P2: 0.1 }}
        locked={{ B1S3P1: 0.2, B1S3P2: 0.3 }}
      />,
    );

    expect(find(view.container, "heat-real-B1S3P1").dataset.value).toBe("0.4");
    expect(find(view.container, "heat-locked-B1S3P2").dataset.value).toBe("0.3");
    expect(has(view.container, "locked-unavailable")).toBe(false);

    view.unmount();
  });

  it("says the locked prediction is unavailable instead of drawing zeros", () => {
    const view = mount(<LiveHeatmap attention={{ B1S3P1: 0.4 }} locked={null} />);

    expect(has(view.container, "heat-real-B1S3P1")).toBe(true);
    expect(has(view.container, "heat-locked-B1S3P1")).toBe(false);
    expect(text(find(view.container, "locked-unavailable")).toLowerCase()).toContain(
      "locked prediction",
    );

    view.unmount();
  });

  it("covers every slot in either vector, in a stable order", () => {
    const view = mount(
      <LiveHeatmap attention={{ B2S1P1: 0.5 }} locked={{ B1S3P1: 0.5, B3S2P2: 0.1 }} />,
    );
    const rows = Array.from(
      view.container.querySelectorAll<HTMLElement>('[data-testid^="heat-row-"]'),
    ).map((row) => row.dataset.slotId);
    expect(rows).toEqual(["B1S3P1", "B2S1P1", "B3S2P2"]);
    view.unmount();
  });
});

// ---------------------------------------------------------------------------
// GazeTrail and ClockOverlay
// ---------------------------------------------------------------------------

describe("GazeTrail", () => {
  it("draws the newest sample as the dot and the rest as the fading trail", () => {
    const view = mount(
      <GazeTrail
        points={[
          { x: 0, y: 0, t: 9_000 },
          { x: 720, y: 450, t: 9_800 },
          { x: 1_440, y: 900, t: 10_000 },
        ]}
        now={10_000}
        screen={{ w: 1_440, h: 900 }}
      />,
    );

    const dot = find(view.container, "gaze-dot");
    expect(dot.style.left).toBe("100%");
    expect(dot.style.top).toBe("100%");
    expect(
      view.container.querySelectorAll('[data-testid="gaze-trail-point"]'),
    ).toHaveLength(2);

    view.unmount();
  });

  it("shows no dot at all once the trail has aged out", () => {
    const view = mount(
      <GazeTrail points={[{ x: 10, y: 10, t: 0 }]} now={9_000} screen={{ w: 100, h: 100 }} />,
    );
    expect(has(view.container, "gaze-dot")).toBe(false);
    expect(text(view.container).toLowerCase()).toContain("no live gaze");
    view.unmount();
  });
});

describe("ClockOverlay", () => {
  it("formats wall-clock time as HH:MM:SS", () => {
    const noon = new Date(2026, 8, 14, 10, 32, 7);
    expect(wallClockTime(noon)).toBe("10:32:07");
    expect(wallClockTime(new Date(2026, 8, 14, 0, 0, 0))).toBe("00:00:00");
  });

  it("renders the current wall-clock time", () => {
    const fixed = new Date(2026, 8, 14, 10, 32, 41);
    const view = mount(<ClockOverlay now={() => fixed} />);
    expect(text(find(view.container, "wall-clock"))).toBe("10:32:41");
    view.unmount();
  });
});
