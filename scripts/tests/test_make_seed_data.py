"""Tests for the texture half of scripts/make_seed_data.py (W3).

The rule they defend: **a pack on the shelf carries its own SKU's real name,
and an ad fixture carries its own headline, at a size that survives the shrink
from texture pixels down to a facing a few centimetres wide.**

That rule was broken in two ways at once and both were invisible from Python.

  1. `make_textures()` never received the SKU list, so it could not print a
     pack's `name`. It printed the brand plus a synthetic "SKU 001" id, which
     is not a thing any shopper reads off a shelf, and which made every pack of
     a brand identical. The whole claim of this project is that both panels
     shop the *same* shelf; a shelf where the packs are unidentifiable is not
     a shelf either panel can be said to have shopped.

  2. Every `ImageDraw.text(...)` call omitted `font=`, which silently selects
     Pillow's fixed ~8 px bitmap face. On a 256x384 pack rendered into a facing
     roughly 0.12 m wide that is about two screen pixels of type. Nothing
     raised. Nothing looked broken in the generator. It was only visible in the
     browser, as a grey smear.

So these tests assert *rendered geometry*, measured with Pillow, rather than
the font size constants themselves. A future edit that halves a size, widens a
word or narrows a border box has to keep the type legible on screen or turn
one of these red.

Nothing here touches `sim/` or `analytics/`: a texture is never read by the
simulator, the saliency model or the metrics, so none of this can move a
measured number. The seed JSON these tests also cover (LF newlines) is the
one place where this script *can* move data, which is why that is pinned too.
"""
import json
import math
import pathlib
import sys

import pytest
from PIL import Image, ImageFont

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts import make_seed_data as seed  # noqa: E402


# ---------------------------------------------------------------------------
# The plane each texture is drawn on, taken from web/src/store/geometry.ts.
#
# Repeated here on purpose: Python cannot import TypeScript, so if these ever
# drift from geometry.ts the aspect-ratio test below is the thing that has to
# be updated by hand, and it is better that a human sees the two numbers side
# by side than that the generator quietly guesses.
#
#   AD_HEADER_HEIGHT_M = 0.2    AD_TALKER_HEIGHT_M = 0.1
#   FLOOR_DECAL_DEPTH_M = 0.3   (a floor decal's "height" is a depth along +z)
#
# Widths are not constants over there: `adSlotSize` uses the ad slot's own
# `width_m` straight out of the planogram, which is why the generator reads
# them from the planogram document rather than from a table like this one.
# ---------------------------------------------------------------------------
GEOMETRY_TS_FIXTURE_HEIGHT_M = {
    "endcap_header": 0.2,
    "shelf_talker": 0.1,
    "floor_decal": 0.3,
}


@pytest.fixture(scope="module")
def skus():
    return seed.build_skus()


@pytest.fixture(scope="module")
def planogram(skus):
    return seed.build_planogram(skus)


@pytest.fixture(scope="module")
def pack_layouts(skus):
    return seed.pack_layouts(skus)


def cap_height_px(font):
    """Cap height of a font, by definition: the ink height of a capital H."""
    box = font.getbbox("H")
    return box[3] - box[1]


def test_reference_projection_matches_the_station_camera():
    """The screen-size maths below is only worth anything if it is the real one.

    `build_planogram` puts every station camera at z = 2.2 looking at the bay,
    and geometry.ts renders the shelf face at FRONT_Z = BAY_DEPTH_M / 2 = 0.2
    through a 50 degree vertical fov. That is a 2.0 m throw and a 1.865 m tall
    window onto the shelf; everything else here is that number divided up.
    """
    camera_z = seed.build_planogram(seed.build_skus())["bays"][0]["station"]["camera_pos"][2]
    assert camera_z == seed.STATION_CAMERA_Z_M

    expected = 2.0 * (seed.STATION_CAMERA_Z_M - seed.SHELF_FACE_Z_M) * math.tan(
        math.radians(seed.CAMERA_FOV_DEG / 2.0)
    )
    assert seed.station_visible_height_m() == pytest.approx(expected)
    assert seed.station_visible_height_m() == pytest.approx(1.8652, abs=1e-3)


