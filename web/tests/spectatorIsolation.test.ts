import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

/**
 * CLAUDE.md, and docs/SPEC.md's risk table row 10:
 *
 *   "The shopper's own screen must not show their gaze dot. People stare at the
 *    dot and corrupt the data. The dot belongs on the spectator view only."
 *
 * That rule is a property of the import graph, so it is checked as one. If a
 * later change ever pulls a spectator component into the shopper's screen -
 * directly or through any depth of intermediate module - this test fails and
 * names the path that did it.
 */

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = resolve(HERE, "..", "src");
const SPECTATOR = join(SRC, "spectator");

/** The modules that are on screen while a person is being measured. */
const SHOPPER_ENTRY_POINTS = [
  join(SRC, "store", "PlanogramScene.tsx"),
  join(SRC, "capture", "CaptureFlow.tsx"),
];

const IMPORT_PATTERN = /(?:from|import)\s*\(?\s*["']([^"']+)["']/g;
const CANDIDATE_SUFFIXES = ["", ".ts", ".tsx", ".d.ts", "/index.ts", "/index.tsx"];

function readable(path: string): boolean {
  try {
    return statSync(path).isFile();
  } catch {
    return false;
  }
}

/** Resolve one specifier to a file inside web/src, or null if it leaves it. */
function resolveLocal(specifier: string, importer: string): string | null {
  let base: string;
  if (specifier.startsWith("@/")) {
    base = join(SRC, specifier.slice(2));
  } else if (specifier.startsWith(".")) {
    base = resolve(dirname(importer), specifier);
  } else {
    return null; // a package: react, three, webgazer...
  }
  for (const suffix of CANDIDATE_SUFFIXES) {
    const candidate = base + suffix;
    if (readable(candidate)) return candidate;
  }
  return null;
}

/** Every module reachable from `entry`, with the path that reached it. */
function reachable(entry: string): Map<string, string[]> {
  const seen = new Map<string, string[]>([[entry, [entry]]]);
  const queue = [entry];
  while (queue.length > 0) {
    const current = queue.shift() as string;
    const source = readFileSync(current, "utf8");
    const trail = seen.get(current) as string[];
    for (const match of source.matchAll(IMPORT_PATTERN)) {
      const target = resolveLocal(match[1], current);
      if (target === null || seen.has(target)) continue;
      seen.set(target, [...trail, target]);
      queue.push(target);
    }
  }
  return seen;
}

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

describe("the shopper's screen cannot render spectator content", () => {
  it("reaches no module under src/spectator, at any import depth", () => {
    for (const entry of SHOPPER_ENTRY_POINTS) {
      const graph = reachable(entry);
      const leaks = [...graph.entries()]
        .filter(([path]) => path.startsWith(SPECTATOR + sep))
        .map(([, trail]) => trail.map(short).join(" -> "));
      expect(leaks, `${short(entry)} must not reach src/spectator`).toEqual([]);
    }
  });

  it("has a spectator directory that is actually populated, so the guard is not vacuous", () => {
    const files = walk(SPECTATOR).map(short);
    for (const required of [
      "spectator/SpectatorView.tsx",
      "spectator/GazeTrail.tsx",
      "spectator/LiveHeatmap.tsx",
      "spectator/AgreementMeter.tsx",
      "spectator/PredictionBadge.tsx",
      "spectator/ClockOverlay.tsx",
    ]) {
      expect(files).toContain(required);
    }
  });

  it("mentions nothing from src/spectator anywhere in src/store or src/capture", () => {
    // Belt and braces: catches a stray dynamic import or a string path that the
    // graph walk above would resolve differently.
    for (const path of [...walk(join(SRC, "store")), ...walk(join(SRC, "capture"))]) {
      const source = readFileSync(path, "utf8");
      for (const match of source.matchAll(IMPORT_PATTERN)) {
        expect(
          match[1],
          `${short(path)} imports ${match[1]}`,
        ).not.toMatch(/spectator/i);
      }
    }
  });

  it("routes the spectator view from main.tsx only, behind its own hash", () => {
    const main = readFileSync(join(SRC, "main.tsx"), "utf8");
    expect(main).toContain("#/spectator");
    // main.tsx is the one module allowed to know about both, because it renders
    // exactly one of them.
    expect(main).toContain("@/spectator/SpectatorView");
  });
});
