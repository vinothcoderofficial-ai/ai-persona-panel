import { afterEach, describe, expect, it } from "vitest";
import {
  LAST_SESSION_KEY,
  readLastSession,
  rememberSession,
  type LastSession,
  type StorageLike,
} from "@/session/lastSession";

/**
 * The session id is generated in the browser by `crypto.randomUUID()` when the
 * store opens (main.tsx), and until now it existed nowhere an operator could
 * reach: the spectator screen on the second monitor needs
 * `?session=<uuid>` and there was no way to learn the uuid without reading a
 * network tab on camera. This module is the note the store leaves behind.
 *
 * Two rules it must never break, both of them honesty rules:
 *
 *   * `localStorage` throws outright in some privacy modes - Safari's private
 *     window historically threw on `setItem`, and a blocked-cookies Chrome
 *     throws on the property access itself. A store session must never fail
 *     because a convenience note could not be written.
 *   * A read that finds nothing, or finds something it does not recognise,
 *     returns `null`. It never invents a session id: a spectator screen
 *     pointed at a fabricated id would sit there saying CONNECTING while a
 *     real shopper was being measured.
 */

const ENTRY: LastSession = {
  session_id: "3f6b1c2e-9a44-4d0e-8c11-77a0b5e2d913",
  variant_id: "C",
  started_at: "2026-09-14T10:32:07.412Z",
};

/** A `localStorage` that works, kept in memory so tests cannot leak into each other. */
function workingStorage(seed: Record<string, string> = {}): StorageLike {
  const map = new Map(Object.entries(seed));
  return {
    getItem: (key) => map.get(key) ?? null,
    setItem: (key, value) => {
      map.set(key, value);
    },
  };
}

/** The privacy-mode storage: present, and throws on both halves. */
const throwingStorage: StorageLike = {
  getItem() {
    throw new DOMException("The operation is insecure.", "SecurityError");
  },
  setItem() {
    throw new DOMException("QuotaExceededError", "QuotaExceededError");
  },
};

afterEach(() => {
  window.localStorage.clear();
});

describe("remembering the session the store just opened", () => {
  it("reads back what was written", () => {
    const storage = workingStorage();
    rememberSession(ENTRY, storage);
    expect(readLastSession(storage)).toEqual(ENTRY);
  });

  it("round-trips through the browser's own localStorage", () => {
    rememberSession(ENTRY);
    expect(readLastSession()).toEqual(ENTRY);
  });

  it("keeps the last session, not the first - the spectator follows what is running now", () => {
    const storage = workingStorage();
    rememberSession(ENTRY, storage);
    rememberSession({ ...ENTRY, session_id: "later", variant_id: "D" }, storage);
    expect(readLastSession(storage)?.session_id).toBe("later");
    expect(readLastSession(storage)?.variant_id).toBe("D");
  });

  it("namespaces its key, so it cannot collide on a shared localhost origin", () => {
    expect(LAST_SESSION_KEY).toContain("shoppertwin");
  });
});

describe("reading when there is nothing honest to return", () => {
  it("is null when nothing was ever written", () => {
    expect(readLastSession(workingStorage())).toBeNull();
  });

  it("is null for a value that is not JSON at all", () => {
    expect(readLastSession(workingStorage({ [LAST_SESSION_KEY]: "not json {" }))).toBeNull();
  });

  it("is null for a document missing a field, rather than half a session", () => {
    // A session id with no variant cannot open the dashboard, which needs both.
    const partial = JSON.stringify({ session_id: "s-1", started_at: ENTRY.started_at });
    expect(readLastSession(workingStorage({ [LAST_SESSION_KEY]: partial }))).toBeNull();
  });

  it("is null for fields of the wrong type", () => {
    const wrong = JSON.stringify({ session_id: 7, variant_id: "A", started_at: "x" });
    expect(readLastSession(workingStorage({ [LAST_SESSION_KEY]: wrong }))).toBeNull();
  });

  it("is null for an empty session id, which would open a socket to nothing", () => {
    const empty = JSON.stringify({ ...ENTRY, session_id: "" });
    expect(readLastSession(workingStorage({ [LAST_SESSION_KEY]: empty }))).toBeNull();
  });
});

describe("a localStorage that throws", () => {
  it("does not take the store's session down with it", () => {
    expect(() => rememberSession(ENTRY, throwingStorage)).not.toThrow();
  });

  it("reads as no session rather than throwing", () => {
    expect(readLastSession(throwingStorage)).toBeNull();
  });

  it("treats an absent storage as no session, both ways", () => {
    // `window.localStorage` itself throws when site data is blocked, so the
    // caller may legitimately have nothing to hand over.
    expect(() => rememberSession(ENTRY, null)).not.toThrow();
    expect(readLastSession(null)).toBeNull();
  });
});