# ---------------------------------------------------------------------------
# Packs
# ---------------------------------------------------------------------------

def test_every_pack_is_lettered_with_its_own_sku_name(skus, pack_layouts):
    """The pack shows this SKU's full name -- not a synthetic id -- brand first.

    Asserting on the joined lines rather than on a substring catches the two
    ways a word-wrapper fails quietly: dropping the word that did not fit, and
    hyphenating or eliding one that did.
    """
    assert len(skus) == seed.N_SKUS
    assert len(pack_layouts) == seed.N_SKUS
    for sku in skus:
        lines = pack_layouts[sku["sku_id"]]
        drawn = " ".join(line.text for line in lines)
        assert drawn == sku["name"], sku["sku_id"]
        # The brand is lettered on the pack as the name's own first line; a
        # separate kicker above it would print the same word twice.
        assert lines[0].text == sku["brand"], sku["sku_id"]
        assert sku["name"].startswith(f"{sku['brand']} ")


def test_pack_lettering_is_a_real_font_not_the_bitmap_default(skus, pack_layouts):
    """Guards the exact regression: `ImageDraw.text()` with no `font=`.

    Pillow's fallback is a fixed ~8 px bitmap face with no size attribute at
    all. Any line drawn with it fails both halves of this.
    """
    for sku in skus:
        for line in pack_layouts[sku["sku_id"]]:
            assert isinstance(line.font, ImageFont.FreeTypeFont), sku["sku_id"]
            assert line.font.size >= seed.NAME_MIN_SIZE


def test_pack_lettering_stays_inside_the_border_box(skus, pack_layouts):
    left, top, right, bottom = seed.pack_text_box()
    for sku in skus:
        for line in pack_layouts[sku["sku_id"]]:
            assert line.x >= left, (sku["sku_id"], line.text)
            assert line.x + line.w <= right, (sku["sku_id"], line.text)
            assert line.y >= top, (sku["sku_id"], line.text)
            assert line.y + line.h <= bottom, (sku["sku_id"], line.text)


def test_pack_name_size_is_the_largest_the_widest_word_allows(skus, pack_layouts):
    """The binding case is the widest *word*, not the longest name: names wrap.

    So this measures the widest word in the whole planogram -- "Biscuits", a
    category name somebody could edit without ever opening the generator --
    confirms it fits, and confirms one size larger would not. That pins the
    size to real glyph geometry instead of to a number in the source.
    """
    left, _, right, _ = seed.pack_text_box()
    box_width = right - left
    words = [word for sku in skus for word in sku["name"].split()]

    def widest(font):
        return max(seed.ink_box(font, word)[2] for word in words)

    chosen = {line.font for lines in pack_layouts.values() for line in lines
              if line.role == "name"}
    assert len(chosen) == 1, "one type size for the whole shelf, or packs differ"
    font = chosen.pop()

    assert widest(font) <= box_width
    assert widest(seed.load_font(font.size + 1)) > box_width


def test_pack_name_wraps_to_at_most_three_lines(skus, pack_layouts):
    for sku in skus:
        name_lines = [ln for ln in pack_layouts[sku["sku_id"]] if ln.role == "name"]
        assert 1 <= len(name_lines) <= seed.NAME_MAX_LINES, sku["sku_id"]


def test_pack_name_survives_the_shrink_onto_a_facing(skus, pack_layouts):
    """The legibility floor, justified rather than asserted as a magic number.

    A 384 px tall pack texture is mapped onto a slot 0.22 m tall, seen through
    the station camera's 1.865 m window. On a 900 px canvas -- the reference
    viewport, and the smaller of the laptop screens this demo is recorded on --
    that is 0.276 screen pixels per texture pixel vertically. The floor is 8 px
    of cap height: below that, antialiased type on a lit, textured plane stops
    being reliably readable, and the pre-fix bitmap face landed at about 1.7.
    """
    for sku in skus:
        for line in pack_layouts[sku["sku_id"]]:
            if line.role != "name":
                continue
            on_screen = seed.screen_cap_height_px(
                cap_height_px(line.font), seed.PACK_H, seed.SLOT_HEIGHT_M
            )
            assert on_screen >= seed.LEGIBLE_CAP_HEIGHT_PX, (sku["sku_id"], on_screen)


