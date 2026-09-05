import { describe, expect, it, vi } from "vitest";
import {
  NO_LOCK,
  fetchLock,
  fetchPredictionLock,
  lockFromDocument,
  lockFromPredictionEndpoint,
  lockFromQuery,
  mergeLocks,
  resolveLock,
} from "@/spectator/lock";
import { DEFAULT_SCREEN, spectatorParamsFromQuery } from "@/spectator/SpectatorView";
// The merge this page's URL is read through. It used to be `spectatorQuery` in
// SpectatorView.tsx, beside a second copy in main.tsx that did not read the
// hash at all; `session/urlParams.ts` is now the only statement of the rule,
// and `web/tests/urlParams.test.ts` fails if a second one reappears.
import { mergedQueryString } from "@/session/urlParams";

const SHA = "a3f9c0d1e2b3a4958677665544332211aabbccddeeff00112233445566778899";

const LOCK_DOCUMENT = {
  prediction_id: "p-9",
  session_id: "s-1",
  variant_id: "B",
  sim_run_id: "r-1",
  created_at: "2026-09-14T10:32:07.412Z",
  population_fixation_prob: { B1S3P1: 0.038 },
  sha256: SHA,
  git_commit: "abc1234",
};

describe("mergedQueryString, which is how this page reads its URL", () => {
  it("reads params written before the hash, like the dashboard's", () => {
    expect(mergedQueryString("?session=s-1&fake=1", "#/spectator")).toBe(
      new URLSearchParams({ session: "s-1", fake: "1" }).toString(),
    );
  });

  it("also reads them written after it, which is how anyone types a hash route", () => {
    // `#/spectator?session=s-1` puts the query inside location.hash, where
    // location.search cannot see it. Both spellings have to work or the demo
    // operator loses a take to a blank screen.
    const params = new URLSearchParams(mergedQueryString("", "#/spectator?session=s-1&fake=1"));
    expect(params.get("session")).toBe("s-1");
    expect(params.get("fake")).toBe("1");
  });

  it("lets the hash win, because it is the part that names this route", () => {
    const params = new URLSearchParams(
      mergedQueryString("?session=before&ceiling=0.5", "#/spectator?session=after"),
    );
    expect(params.get("session")).toBe("after");
    expect(params.get("ceiling")).toBe("0.5");
  });

  it("copes with no query at all", () => {
    expect(mergedQueryString("", "#/spectator")).toBe("");
  });
});

describe("spectatorParamsFromQuery", () => {
  it("defaults to no session, no fake stream and no lock", () => {
    const params = spectatorParamsFromQuery("");
    expect(params.sessionId).toBeNull();
    expect(params.fake).toBe(false);
    expect(params.lock).toEqual(NO_LOCK);
    expect(params.lockUrl).toBeNull();
    expect(params.screen).toEqual(DEFAULT_SCREEN);
    expect(params.screenshotUrl).toBeNull();
    expect(params.ceiling).toBeNull();
  });

  it("reads every option the spectator URL accepts", () => {
    const params = spectatorParamsFromQuery(
      `?session=s-1&fake=1&sha256=${SHA}&locked_at=2026-09-14T10:32:07.412Z` +
        "&lock=/locks/s-1.json&screen_w=1920&screen_h=1080" +
        "&screenshot=/shots/b1.png&ceiling=0.72",
    );
    expect(params.sessionId).toBe("s-1");
    expect(params.fake).toBe(true);
    expect(params.lock.sha256_prefix).toBe("a3f9c0d1");
    expect(params.lockUrl).toBe("/locks/s-1.json");
    expect(params.screen).toEqual({ w: 1920, h: 1080 });
    expect(params.screenshotUrl).toBe("/shots/b1.png");
    expect(params.ceiling).toBe(0.72);
  });

  it("treats fake=0 as off, so the flag cannot be switched on by accident", () => {
    expect(spectatorParamsFromQuery("?session=s-1&fake=0").fake).toBe(false);
    expect(spectatorParamsFromQuery("?session=s-1&fake=false").fake).toBe(false);
    expect(spectatorParamsFromQuery("?session=s-1&fake").fake).toBe(true);
  });

  it("ignores a nonsense screen size rather than dividing by it", () => {
    const params = spectatorParamsFromQuery("?screen_w=0&screen_h=abc");
    expect(params.screen).toEqual(DEFAULT_SCREEN);
  });
});

describe("fetchLock", () => {
  it("reads a lock document from ?lock=<url>", async () => {
    const fetchImpl = vi.fn(async () => ({
      ok: true,
      json: async () => LOCK_DOCUMENT,
    })) as unknown as typeof fetch;

    const lock = await fetchLock("/locks/s-1.json", fetchImpl);
    expect(lock.sha256_prefix).toBe("a3f9c0d1");
    expect(lock.population_fixation_prob).toEqual({ B1S3P1: 0.038 });
    expect(lock.source).toBe("file");
  });

  it("reports no lock — never zeros — when the file is missing or unreadable", async () => {
    const missing = (async () => ({ ok: false, json: async () => ({}) })) as unknown as typeof fetch;
    expect(await fetchLock("/nope.json", missing)).toEqual(NO_LOCK);

    const broken = (async () => {
      throw new Error("network down");
    }) as unknown as typeof fetch;
    expect(await fetchLock("/nope.json", broken)).toEqual(NO_LOCK);
  });
});

