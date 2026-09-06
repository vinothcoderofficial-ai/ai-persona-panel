import { describe, expect, it } from "vitest";
import {
  AiRequestError,
  getAiStatus,
  getPersonaPolicy,
  getPersonaTrace,
  regeneratePersonaPolicy,
  type FetchLike,
} from "@/ai/client";

/**
 * The four calls the AI panel makes, and the one thing that matters about all
 * of them: **a failure carries the server's own sentence**.
 *
 * `api/app/routers/ai.py` distinguishes 503 (never reached the model), 422 (it
 * answered and named a brand this store does not stock) and 502 (it never
 * produced schema-valid JSON), and each `detail` says which and what to do
 * about it. A client that collapsed those into "request failed" would throw
 * away the only part a person can act on - and this screen exists precisely so
 * that somebody can see why the AI is not answering.
 */

function respondWith(status: number, body: unknown): FetchLike {
  return async () =>
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    });
}

function recorder(body: unknown): { fetchImpl: FetchLike; calls: Array<[string, string]> } {
  const calls: Array<[string, string]> = [];
  const fetchImpl: FetchLike = async (input, init) => {
    calls.push([String(input), (init?.method ?? "GET").toUpperCase()]);
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  return { fetchImpl, calls };
}

describe("ai client paths", () => {
  it("reads status from GET /api/ai/status", async () => {
    const { fetchImpl, calls } = recorder({ provider: "ollama", personas: [] });

    const status = await getAiStatus(fetchImpl);

    expect(calls).toEqual([["/api/ai/status", "GET"]]);
    expect(status.provider).toBe("ollama");
  });

  it("reads a policy from GET /api/ai/personas/{id}/policy", async () => {
    const { fetchImpl, calls } = recorder({ persona_id: "mission", prompt: "x" });

    await getPersonaPolicy("mission", fetchImpl);

    expect(calls).toEqual([["/api/ai/personas/mission/policy", "GET"]]);
  });

  it("re-asks with POST, never GET", async () => {
    const { fetchImpl, calls } = recorder({ persona_id: "mission", changed_fields: [] });

    await regeneratePersonaPolicy("mission", fetchImpl);

    expect(calls).toEqual([["/api/ai/personas/mission/policy", "POST"]]);
  });

  it("reads a trace from GET /api/ai/personas/{id}/trace", async () => {
    const { fetchImpl, calls } = recorder({ persona_id: "mission", shoppers: [] });

    await getPersonaTrace("mission", fetchImpl);

    expect(calls).toEqual([["/api/ai/personas/mission/trace", "GET"]]);
  });

  it("percent-encodes a persona id rather than pasting it into the path", async () => {
    const { fetchImpl, calls } = recorder({});

    await getPersonaTrace("a/b", fetchImpl);

    expect(calls[0][0]).toBe("/api/ai/personas/a%2Fb/trace");
  });
});

describe("ai client failures", () => {
  it("throws the server's detail, not a generic message", async () => {
    const detail = "LLM_OFFLINE=1: complete_json will not contact the LLM.";

    await expect(
      regeneratePersonaPolicy("mission", respondWith(503, { detail })),
    ).rejects.toThrow(detail);
  });

  it("carries the status code, so the panel can tell 503 from 422", async () => {
    const error = await regeneratePersonaPolicy(
      "mission",
      respondWith(422, { detail: "names brand(s) not in planogram" }),
    ).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(AiRequestError);
    expect((error as AiRequestError).status).toBe(422);
    expect((error as AiRequestError).detail).toContain("not in planogram");
  });

  it("still reports something useful when the body is not JSON", async () => {
    const fetchImpl: FetchLike = async () =>
      new Response("<html>502 Bad Gateway</html>", { status: 502 });

    const error = await getAiStatus(fetchImpl).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(AiRequestError);
    expect((error as AiRequestError).status).toBe(502);
    expect((error as AiRequestError).message).toContain("502");
  });
});
