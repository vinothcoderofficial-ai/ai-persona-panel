import type { Session } from "@/contracts/session.schema";

/** The three intake answers, exactly as `session.schema.json` stores them. */
export type Intake = NonNullable<Session["intake"]>;

export type ArchetypeLabel = NonNullable<Session["archetype_label"]>;

/**
 * SPEC 4.3: intake -> archetype, evaluated **in this order**.
 *
 *   has_list && hurry    -> mission
 *   !has_list && !hurry  -> browser
 *   same_brand           -> loyalist
 *   otherwise            -> switcher
 *
 * The order is the rule, not a formatting detail: a shopper who came with a
 * list, is in a hurry *and* always buys the same brand is a mission shopper,
 * because the first line matches first. Reordering these branches silently
 * relabels a chunk of the real panel and breaks the comparison against the
 * synthetic one.
 */
export function archetypeFromIntake(intake: Intake): ArchetypeLabel {
  if (intake.has_list && intake.hurry) return "mission";
  if (!intake.has_list && !intake.hurry) return "browser";
  if (intake.same_brand) return "loyalist";
  return "switcher";
}
