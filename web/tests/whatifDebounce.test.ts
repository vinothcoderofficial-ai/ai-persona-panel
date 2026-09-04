import { describe, expect, it } from "vitest";
import { DEBOUNCE_MS, createDebouncer } from "@/whatif/debounce";
import { fakeClock } from "./whatifFixture";

/**
 * SPEC M9: "debounce 300 ms in the UI". Dragging through a dropdown must not
 * fire a simulation per keystroke. The timer is injected, so this runs with no
 * real waiting at all.
 */

describe("createDebouncer", () => {
  it("runs only the last of a burst", () => {
    const clock = fakeClock();
    const seen: string[] = [];
    const debouncer = createDebouncer(DEBOUNCE_MS, clock.schedule);

    debouncer.schedule(() => seen.push("first"));
    debouncer.schedule(() => seen.push("second"));
    debouncer.schedule(() => seen.push("third"));
    expect(seen).toEqual([]);

    clock.flush();
    expect(seen).toEqual(["third"]);
  });

  it("waits the 300 ms SPEC M9 asks for", () => {
    const clock = fakeClock();
    createDebouncer(DEBOUNCE_MS, clock.schedule).schedule(() => undefined);
    expect(DEBOUNCE_MS).toBe(300);
    expect(clock.delays).toEqual([300]);
  });

  it("cancels a pending call outright", () => {
    const clock = fakeClock();
    let ran = 0;
    const debouncer = createDebouncer(DEBOUNCE_MS, clock.schedule);
    debouncer.schedule(() => {
      ran += 1;
    });
    debouncer.cancel();
    clock.flush();
    expect(ran).toBe(0);
  });

  it("runs a later call after an earlier one has already fired", () => {
    const clock = fakeClock();
    const seen: string[] = [];
    const debouncer = createDebouncer(DEBOUNCE_MS, clock.schedule);

    debouncer.schedule(() => seen.push("one"));
    clock.flush();
    debouncer.schedule(() => seen.push("two"));
    clock.flush();
    expect(seen).toEqual(["one", "two"]);
  });
});
