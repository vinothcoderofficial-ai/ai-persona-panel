import type { CSSProperties, ReactNode } from "react";
import type { Planogram } from "@/contracts/planogram.schema";
import {
  CLEAR_CREATIVE,
  NO_CHANGE,
  SHELF_LEVELS,
  type PromoChoice,
  type ShelfLevel,
  type WhatIfSelection,
} from "@/whatif/patches";
import { label, note, panel, panelHeading, select as selectStyle } from "@/whatif/styles";

/**
 * SPEC M9's three what-if controls: *focal SKU to shelf level; creative to ad
 * slot; promo on/off*.
 *
 * Five dropdowns, because two of those three name a pair. Every option is read
 * off the resolved planogram, so the page can only ever ask for a move, a
 * creative or a price the base planogram actually has - a patch naming
 * something that does not exist is a 400 from the endpoint, and there is no
 * reason to let a dropdown produce one.
 *
 * Presentational: it holds no state and decides nothing. `WhatIfPanel` owns the
 * selection and `patches.ts` turns it into patches.
 */

const LEVEL_NAMES: Record<ShelfLevel, string> = {
  top: "Top shelf",
  above_eye: "Above eye",
  eye: "Eye level",
  below_eye: "Below eye",
  bottom: "Bottom shelf",
};

const PROMO_NAMES: Record<Exclude<PromoChoice, "">, string> = {
  on: "Promo on",
  off: "Promo off",
};

export interface WhatIfControlsProps {
  /** The base planogram, resolved. Every option comes from it. */
  planogram: Planogram;
  selection: WhatIfSelection;
  onChange: (next: WhatIfSelection) => void;
}

function Field({
  testId,
  title,
  value,
  disabled,
  hint,
  onPick,
  children,
}: {
  testId: string;
  title: string;
  value: string;
  disabled?: boolean;
  hint?: string;
  onPick: (value: string) => void;
  children: ReactNode;
}) {
  return (
    <label style={fieldStyle}>
      <span style={label}>{title}</span>
      <select
        data-testid={testId}
        style={{ ...selectStyle, opacity: disabled === true ? 0.5 : 1 }}
        value={value}
        disabled={disabled === true}
        onChange={(event) => onPick(event.target.value)}
      >
        {children}
      </select>
      {hint !== undefined && <span style={{ ...note, marginTop: 4 }}>{hint}</span>}
    </label>
  );
}

export function WhatIfControls({ planogram, selection, onChange }: WhatIfControlsProps) {
  const adSlots = planogram.bays.flatMap((bay) => bay.ad_slots);
  // Nothing can be moved or re-priced until a SKU is named, so those two
  // controls are off rather than silently doing nothing when used.
  const noFocalSku = selection.focalSkuId === NO_CHANGE;
  const noAdSlot = selection.adSlotId === NO_CHANGE;

  return (
    <section style={panel} data-testid="whatif-controls">
      <div style={panelHeading}>What if we...</div>
      <div style={gridStyle}>
        <Field
          testId="whatif-focal-sku"
          title="Focal SKU"
          value={selection.focalSkuId}
          onPick={(focalSkuId) => onChange({ ...selection, focalSkuId })}
        >
          <option value={NO_CHANGE}>No focal SKU</option>
          {planogram.skus.map((sku) => (
            <option key={sku.sku_id} value={sku.sku_id}>
              {sku.sku_id} — {sku.name}
            </option>
          ))}
        </Field>

        <Field
          testId="whatif-shelf-level"
          title="Move it to"
          value={selection.shelfLevel}
          disabled={noFocalSku}
          hint="Into the free slot on that shelf in its own bay, or swapping with the SKU already there."
          onPick={(value) => onChange({ ...selection, shelfLevel: value as ShelfLevel | "" })}
        >
          <option value={NO_CHANGE}>Leave it where it is</option>
          {SHELF_LEVELS.map((level) => (
            <option key={level} value={level}>
              {LEVEL_NAMES[level]}
            </option>
          ))}
        </Field>

        <Field
          testId="whatif-promo"
          title="Promo flag"
          value={selection.promo}
          disabled={noFocalSku}
          hint="Price is carried through unchanged, so only the flag moves."
          onPick={(value) => onChange({ ...selection, promo: value as PromoChoice })}
        >
          <option value={NO_CHANGE}>Leave the promo flag alone</option>
          {(["on", "off"] as const).map((choice) => (
            <option key={choice} value={choice}>
              {PROMO_NAMES[choice]}
            </option>
          ))}
        </Field>

        <Field
          testId="whatif-ad-slot"
          title="Ad slot"
          value={selection.adSlotId}
          onPick={(adSlotId) => onChange({ ...selection, adSlotId })}
        >
          <option value={NO_CHANGE}>No ad slot</option>
          {adSlots.map((adSlot) => (
            <option key={adSlot.ad_slot_id} value={adSlot.ad_slot_id}>
              {adSlot.ad_slot_id} — {adSlot.type.replace(/_/g, " ")}
            </option>
          ))}
        </Field>

        <Field
          testId="whatif-creative"
          title="Creative"
          value={selection.creativeId}
          disabled={noAdSlot}
          onPick={(creativeId) => onChange({ ...selection, creativeId })}
        >
          <option value={NO_CHANGE}>Leave the creative alone</option>
          <option value={CLEAR_CREATIVE}>Clear it — no creative</option>
          {planogram.creatives.map((creative) => (
            <option key={creative.creative_id} value={creative.creative_id}>
              {creative.creative_id} — {creative.brand}
            </option>
          ))}
        </Field>
      </div>
    </section>
  );
}

const gridStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
  gap: 14,
};

const fieldStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  minWidth: 0,
};
