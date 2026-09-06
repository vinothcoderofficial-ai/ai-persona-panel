/**
 * The store's fixture colours.
 *
 * Two of them are furniture — the carcass and the boards — and one of them is a
 * rule. The aisle display keeps its own colours in `AisleDisplay.tsx` on
 * purpose: the whole point of that prop is that it must *not* read as part of
 * the gondola run, so sharing the gondola's palette is the last thing it wants.
 *
 * `EMPTY_SPACE_COLOR` says "nothing is booked here, on purpose". The planogram
 * carries six slots with `sku_id: null` and `facings: 0`, and on variant A two
 * of the three ad fixtures carry no creative; both are the experiment as
 * designed, not gaps waiting to be filled. Space that is deliberately empty has
 * to read as deliberately empty, or a viewer reads it as a bug and a shopper
 * reads it as a shelf that has been picked clean.
 *
 * It is one constant rather than one per fixture type because the store should
 * teach the shopper a single thing: this dark slate is space nothing occupies.
 * An empty shelf position and an unbooked ad fixture are the same statement
 * about the planogram, and they should not be two different colours saying it.
 *
 * They live in their own module because `Bay.tsx` and `AdSlot.tsx` both need
 * them and `Bay` imports `AdSlot`, so hanging them off either component would
 * be an import cycle waiting to happen. `geometry.ts` is the wrong home too: it
 * is the single source of *placement*, and a colour is not a position.
 */

/** The bay box behind the shelves, and the housing of an ad fixture. */
export const CARCASS_COLOR = "#b3b8c0";

/** Shelf boards: the lightest thing in the store, so the packs sit against it. */
export const BOARD_COLOR = "#eceef2";

/** Deliberately empty: an unfilled shelf position, an unbooked ad fixture. */
export const EMPTY_SPACE_COLOR = "#565b63";
