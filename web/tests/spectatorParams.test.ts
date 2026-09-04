import { describe, expect, it, vi } from "vitest";
import { NO_LOCK, fetchLock, mergeLocks, lockFromQuery } from "@/spectator/lock";
import {
  DEFAULT_SCREEN,
  spectatorParamsFromQuery,
  spectatorQuery,
} from "@/spectator/SpectatorView";

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

describe("spectatorQuery", () => {
  it("reads params written before the hash, like the dashboard's", () => {
    expect(spectatorQuery("?session=s-1&fake=1", "#/spectator")).toBe(
      new URLSearchParams({ session: "s-1", fake: "1" }).toString(),
    );
  });

  it("also reads them written after it, which is how anyone types a hash route", () => {
    // `#/spectator?session=s-1` puts the query inside location.hash, where
    // location.search cannot see it. Both spellings have to work or the demo
    // operator loses a take to a blank screen.
    const params = new URLSearchParams(spectatorQuery("", "#/spectator?session=s-1&fake=1"));
    expect(params.get("session")).toBe("s-1");
    expect(params.get("fake")).toBe("1");
  });

  it("lets the hash win, because it is the part that names this route", () => {
    const params = new URLSearchParams(
      spectatorQuery("?session=before&ceiling=0.5", "#/spectator?session=after"),
    );
    expect(params.get("session")).toBe("after");
    expect(params.get("ceiling")).toBe("0.5");
  });

  it("copes with no query at all", () => {
    expect(spectatorQuery("", "#/spectator")).toBe("");
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
