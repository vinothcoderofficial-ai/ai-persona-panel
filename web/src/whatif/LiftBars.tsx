import type { CSSProperties } from "react";
import { Bar, BarChart, Cell, ReferenceLine, XAxis, YAxis } from "recharts";
import {
  NOT_APPLICABLE,
  formatLift,
  liftExplanation,
  type PersonaLiftRow,
} from "@/whatif/lift";
import {
  DOWN,
  GREY,
  PANEL_BORDER,
  UP,
  absent,
  bigNumber,
  mono,
  note,
  panel,
  panelHeading,
} from "@/whatif/styles";

/**
 * SPEC M9: *"`LiftBars.tsx` per-persona lift vs baseline."*
 *
 * Two kinds of figure, labelled apart because they come from different places:
 *
 *  * **Population lift** - `lift_vs_baseline` exactly as `POST /whatif`
 *    returned it, computed on the server against its own cached baseline run.
 *  * **Per-persona bars** - each bar is *the relative change in that persona's
 *    probability of fixating the focal SKU*, patched run against the baseline
 *    run this page opened with. `lift.ts:personaLiftRows` is where that is
 *    worked out, and its docstring is the full definition.
 *
 * Whatever cannot be computed is written out in words. `lift_vs_baseline` is
 * `{}` when no SKU was focal, and a key is `null` when its baseline was exactly
 * 0 and the ratio is undefined; a persona whose baseline attention was 0 has no
 * bar for the same reason. None of those is 0%, and none of them is drawn as a
 * zero-length bar - on a recorded demo a fabricated figure is worse than a
 * blank.
 */

/** The two figures SPEC 4.8 puts in `lift_vs_baseline`, in reporting order. */
const POPULATION_KEYS: { key: string; title: string }[] = [
  { key: "focal_sku_attention", title: "Focal SKU attention" },
  { key: "focal_sku_purchase_share", title: "Focal SKU purchase share" },
];

export interface LiftBarsProps {
  /** `lift_vs_baseline`, untouched. A missing key and a null key differ. */
  lift: Record<string, number | null>;
  rows: PersonaLiftRow[];
  focalSkuId: string | null;
  width?: number;
  height?: number;
}

function PopulationFigure({
  lift,
  entry,
}: {
  lift: Record<string, number | null>;
  entry: { key: string; title: string };
}) {
  const value = lift[entry.key];
  const computed = entry.key in lift && value !== null && value !== undefined;
  const explanation = liftExplanation(lift, entry.key);

  return (
    <div style={figureStyle}>
      <div style={{ ...note, textTransform: "uppercase", letterSpacing: "0.08em" }}>
        {entry.title}
      </div>
      <div
        data-testid={`whatif-lift-${entry.key}`}
        data-lift={computed ? String(value) : ""}
        style={computed ? { ...bigNumber, color: (value ?? 0) < 0 ? DOWN : UP } : absentFigure}
      >
        {formatLift(value)}
      </div>
      {explanation !== null && (
        <div data-testid={`whatif-lift-explainer-${entry.key}`} style={{ ...note, maxWidth: 320 }}>
          {explanation}
        </div>
      )}
    </div>
  );
}

export function LiftBars({ lift, rows, focalSkuId, width = 460, height = 190 }: LiftBarsProps) {
  const drawable = rows.filter((row) => row.lift !== null);
  const chartRows = drawable.map((row) => ({
    persona: row.personaId,
    // Percentage points, so the axis reads the way the labels beside it do.
    lift: (row.lift ?? 0) * 100,
  }));

  return (
    <section style={panel} data-testid="lift-bars">
      <div style={panelHeading}>
        Lift vs baseline{focalSkuId === null ? "" : ` — ${focalSkuId}`}
      </div>

      <div style={figuresRowStyle}>
        {POPULATION_KEYS.map((entry) => (
          <PopulationFigure key={entry.key} lift={lift} entry={entry} />
        ))}
      </div>
      <div style={{ ...note, marginBottom: 12 }}>
        Population figures come from <code style={mono}>POST /whatif</code> itself.
      </div>

      {rows.length === 0 ? (
        <div data-testid="whatif-persona-lift-empty" style={absent}>
          No per-persona lift yet — it needs a focal SKU and the baseline run this page
          opened with.
        </div>
      ) : (
        <>
          {chartRows.length > 0 && (
            <BarChart
              width={width}
              height={height}
              data={chartRows}
              margin={{ top: 8, right: 12, bottom: 4, left: 4 }}
            >
              <XAxis dataKey="persona" stroke={GREY} tick={{ fontSize: 12, fill: GREY }} />
              <YAxis
                stroke={GREY}
                tick={{ fontSize: 12, fill: GREY }}
                tickFormatter={(value: number) => `${value.toFixed(0)}%`}
              />
              <ReferenceLine y={0} stroke={PANEL_BORDER} />
              <Bar dataKey="lift" name="Lift vs baseline" isAnimationActive={false}>
                {chartRows.map((row) => (
                  <Cell key={row.persona} fill={row.lift < 0 ? DOWN : UP} />
                ))}
              </Bar>
            </BarChart>
          )}

          <div style={tableStyle}>
            {rows.map((row) => (
              <div key={row.personaId} style={tableRowStyle}>
                <div style={{ ...mono, flex: "0 0 96px", color: GREY, fontSize: 13 }}>
                  {row.personaId}
                </div>
                <div style={{ ...mono, flex: 1, fontSize: 13, opacity: 0.85 }}>
                  {row.baseline.toFixed(4)} → {row.patched.toFixed(4)}
                </div>
                <div
                  data-testid={`whatif-persona-lift-${row.personaId}`}
                  data-lift={row.lift === null ? "" : String(row.lift)}
                  style={
                    row.lift === null
                      ? { ...absent, flex: "0 0 130px", textAlign: "right", fontSize: 13 }
                      : {
                          ...mono,
                          flex: "0 0 130px",
                          textAlign: "right",
                          fontSize: 15,
                          fontWeight: 700,
                          color: row.lift < 0 ? DOWN : UP,
                        }
                  }
                >
                  {row.lift === null ? NOT_APPLICABLE : formatLift(row.lift)}
                </div>
              </div>
            ))}
          </div>

          <div style={{ ...note, marginTop: 8 }}>
            Each bar is the relative change in that persona&apos;s probability of fixating the
            focal SKU, this run against the baseline run — computed in the browser from the two
            <code style={mono}> per_persona</code> results, not returned by the endpoint.
          </div>
        </>
      )}
    </section>
  );
}

const figuresRowStyle: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: 28,
  marginBottom: 6,
};

const figureStyle: CSSProperties = {
  minWidth: 200,
};

const absentFigure: CSSProperties = {
  ...absent,
  fontSize: 22,
  lineHeight: 1.4,
};

const tableStyle: CSSProperties = {
  marginTop: 10,
  borderTop: `1px solid ${PANEL_BORDER}`,
};

const tableRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 10,
  padding: "5px 0",
  borderBottom: `1px solid ${PANEL_BORDER}`,
};
