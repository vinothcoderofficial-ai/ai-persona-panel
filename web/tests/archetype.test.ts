import { describe, expect, it } from "vitest";
import { archetypeFromIntake, type Intake } from "@/capture/archetype";

/**
 * SPEC 4.3, evaluated strictly in this order:
 *   has_list && hurry  -> mission
 *   !has_list && !hurry -> browser
 *   same_brand          -> loyalist
 *   otherwise           -> switcher
 */
function intake(has_list: boolean, same_brand: boolean, hurry: boolean): Intake {
  return { has_list, same_brand, hurry };
}

describe("archetypeFromIntake", () => {
  it("covers all eight intake combinations", () => {
    const table: [Intake, string][] = [
      // has_list && hurry wins whatever same_brand says.
      [intake(true, true, true), "mission"],
      [intake(true, false, true), "mission"],
      // No list and no hurry is a browser, again regardless of same_brand.
      [intake(false, true, false), "browser"],
      [intake(false, false, false), "browser"],
      // Neither of the first two rules fired: same_brand decides.
      [intake(true, true, false), "loyalist"],
      [intake(false, true, true), "loyalist"],
      // Nothing fired.
      [intake(true, false, false), "switcher"],
      [intake(false, false, true), "switcher"],
    ];

    expect(table.map(([answers]) => archetypeFromIntake(answers))).toEqual(
      table.map(([, label]) => label),
    );
  });

  it("puts a listed, hurried, brand-loyal shopper in mission, not loyalist", () => {
    // The precedence case: three trues. Order of evaluation is the whole point.
    expect(archetypeFromIntake({ has_list: true, same_brand: true, hurry: true })).toBe(
      "mission",
    );
  });

  it("returns only labels the session schema allows", () => {
    const labels = new Set<string>();
    for (const has_list of [true, false]) {
      for (const same_brand of [true, false]) {
        for (const hurry of [true, false]) {
          labels.add(archetypeFromIntake(intake(has_list, same_brand, hurry)));
        }
      }
    }
    expect([...labels].sort()).toEqual(["browser", "loyalist", "mission", "switcher"]);
  });
});
