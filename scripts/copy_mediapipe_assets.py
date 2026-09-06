"""Put WebGazer's MediaPipe assets where the browser can fetch them.

Run: `python scripts/copy_mediapipe_assets.py` (exit 0 on success, 1 on a
missing or incomplete source). Part of `make setup`.

Why this exists
---------------
WebGazer 3.5.x replaced the TensorFlow FaceMesh backend with MediaPipe's
local-WASM runtime. `node_modules/webgazer/src/facemesh.mjs` calls
`createDetector` with `runtime: 'mediapipe'` and
`solutionPath: params.faceMeshSolutionPath`, and MediaPipe turns that path into
`locateFile` URLs it fetches **from this app's own web root** - roughly 17 MB of
WASM binaries, a packed asset blob and the face-mesh graph.

Nothing in this repository served them, so every one of those requests 404ed.
MediaPipe's script injector resolves its load promise on the `error` event as
well as on `load`, so the 404 was swallowed; the next statement called
`window.createMediapipeSolutionsWasm`, which was still the `{locateFile}`
placeholder object rather than the real Emscripten factory, and that threw
"z2 is not a function" (`z2` is the bundler's renamed copy of MediaPipe's
minified local). `GazeTracker.start()` rejected, the capture flow caught it, and
every webcam session degraded to `mode: "cursor_only"` with a one-line notice.
A project whose central claim is that it measures gaze was measuring mouse
pointers, and no test was red.

Where the files come from
-------------------------
`node_modules/webgazer/dist/mediapipe/face_mesh/`. webgazer ships them itself
and declares them in its own `files` list, so this script depends only on a
package `package.json` already declares. The same bytes also sit in
`node_modules/@mediapipe/face_mesh/`, but that is a transitive dependency of
`@tensorflow-models/face-landmarks-detection` - reaching into it would make this
repository depend on a package it never asked for and npm is free to hoist,
dedupe or move.

The whole directory is copied, not just the eight files MediaPipe requests: a
later webgazer may want a ninth, and the `package.json` beside them is what
records that these are Apache-2.0 MediaPipe files. `REQUIRED_ASSETS` is
therefore a floor - the copy is refused if any of them is absent - and not a
filter.

`web/public/mediapipe/` is gitignored, exactly as `web/public/textures/*.png`
is: 17 MB of node_modules content does not belong in a git history, and this
script is what makes a fresh clone whole.
"""
import filecmp
import pathlib
import shutil
import sys
from dataclasses import dataclass

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: What webgazer installs. Not @mediapipe/face_mesh - see the module docstring.
SOURCE = ROOT / "node_modules" / "webgazer" / "dist" / "mediapipe" / "face_mesh"

#: Vite's `root` is `web/`, so `web/public/` is served at `/`, and this lands on
#: the URL `/mediapipe/face_mesh` that `web/src/capture/GazeTracker.ts` sets as
#: `webgazer.params.faceMeshSolutionPath`. The two are tied together by a test:
#: `web/tests/mediapipeAssets.test.ts`.
DEST = ROOT / "web" / "public" / "mediapipe" / "face_mesh"

#: Every file MediaPipe's `locateFile` asks for, by the name it asks for. The
#: browser picks the `simd` trio or the plain pair at runtime depending on what
#: it supports, so both sets have to be present. `..._simd_wasm_bin.data` is a
#: zero-byte file upstream and is still requested.
REQUIRED_ASSETS = (
    "face_mesh.binarypb",
    "face_mesh_solution_packed_assets.data",
    "face_mesh_solution_packed_assets_loader.js",
    "face_mesh_solution_simd_wasm_bin.data",
    "face_mesh_solution_simd_wasm_bin.js",
    "face_mesh_solution_simd_wasm_bin.wasm",
    "face_mesh_solution_wasm_bin.js",
    "face_mesh_solution_wasm_bin.wasm",
)


class AssetsUnavailable(RuntimeError):
    """The source directory is absent or incomplete, and says what to do."""


@dataclass(frozen=True)
class Copied:
    """One file the run looked at. `written` is False when it was already right."""

    name: str
    size: int
    written: bool


def human(size: int) -> str:
    if size >= 1_000_000:
        return f"{size / 1_000_000:.1f} MB"
    if size >= 1_000:
        return f"{size / 1_000:.1f} kB"
    return f"{size} B"


def copy_assets(source: pathlib.Path, dest: pathlib.Path) -> list[Copied]:
    """Copy `source` into `dest`, skipping files that are already identical.

    Returns one `Copied` per file in the source directory. Raises
    `AssetsUnavailable` - before creating anything - if the source is missing or
    is missing one of `REQUIRED_ASSETS`, so an interrupted or partial install
    can never leave behind a half-filled directory that a later run, or a later
    test, mistakes for a good one.
    """
    if not source.is_dir():
        raise AssetsUnavailable(
            f"WebGazer's MediaPipe assets are not installed: {source} does not exist.\n"
            "Run `npm install` first - webgazer ships them in its own dist/ - then "
            "run this script again."
        )

    present = {path.name: path for path in sorted(source.iterdir()) if path.is_file()}
    missing = [name for name in REQUIRED_ASSETS if name not in present]
    if missing:
        raise AssetsUnavailable(
            f"{source} is missing {len(missing)} file(s) the MediaPipe runtime "
            f"fetches at startup: {', '.join(missing)}.\n"
            "Reinstall webgazer (`npm install`) - copying an incomplete set would "
            'produce 404s in the browser and "z2 is not a function".'
        )

    dest.mkdir(parents=True, exist_ok=True)

    results: list[Copied] = []
    for name, path in present.items():
        target = dest / name
        # shallow=False reads both files. That is ~33 MB per run and a fraction
        # of a second, and it is what makes a truncated copy - an interrupted
        # first run - repair itself instead of passing a size check.
        same = target.is_file() and filecmp.cmp(path, target, shallow=False)
        if not same:
            shutil.copy2(path, target)
        results.append(Copied(name=name, size=path.stat().st_size, written=not same))
    return results


def main() -> int:
    try:
        copied = copy_assets(SOURCE, DEST)
    except AssetsUnavailable as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1

    written = 0
    total = 0
    for item in copied:
        total += item.size
        written += 1 if item.written else 0
        verb = "copy" if item.written else "ok  "
        print(f"{verb} {(DEST / item.name).relative_to(ROOT)}  {human(item.size)}")

    print(
        f"{len(copied)} file(s), {human(total)} in {DEST.relative_to(ROOT)} "
        f"({written} copied, {len(copied) - written} already current)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
