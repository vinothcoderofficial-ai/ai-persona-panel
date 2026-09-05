import { describe, expect, it } from "vitest";
import {
  FAKE_PREDICTION_ID,
  FAKE_SESSION_ID,
  MEANINGFUL_MIN_EVIDENCE,
  parseLiveUpdate,
} from "@/spectator/liveMessage";

/**
 * The SPEC 4.7 example, plus the four count fields `api/app/live.py` emits.
 *
 * SPEC 4.7's own example predates the fix to a meter that could never turn on
 * for a cursor_only session — the only kind the demo produces. `n_fixations`
 * still means what it always meant; `evidence_count`/`evidence_kind` name the
 * count the 15 threshold is actually applied to.
 */
const SPEC_4_7 = {
  session_id: "11111111-2222-3333-4444-555555555555",
  t_ms: 41200,
  n_fixations: 37,
  n_cursor_dwells: 12,
  evidence_count: 37,
  evidence_kind: "fixations",
  stations_visited: 2,
  attention: { B1S3P1: 0.11 },
  latest_gaze: { x: 812, y: 344 },
  spearman: 0.58,
  meaningful: true,
  prediction_id: "66666666-7777-8888-9999-000000000000",
};

describe("SPEC 4.7 live update", () => {
  it("parses the message the server documents", () => {
    const update = parseLiveUpdate(JSON.stringify(SPEC_4_7));
    expect(update).not.toBeNull();
    expect(update).toEqual({ ...SPEC_4_7, fake: false });
  });

  it("agrees with api/app/live.py on the 15-unit threshold", () => {
    expect(MEANINGFUL_MIN_EVIDENCE).toBe(15);
  });

  it("carries a cursor_only frame's evidence through unchanged", () => {
    const update = parseLiveUpdate(
      JSON.stringify({
        ...SPEC_4_7,
        n_fixations: 0,
        n_cursor_dwells: 22,
        evidence_count: 22,
        evidence_kind: "cursor_dwells",
      }),
    );
    expect(update?.evidence_kind).toBe("cursor_dwells");
    expect(update?.evidence_count).toBe(22);
    expect(update?.n_fixations).toBe(0);
  });

  it("rejects a frame whose evidence fields are missing or unrecognised", () => {
    // A meter that renders "0 of 15" because a field was absent is exactly the
    // silent hole this parser exists to refuse.
    for (const bad of [
      { ...SPEC_4_7, evidence_count: undefined },
      { ...SPEC_4_7, evidence_kind: undefined },
      { ...SPEC_4_7, n_cursor_dwells: undefined },
      { ...SPEC_4_7, evidence_kind: "eyeballs" },
    ]) {
      expect(parseLiveUpdate(JSON.stringify(bad))).toBeNull();
    }
  });

  it("carries latest_gaze: null through rather than inventing a position", () => {
    const update = parseLiveUpdate(
      JSON.stringify({ ...SPEC_4_7, latest_gaze: null, n_fixations: 0, meaningful: false }),
    );
    expect(update?.latest_gaze).toBeNull();
  });

  it("marks a frame the server flagged with fake: true", () => {
    const update = parseLiveUpdate(JSON.stringify({ ...SPEC_4_7, fake: true }));
    expect(update?.fake).toBe(true);
  });

  it("marks the fake constants even if the fake flag were ever dropped", () => {
    // ws.py stamps three independent marks on every synthetic frame. Any one of
    // them is enough for this UI to refuse to present it as a measurement.
    expect(parseLiveUpdate(JSON.stringify({ ...SPEC_4_7, session_id: FAKE_SESSION_ID }))?.fake)
      .toBe(true);
    expect(
      parseLiveUpdate(JSON.stringify({ ...SPEC_4_7, prediction_id: FAKE_PREDICTION_ID }))?.fake,
    ).toBe(true);
  });

  it("rejects anything that is not a 4.7 message instead of half-rendering it", () => {
    expect(parseLiveUpdate("not json")).toBeNull();
    expect(parseLiveUpdate("[1,2,3]")).toBeNull();
    expect(parseLiveUpdate(JSON.stringify({ ...SPEC_4_7, attention: undefined }))).toBeNull();
    expect(parseLiveUpdate(JSON.stringify({ ...SPEC_4_7, n_fixations: "37" }))).toBeNull();
    expect(parseLiveUpdate(JSON.stringify({ error: "batch is not valid JSON" }))).toBeNull();
  });

  it("keeps only numeric attention entries", () => {
    const update = parseLiveUpdate(
      JSON.stringify({
        ...SPEC_4_7,
        attention: { B1S3P1: 0.11, B1S3P2: "x", B1S3P3: 0.4 },
      }),
    );
    expect(update?.attention).toEqual({ B1S3P1: 0.11, B1S3P3: 0.4 });
  });

  it("reports an unusable spearman as null rather than as zero agreement", () => {
    const update = parseLiveUpdate(JSON.stringify({ ...SPEC_4_7, spearman: null }));
    expect(update?.spearman).toBeNull();
  });
});