# ---------------------------------------------------------------------------
# Ad sheets
# ---------------------------------------------------------------------------

def test_ad_sheets_are_cut_to_each_fixture_aspect(planogram):
    """One 3.2:1 sheet stretched onto a 4:1 header and a 3:1 talker distorts both.

    The endcap header is 0.80 x 0.20 m, the shelf talker 0.30 x 0.10 m and the
    floor decal 0.60 x 0.30 m, so the three fixtures want 4:1, 3:1 and 2:1
    canvases. Every one of those widths comes out of the planogram, so this
    also catches an ad slot whose `width_m` moved without its sheet following.
    """
    planes = seed.ad_fixture_planes(planogram)
    assert set(planes) == set(GEOMETRY_TS_FIXTURE_HEIGHT_M)

    for fixture, (width_m, height_m) in planes.items():
        assert height_m == GEOMETRY_TS_FIXTURE_HEIGHT_M[fixture]
        canvas_w, canvas_h = seed.ad_canvas_size(width_m, height_m)
        assert canvas_w / canvas_h == pytest.approx(width_m / height_m, rel=0.01), fixture


def test_ad_headline_names_its_brand(planogram):
    for creative in planogram["creatives"]:
        for fixture, (width_m, height_m) in seed.ad_fixture_planes(planogram).items():
            lines = seed.ad_layout(creative["brand"], width_m, height_m)
            drawn = " ".join(line.text for line in lines)
            assert drawn == f"{creative['brand'].upper()} {seed.AD_HEADLINE_TAIL}", fixture


def test_ad_headline_fills_most_of_its_sheet(planogram):
    """The measured defect: 19 characters over 98 of 512 texture pixels, 19%.

    A headline that occupies a fifth of its own sheet can never be read at any
    fixture size, because the sheet is then scaled down to fit a 0.30 m talker.
    Filling roughly 80% of the width is what makes the fixture size the only
    thing that limits it.
    """
    for creative in planogram["creatives"]:
        for fixture, (width_m, height_m) in seed.ad_fixture_planes(planogram).items():
            canvas_w, canvas_h = seed.ad_canvas_size(width_m, height_m)
            lines = seed.ad_layout(creative["brand"], width_m, height_m)
            widest = max(line.w for line in lines)
            assert 0.70 <= widest / canvas_w <= 0.90, (creative["creative_id"], fixture)


def test_ad_headline_stays_inside_its_sheet(planogram):
    for creative in planogram["creatives"]:
        for fixture, (width_m, height_m) in seed.ad_fixture_planes(planogram).items():
            canvas_w, canvas_h = seed.ad_canvas_size(width_m, height_m)
            left, top, right, bottom = seed.ad_text_box(canvas_w, canvas_h)
            for line in seed.ad_layout(creative["brand"], width_m, height_m):
                assert line.x >= left, (fixture, line.text)
                assert line.x + line.w <= right, (fixture, line.text)
                assert line.y >= top, (fixture, line.text)
                assert line.y + line.h <= bottom, (fixture, line.text)


