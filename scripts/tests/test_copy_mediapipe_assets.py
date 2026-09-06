"""Tests for scripts/copy_mediapipe_assets.py, the step that makes the eye tracker start.

WebGazer 3.5.x drives MediaPipe's local-WASM FaceMesh runtime, which fetches
~17 MB of WASM, model and packed-asset files **from the host app's own web
root**. Nothing put them there, so every request 404ed, MediaPipe's script
injector swallowed the 404 (it resolves on `error` as well as on `load`), and
the next call landed on a placeholder object: "z2 is not a function". Every
webcam session degraded to `mode: "cursor_only"`, silently.

This script is the fix, and the properties these tests pin are the ones that
make it trustworthy as a setup step:

  * **It is complete.** Every file MediaPipe's `locateFile` asks for is copied,
    and a source that is missing one is an error rather than a partial copy that
    404s later in a much harder place to read.
  * **It fails loudly and actionably.** No node_modules means "run npm install",
    said in those words, not a stack trace ending in FileNotFoundError.
  * **It is idempotent.** `make setup` runs it on every fresh clone and on every
    re-run in a working tree that already has the files; the second run must
    change nothing and must still be able to repair a truncated file.
  * **Its required-file list matches what webgazer actually ships.** A list that
    drifts from the installed package is a list that stops being a check.
"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts import copy_mediapipe_assets as copier  # noqa: E402


def make_source(tmp_path: pathlib.Path, extra: dict[str, bytes] | None = None) -> pathlib.Path:
    """A stand-in for node_modules/webgazer/dist/mediapipe/face_mesh."""
    source = tmp_path / "source"
    source.mkdir()
    for name in copier.REQUIRED_ASSETS:
        # Distinct contents per file, so a copy that mixes two up is visible.
        (source / name).write_bytes(name.encode("utf-8"))
    for name, payload in (extra or {}).items():
        (source / name).write_bytes(payload)
    return source


def test_copies_every_required_asset(tmp_path):
    source = make_source(tmp_path)
    dest = tmp_path / "public" / "mediapipe" / "face_mesh"

    copied = copier.copy_assets(source, dest)

    assert sorted(item.name for item in copied) == sorted(copier.REQUIRED_ASSETS)
    for name in copier.REQUIRED_ASSETS:
        assert (dest / name).read_bytes() == name.encode("utf-8")
    assert all(item.written for item in copied)


def test_copies_the_rest_of_the_directory_too(tmp_path):
    """A future asset nobody has heard of still has to arrive.

    The required list is a floor, not a filter: MediaPipe decides at runtime
    which files it wants, and the licence metadata webgazer ships beside them
    (package.json says Apache-2.0) belongs next to the bytes it covers.
    """
    source = make_source(tmp_path, extra={"package.json": b"{}", "index.d.ts": b"declare"})
    dest = tmp_path / "public"

    copied = copier.copy_assets(source, dest)

    assert (dest / "package.json").read_bytes() == b"{}"
    assert (dest / "index.d.ts").read_bytes() == b"declare"
    assert len(copied) == len(copier.REQUIRED_ASSETS) + 2


def test_second_run_copies_nothing(tmp_path):
    source = make_source(tmp_path)
    dest = tmp_path / "public"

    copier.copy_assets(source, dest)
    again = copier.copy_assets(source, dest)

    assert again, "the second run must still report the files it checked"
    assert [item.name for item in again if item.written] == []


def test_a_truncated_asset_is_repaired(tmp_path):
    """17 MB of copying gets interrupted; the next run must not call it done."""
    source = make_source(tmp_path)
    dest = tmp_path / "public"
    copier.copy_assets(source, dest)

    victim = dest / "face_mesh_solution_wasm_bin.wasm"
    victim.write_bytes(b"")

    repaired = copier.copy_assets(source, dest)

    assert victim.read_bytes() == b"face_mesh_solution_wasm_bin.wasm"
    assert [item.name for item in repaired if item.written] == [
        "face_mesh_solution_wasm_bin.wasm"
    ]


def test_missing_source_says_what_to_run(tmp_path):
    with pytest.raises(copier.AssetsUnavailable) as excinfo:
        copier.copy_assets(tmp_path / "not-installed", tmp_path / "public")

    message = str(excinfo.value)
    assert "npm install" in message
    # Naming the directory it looked in is the difference between a person
    # fixing this in ten seconds and a person opening the script.
    assert "not-installed" in message


def test_incomplete_source_is_refused(tmp_path):
    source = make_source(tmp_path)
    (source / "face_mesh.binarypb").unlink()
    dest = tmp_path / "public"

    with pytest.raises(copier.AssetsUnavailable) as excinfo:
        copier.copy_assets(source, dest)

    assert "face_mesh.binarypb" in str(excinfo.value)
    # Nothing may be left half-copied for a later run to mistake for a good one.
    assert not dest.exists()


def test_required_list_matches_the_installed_webgazer():
    """The list is only a check while it agrees with the package it describes."""
    if not copier.SOURCE.is_dir():
        pytest.skip(f"{copier.SOURCE} is absent - run `npm install`")

    shipped = {path.name for path in copier.SOURCE.iterdir() if path.is_file()}
    assert set(copier.REQUIRED_ASSETS) <= shipped


def test_destination_is_the_directory_the_dev_server_serves():
    """web/public is Vite's static root, so /mediapipe/face_mesh is the URL."""
    assert copier.DEST == ROOT / "web" / "public" / "mediapipe" / "face_mesh"
    assert copier.SOURCE == ROOT / "node_modules" / "webgazer" / "dist" / "mediapipe" / "face_mesh"
