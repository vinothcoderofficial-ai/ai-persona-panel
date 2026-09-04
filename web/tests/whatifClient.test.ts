import { describe, expect, it } from "vitest";
import { WHATIF_PATH, postWhatIf, type FetchLike } from "@/whatif/client";
import type { WhatIfRequestBody } from "@/whatif/patches";

/**
 * A 400, 404 or 422 from POST /whatif says exactly what was wrong with the
 * request - "move_sku: unknown to_slot_id 'B9S9P9'" - and that sentence is the
 * only thing that tells an operator which control they just broke. It has to
 * reach the screen, not be swallowed into a generic failure.
 */

const BODY: WhatIfRequestBody = {
  base_planogram_id: "demo_aisle",
  patches: [],
  n_synth: 10_000,
  seed: 42,
};

const OK_RESPONSE = {
  sim_run_id: "wi_abc",
  elapsed_ms: 9,
  per_persona: {},
  population_fixation_prob: { B1S3P1: 0.038 },
  lift_vs_baseline: {},
  ad_slot_attention: {},
};

interface Call {
  url: string;
  init: { method: string; headers: Record<string, string>; body: string };
}

function stub(
  response: { ok: boolean; status: number; statusText: string; body: string },
  calls: Call[] = [],
): FetchLike {
  return (url, init) => {
    calls.push({ url, init });
    return Promise.resolve({
      ok: response.ok,
      status: response.status,
      statusText: response.statusText,
      text: () => Promise.resolve(response.body),
    });
  };
}

describe("postWhatIf", () => {
  it("posts the body as JSON to the what-if endpoint", async () => {
    const calls: Call[] = [];
    const fetchImpl = stub(
      { ok: true, status: 200, statusText: "OK", body: JSON.stringify(OK_RESPONSE) },
      calls,
    );

    await postWhatIf(BODY, fetchImpl);

    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe(`/api${WHATIF_PATH}`);
    expect(calls[0].init.method).toBe("POST");
    expect(calls[0].init.headers["Content-Type"]).toBe("application/json");
    expect(JSON.parse(calls[0].init.body)).toEqual(BODY);
  });

  it("returns the parsed response", async () => {
    const result = await postWhatIf(
      BODY,
      stub({ ok: true, status: 200, statusText: "OK", body: JSON.stringify(OK_RESPONSE) }),
    );
    expect(result).toEqual(OK_RESPONSE);
  });

  it("surfaces a 400's detail message", async () => {
    const detail = "move_sku: unknown to_slot_id 'B9S9P9'";
    const failure = postWhatIf(
      BODY,
      stub({
        ok: false,
        status: 400,
        statusText: "Bad Request",
        body: JSON.stringify({ detail }),
      }),
    );
    await expect(failure).rejects.toThrow(detail);
    await expect(failure).rejects.toMatchObject({ status: 400, detail });
  });

  it("surfaces a 404's detail message", async () => {
    const detail = "unknown base_planogram_id 'nope'";
    await expect(
      postWhatIf(
        BODY,
        stub({ ok: false, status: 404, statusText: "Not Found", body: JSON.stringify({ detail }) }),
      ),
    ).rejects.toThrow(detail);
  });

  it("reads a 422's structured detail rather than printing [object Object]", async () => {
    const body = JSON.stringify({
      detail: [{ loc: ["body", "n_synth"], msg: "Input should be less than or equal to 50000" }],
    });
    const failure = postWhatIf(
      BODY,
      stub({ ok: false, status: 422, statusText: "Unprocessable Entity", body }),
    );
    await expect(failure).rejects.toThrow(/less than or equal to 50000/);
    await expect(failure).rejects.not.toThrow(/\[object Object\]/);
  });

  it("falls back to the raw body when the failure is not JSON at all", async () => {
    await expect(
      postWhatIf(
        BODY,
        stub({ ok: false, status: 502, statusText: "Bad Gateway", body: "upstream is down" }),
      ),
    ).rejects.toThrow(/upstream is down/);
  });

  it("still names the status when the failure body is empty", async () => {
    await expect(
      postWhatIf(BODY, stub({ ok: false, status: 500, statusText: "Server Error", body: "" })),
    ).rejects.toThrow(/500/);
  });
});
