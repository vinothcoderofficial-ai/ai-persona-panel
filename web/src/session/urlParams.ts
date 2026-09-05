/**
 * Where a ShopperTwin URL keeps its params, and which half of it wins.
 *
 * Every screen in this app is addressed by one URL with two halves. The
 * participant link `scripts/collect_link.py` writes is `https://host/?variant=X`
 * with no fragment at all, so its params live in `location.search`. The screens
 * table in README is spelled `#/dashboard?session=<id>&variant=<id>`, and
 * `#/spectator?session=<id>` is simply how a person types a hash route, so
 * those params live in `location.hash` where `location.search` cannot see them.
 * Both spellings are real, both are documented, and both get typed - so every
 * screen has to read both, and one of the two has to win when they disagree.
 *
 * **The hash wins**, because it is the half that names the route.
 *
 * Why this is a module and not a private helper
 * ---------------------------------------------
 * Because it was a private helper, twice, and the two copies disagreed. Until
 * this module existed the rule was written out in `spectator/SpectatorView.tsx`
 * (which merged both halves) and again in `main.tsx` (which read
 * `location.search` and nothing else). That gap produced two bugs, both
 * reproduced in a live browser:
 *
 *   1. **The dashboard showed the wrong session.** `#/dashboard?session=X` put
 *      X in `location.hash`; the router could not see it, found no session
 *      named, and loaded the *remembered* session in its place - under a note
 *      on screen asserting that the URL had named no session. Wrong data with a
 *      confident caption.
 *
 *   2. **A shopper could be measured on the wrong arm.** `#/?variant=D&skip_capture=1`
 *      lost both params. The store fell back to variant A, and a real consented
 *      session was collected, accepted and filed under an arm nobody chose.
 *      That one does not stop at a screen: it puts a measurement in the wrong
 *      cell of the panel, silently, and nothing downstream can detect it
 *      afterwards. `predictions/` is committed evidence, and evidence filed
 *      under the wrong condition is worse than no evidence.
 *
 * Both are fixed, and the fix is that there is now one implementation of the
 * rule and two callers of it. Two copies that agree today are the same bug
 * waiting for the next person to edit one of them, which is why
 * `web/tests/urlParams.test.ts` walks `web/src` and fails if a second copy
 * appears anywhere.
 *
 * This module deliberately knows nothing about `window`: it takes the two
 * strings and returns the answer, so both callers can be tested without a
 * location, and so nothing here can drag a screen's module into another
 * screen's import graph (`web/tests/spectatorIsolation.test.ts`).
 */

/**
 * The effective query for a page, merged from both sides of the `#`.
 *
 * Params written before the hash are the base; params written after it are laid
 * over the top, replacing a key outright rather than appending to it - so
 * `?variant=A#/?variant=D` is D, once. A key that only the search carried
 * survives: the hash overrides, it does not replace.
 */
export function mergedQuery(search: string, hash: string): URLSearchParams {
  const merged = new URLSearchParams(search);
  const marker = hash.indexOf("?");
  if (marker !== -1) {
    for (const [key, value] of new URLSearchParams(hash.slice(marker + 1))) {
      merged.set(key, value);
    }
  }
  return merged;
}

/**
 * The same merge, serialised - for the callers that pass a query string on
 * rather than reading keys out of it (`spectatorParamsFromQuery`, which parses
 * a string, and `lockFromQuery` underneath it).
 *
 * It merges nothing itself. It is `mergedQuery(...).toString()` and must stay
 * that way, so the two forms can never drift into two rules again.
 */
export function mergedQueryString(search: string, hash: string): string {
  return mergedQuery(search, hash).toString();
}
