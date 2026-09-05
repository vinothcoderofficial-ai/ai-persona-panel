import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { mergedQuery, mergedQueryString } from "@/session/urlParams";

/**
 * The one merge rule, and the two bugs that paid for it.
 *
 * A ShopperTwin URL may carry its params on either side of the `#`, and the
 * hash wins on a collision. That rule used to be written out twice - once in
 * `main.tsx`, once in `spectator/SpectatorView.tsx` - and for a while the two
 * copies disagreed, because only the spectator's actually read the hash. Both
 * halves of that disagreement were reproduced in a browser:
 *
 *   1. `#/dashboard?session=<id>` loaded a *different* session than the URL
 *      named, and captioned it with a note claiming no session had been named.
 *   2. `#/?variant=D&skip_capture=1` dropped both params, so a consented
 *      shopper could be measured on variant A while believing they were on D -
 *      a session collected, accepted and filed under an arm nobody chose.
 *
 * The second one is not a screen bug. It puts a real measurement in the wrong
 * arm of the panel, which is a data-integrity failure this project cannot
 * detect after the fact. These tests pin the rule, and the last one pins the
 * fact that it is written down exactly once.
 */

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = resolve(HERE, "..", "src");

describe("mergedQuery", () => {
  it("reads params written before the hash, which is the participant link", () => {
    // `scripts/collect_link.py` hands participants `https://host/?variant=X`.
    const params = mergedQuery("?variant=B&skip_capture=1", "#/");
    expect(params.get("variant")).toBe("B");
    expect(params.get("skip_capture")).toBe("1");
  });

  it("also reads them written after it, which is how anyone types a hash route", () => {
    const params = mergedQuery("", "#/spectator?session=s-1&fake=1");
    expect(params.get("session")).toBe("s-1");
    expect(params.get("fake")).toBe("1");
  });

  it("lets the hash win, because it is the half that names the route", () => {
    const params = mergedQuery("?session=before&ceiling=0.5", "#/spectator?session=after");
    expect(params.get("session")).toBe("after");
    // A param only the search carried is still there: the hash overrides, it
    // does not replace.
    expect(params.get("ceiling")).toBe("0.5");
  });

  it("copes with no query at all, on either side", () => {
    expect(mergedQuery("", "#/spectator").toString()).toBe("");
    expect(mergedQuery("", "").toString()).toBe("");
    expect(mergedQuery("", "#/whatif?").toString()).toBe("");
  });

  it("returns a live URLSearchParams, not a string", () => {
    const params = mergedQuery("?variant=B", "#/");
    expect(params).toBeInstanceOf(URLSearchParams);
    params.set("session", "s-1");
    expect(params.get("session")).toBe("s-1");
  });

  it("replaces a repeated key outright rather than appending to it", () => {
    // `set`, not `append`: `?variant=A&variant=B#/?variant=D` means D, once.
    const params = mergedQuery("?variant=A&variant=B", "#/?variant=D");
    expect(params.getAll("variant")).toEqual(["D"]);
    // With nothing in the hash to override them, repeats are left as typed -
    // this function decides precedence, it does not tidy URLs.
    expect(mergedQuery("?variant=A&variant=B", "#/").getAll("variant")).toEqual(["A", "B"]);
  });
});

describe("the two bugs this module exists to prevent", () => {
  it("names the session in #/dashboard?session=<id>, so no other one is loaded", () => {
    // Bug 1: the dashboard read location.search only, found nothing, loaded the
    // remembered session instead, and printed a note saying none had been named.
    const params = mergedQuery("", "#/dashboard?session=f76c3037-46c7-4bc0-9c6c-5ddf7b6c1539");
    expect(params.get("session")).toBe("f76c3037-46c7-4bc0-9c6c-5ddf7b6c1539");
  });

  it("keeps the variant in #/?variant=D&skip_capture=1, so nobody is measured on A", () => {
    // Bug 2, the expensive one: both params were lost, the store fell back to
    // its default variant, and the session was filed under an arm nobody chose.
    const params = mergedQuery("", "#/?variant=D&skip_capture=1");
    expect(params.get("variant")).toBe("D");
    expect(params.get("skip_capture")).toBe("1");
  });
});

describe("mergedQueryString", () => {
  it("is the serialised form of the same merge, for callers that take a string", () => {
    expect(mergedQueryString("?session=s-1&fake=1", "#/spectator")).toBe(
      new URLSearchParams({ session: "s-1", fake: "1" }).toString(),
    );
    expect(mergedQueryString("", "#/spectator")).toBe("");
  });

  it("never disagrees with mergedQuery, because it does not merge anything itself", () => {
    for (const [search, hash] of [
      ["?variant=B", "#/"],
      ["", "#/spectator?session=s-1&fake=1"],
      ["?session=before&ceiling=0.5", "#/spectator?session=after"],
      ["?variant=A&variant=B", "#/?variant=D"],
      ["", ""],
    ] as const) {
      expect(mergedQueryString(search, hash)).toBe(mergedQuery(search, hash).toString());
    }
  });
});

// ---------------------------------------------------------------------------
// Written down once
// ---------------------------------------------------------------------------

function walk(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) out.push(...walk(path));
    else if (/\.tsx?$/.test(name)) out.push(path);
  }
  return out;
}

function short(path: string): string {
  return relative(SRC, path).split(sep).join("/");
}

/**
 * The rule has to be *one* implementation, not two that agree today.
 *
 * The wrong-variant measurement above happened because there were two copies
 * and only one of them read the hash. Two copies that agree are the same bug
 * waiting for the next edit, so this walks the source for anyone splitting a
 * fragment on its `?` and insists there is exactly one such place.
 */
describe("the merge rule exists once in the codebase", () => {
  it("splits a fragment on its '?' in src/session/urlParams.ts and nowhere else", () => {
    const splitters = walk(SRC)
      .filter((path) => /\.indexOf\("\?"\)|\.split\("\?"\)/.test(readFileSync(path, "utf8")))
      .map(short);
    expect(splitters).toEqual(["session/urlParams.ts"]);
  });
});
