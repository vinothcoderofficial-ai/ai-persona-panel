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

/**
 * A CIE Lab triple as a CSS colour.
 *
 * `schemas/planogram.schema.json` carries every SKU's colour as `color_lab`,
 * and `sim/saliency.py` reads that triple directly for its colour-contrast
 * term. For a SKU read off a video it is the *only* thing about the product
 * that was measured - `vision/planogram.py` writes brand, name, price and
 * promo as unknown on purpose - so it is what the scene draws when there is no
 * pack shot to draw instead.
 *
 * Lives here rather than in a screen because two places need the same answer:
 * `#/vision`'s swatch list and `ProductSlot`'s material. Two conversions would
 * eventually disagree, and the shelf and the table beside it would then be
 * showing different colours for the same product.
 */
export function labToCss(lab: readonly number[]): string {
  const [l = 50, a = 0, b = 0] = lab;
  const y = (l + 16) / 116;
  const x = a / 500 + y;
  const z = y - b / 200;

  const expand = (t: number): number => (t ** 3 > 0.008856 ? t ** 3 : (t - 16 / 116) / 7.787);
  const [xr, yr, zr] = [expand(x) * 95.047, expand(y) * 100.0, expand(z) * 108.883];

  const clamp = (v: number): number => Math.max(0, Math.min(255, Math.round(v)));
  const gamma = (v: number): number =>
    v > 0.0031308 ? 1.055 * v ** (1 / 2.4) - 0.055 : 12.92 * v;

  const r = gamma((xr * 3.2406 - yr * 1.5372 - zr * 0.4986) / 100);
  const g = gamma((-xr * 0.9689 + yr * 1.8758 + zr * 0.0415) / 100);
  const bl = gamma((xr * 0.0557 - yr * 0.204 + zr * 1.057) / 100);

  return `rgb(${clamp(r * 255)}, ${clamp(g * 255)}, ${clamp(bl * 255)})`;
}