def test_upright_ad_headlines_survive_the_shrink_onto_their_fixture(planogram):
    """Same floor as the packs, for the two fixtures that stand up on screen.

    The floor decal is exempt and deliberately so: `isFlatAd` lays it in the
    ground plane, so its 0.30 m runs away from the camera rather than up the
    frame and foreshortens to a fraction of that on screen. There is no honest
    cap-height number for it from a fixed camera, and no variant in `data/`
    books a creative onto it -- only the optimizer ever proposes one.
    """
    planes = seed.ad_fixture_planes(planogram)
    for creative in planogram["creatives"]:
        for fixture, (width_m, height_m) in planes.items():
            if fixture == "floor_decal":
                continue
            _, canvas_h = seed.ad_canvas_size(width_m, height_m)
            for line in seed.ad_layout(creative["brand"], width_m, height_m):
                on_screen = seed.screen_cap_height_px(
                    cap_height_px(line.font), canvas_h, height_m
                )
                assert on_screen >= seed.LEGIBLE_CAP_HEIGHT_PX, (fixture, on_screen)


# ---------------------------------------------------------------------------
# The files that actually get written
# ---------------------------------------------------------------------------

def test_make_textures_writes_one_pack_per_sku_and_one_sheet_per_fixture(tmp_path, skus, planogram):
    """Every `texture_url` in the planogram must resolve to a file on disk.

    Deriving the filenames from the planogram's own `texture_url` values, here
    and in the generator, is what stops a renamed SKU texture from 404ing in
    the browser while every Python test stays green.
    """
    seed.make_textures(skus, planogram, out_dir=tmp_path)

    for sku in skus:
        path = tmp_path / pathlib.PurePosixPath(sku["texture_url"]).name
        assert path.is_file(), sku["sku_id"]
        with Image.open(path) as img:
            assert img.size == (seed.PACK_W, seed.PACK_H)

    planes = seed.ad_fixture_planes(planogram)
    for creative in planogram["creatives"]:
        stem = pathlib.PurePosixPath(creative["texture_url"]).stem
        default = tmp_path / f"{stem}.png"
        assert default.is_file(), creative["creative_id"]

        for fixture, (width_m, height_m) in planes.items():
            per_fixture = tmp_path / f"{stem}_{fixture}.png"
            assert per_fixture.is_file(), (creative["creative_id"], fixture)
            with Image.open(per_fixture) as img:
                assert img.size == seed.ad_canvas_size(width_m, height_m)


def test_the_default_ad_sheet_is_the_fixture_the_planogram_books(tmp_path, skus, planogram):
    """`data/` says AD_1 hangs on B3_ENDCAP, and AdSlot.tsx loads `creative.texture_url`.

    Until something reads `ad.type` and reaches for the per-fixture sheet, the
    plain `ad_1.png` is the one the store shows, so it has to be the endcap's
    own 4:1 cut rather than an arbitrary default.
    """
    seed.make_textures(skus, planogram, out_dir=tmp_path)
    booked = {
        ad["creative_id"]: ad["type"]
        for bay in planogram["bays"]
        for ad in bay["ad_slots"]
        if ad["creative_id"] is not None
    }
    assert booked["AD_1"] == "endcap_header"

    planes = seed.ad_fixture_planes(planogram)
    for creative in planogram["creatives"]:
        stem = pathlib.PurePosixPath(creative["texture_url"]).stem
        fixture = booked.get(creative["creative_id"], seed.AD_DEFAULT_FIXTURE)
        with Image.open(tmp_path / f"{stem}.png") as default:
            with Image.open(tmp_path / f"{stem}_{fixture}.png") as cut:
                assert default.size == cut.size
                assert default.tobytes() == cut.tobytes()


# ---------------------------------------------------------------------------
# The seed JSON
# ---------------------------------------------------------------------------

def test_seed_json_is_written_with_lf_newlines(tmp_path):
    """`Path.write_text` uses os.linesep, so on Windows this script rewrote every
    data file with CRLF and made all thirteen show as modified in `git status`
    with an empty `git diff`. `.gitattributes` fixes it for git; writing LF
    fixes it for whoever is looking at the file.
    """
    path = tmp_path / "seed.json"
    seed.write_json(path, {"variant_id": "A", "patches": []})
    raw = path.read_bytes()
    assert b"\r\n" not in raw
    assert b"\n" in raw
    assert json.loads(raw.decode("utf-8"))["variant_id"] == "A"