describe("mergeLocks", () => {
  it("keeps the badge from the URL when no document was offered", () => {
    const fromQuery = lockFromQuery(`?sha256=${SHA}&locked_at=2026-09-14T10:32:07.412Z`);
    expect(mergeLocks(fromQuery, NO_LOCK)).toBe(fromQuery);
  });

  it("prefers the document, which is the lock itself", () => {
    const fromQuery = lockFromQuery("?sha256=00000000&locked_at=1999-01-01T00:00:00.000Z");
    const merged = mergeLocks(fromQuery, {
      prediction_id: "p-9",
      sha256_prefix: "a3f9c0d1",
      created_at: "2026-09-14T10:32:07.412Z",
      population_fixation_prob: { B1S3P1: 0.038 },
      source: "file",
    });
    expect(merged.sha256_prefix).toBe("a3f9c0d1");
    expect(merged.created_at).toBe("2026-09-14T10:32:07.412Z");
    expect(merged.population_fixation_prob).toEqual({ B1S3P1: 0.038 });
  });

  it("is NO_LOCK when neither source had anything", () => {
    expect(mergeLocks(NO_LOCK, NO_LOCK)).toEqual(NO_LOCK);
  });
});

// ---------------------------------------------------------------------------
// GET /sessions/{id}/prediction — the default lock source
// ---------------------------------------------------------------------------

/** Exactly what `api/app/routers/sessions.py:get_session_prediction` returns. */
const PREDICTION_RESPONSE = {
  prediction_id: "f2493990-4a5d-479d-902c-eaeb8d91680d",
  sim_run_id: "run-1",
  created_at: "2026-09-04T22:19:33.086Z",
  sha256_prefix: "f3ded23e",
  population_fixation_prob: { B1S3P1: 0.038, B1S3P2: 0.021 },
};

describe("lockFromPredictionEndpoint", () => {
  it("reads the endpoint's already-truncated sha256_prefix", () => {
    const lock = lockFromPredictionEndpoint(PREDICTION_RESPONSE);
    expect(lock.sha256_prefix).toBe("f3ded23e");
    expect(lock.created_at).toBe("2026-09-04T22:19:33.086Z");
    expect(lock.prediction_id).toBe("f2493990-4a5d-479d-902c-eaeb8d91680d");
    expect(lock.population_fixation_prob).toEqual({ B1S3P1: 0.038, B1S3P2: 0.021 });
    expect(lock.source).toBe("api");
  });

  it("refuses a body that is not this endpoint's", () => {
    expect(lockFromPredictionEndpoint({ detail: "no prediction lock" })).toEqual(NO_LOCK);
    expect(lockFromPredictionEndpoint({ ...PREDICTION_RESPONSE, sha256_prefix: "nope" }))
      .toEqual(NO_LOCK);
    expect(lockFromPredictionEndpoint(null)).toEqual(NO_LOCK);
  });
});

describe("fetchPredictionLock", () => {
  it("GETs the session's lock through the api prefix the dev proxy strips", async () => {
    const calls: string[] = [];
    const fetchImpl = (async (url: string) => {
      calls.push(url);
      return { ok: true, status: 200, json: async () => PREDICTION_RESPONSE };
    }) as unknown as typeof fetch;

    const lock = await fetchPredictionLock("sess 1", fetchImpl);
    expect(calls).toEqual(["/api/sessions/sess%201/prediction"]);
    expect(lock.sha256_prefix).toBe("f3ded23e");
  });

  it("is NO_LOCK on a 404 — the honest 'no lock' message, never zeros", async () => {
    const notFound = (async () => ({
      ok: false,
      status: 404,
      json: async () => ({ detail: "no prediction lock" }),
    })) as unknown as typeof fetch;
    expect(await fetchPredictionLock("s-1", notFound)).toEqual(NO_LOCK);
  });

  it("is NO_LOCK when the API is not running at all", async () => {
    const down = (async () => {
      throw new TypeError("Failed to fetch");
    }) as unknown as typeof fetch;
    expect(await fetchPredictionLock("s-1", down)).toEqual(NO_LOCK);
  });
});

describe("resolveLock precedence", () => {
  const fetched = lockFromPredictionEndpoint(PREDICTION_RESPONSE);
  const typed = lockFromQuery(`?sha256=${SHA}&locked_at=2026-09-14T10:32:07.412Z`);
  const document = lockFromDocument(LOCK_DOCUMENT);

  it("falls back to the fetched lock when nothing was typed", () => {
    expect(resolveLock([fetched, NO_LOCK, NO_LOCK])).toEqual(fetched);
  });

  it("lets hand-typed badge fields override the fetched ones", () => {
    const lock = resolveLock([fetched, typed, NO_LOCK]);
    expect(lock.sha256_prefix).toBe("a3f9c0d1");
    expect(lock.created_at).toBe("2026-09-14T10:32:07.412Z");
  });

  it("keeps the fetched vector when the override supplied only a badge", () => {
    // Overriding the badge must not blank the heatmap column beside it.
    const lock = resolveLock([fetched, typed, NO_LOCK]);
    expect(lock.population_fixation_prob).toEqual({ B1S3P1: 0.038, B1S3P2: 0.021 });
  });

  it("lets an explicitly named lock document beat both", () => {
    const lock = resolveLock([fetched, typed, document]);
    expect(lock.sha256_prefix).toBe("a3f9c0d1");
    expect(lock.population_fixation_prob).toEqual({ B1S3P1: 0.038 });
    expect(lock.source).toBe("file");
  });

  it("is NO_LOCK when every source came up empty", () => {
    expect(resolveLock([NO_LOCK, NO_LOCK, NO_LOCK])).toEqual(NO_LOCK);
  });
});
