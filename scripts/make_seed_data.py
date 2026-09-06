"""Generate the seed planogram, variants and persona files.

Run once after cloning:  python scripts/make_seed_data.py
Writes:
  data/planograms/demo_aisle.json   3 bays x 5 shelves, 24 SKUs, 3 ad slots
  data/variants/A.json B.json C.json D.json
  data/personas/*.json
  web/public/textures/sku_NNN.png            one pack face per SKU
  web/public/textures/ad_N.png               the cut the store loads today
  web/public/textures/ad_N_<fixture>.png     one cut per ad fixture aspect
                                    (all `.png`s need Pillow; skipped with a
                                     warning if it is absent)

The texture half of this file is the only place in the repository that decides
what a shopper can actually read off the shelf, so the sizing maths and the
reason for every number lives here rather than in a comment in the browser.
Nothing under `sim/` or `analytics/` opens a texture: a change down here cannot
move a measured number, only what the two panels see.
"""
from __future__ import annotations

import dataclasses
import json
import math
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]

CATEGORIES = ["chips", "cola", "biscuits", "nuts"]
BRANDS = ["Crunch", "Zapp", "Nimbus", "Orchid"]
LEVELS = ["top", "above_eye", "eye", "below_eye", "bottom"]
SHELF_HEIGHTS = [1.70, 1.45, 1.20, 0.85, 0.40]
LEVEL_BY_INDEX = ["top", "above_eye", "eye", "below_eye", "bottom"]

# Lab colours, one per brand, spread apart so colour contrast is meaningful
BRAND_LAB = {
    "Crunch": [62.0, 48.0, 51.0],
    "Zapp": [55.0, -30.0, 40.0],
    "Nimbus": [70.0, 5.0, -45.0],
    "Orchid": [45.0, 55.0, -20.0],
}
BRAND_RGB = {
    "Crunch": (214, 78, 51),
    "Zapp": (58, 158, 82),
    "Nimbus": (60, 110, 205),
    "Orchid": (150, 60, 160),
}

N_SKUS = 24                        # 3 bays x 8 filled positions

# Per bay: 8 SKUs over 5 shelves. Eye level keeps slot P2 free in every bay so a
# "move to eye level" patch always has a valid target. 3 bays x 8 = 24 SKUs.
FILL_PER_SHELF = [2, 2, 1, 2, 1]   # top, above_eye, eye, below_eye, bottom
SLOTS_PER_SHELF = 2
SLOT_WIDTH_M = 0.50
# Every slot is this tall, which is also the height of the plane a pack texture
# is stretched onto. The pack lettering below is sized against it.
SLOT_HEIGHT_M = 0.22


def build_skus():
    skus = []
    for i in range(N_SKUS):
        brand = BRANDS[i % len(BRANDS)]
        category = CATEGORIES[(i // 2) % len(CATEGORIES)]
        sku_id = f"SKU_{i+1:03d}"
        skus.append({
            "sku_id": sku_id,
            "name": f"{brand} {category.title()} {100 + 10 * (i % 5)}g",
            "brand": brand,
            "category": category,
            "price": round(25.0 + 5.0 * (i % 7), 2),
            "promo": i % 9 == 0,
            "texture_url": f"/textures/{sku_id.lower()}.png",
            "color_lab": BRAND_LAB[brand],
        })
    return skus


def build_planogram(skus):
    bays = []
    sku_iter = iter(skus)
    for b in range(3):
        bay_id = f"B{b+1}"
        shelves = []
        for s in range(5):
            shelf_id = f"{bay_id}S{s+1}"
            slots = []
            for p in range(SLOTS_PER_SHELF):
                filled = p < FILL_PER_SHELF[s]
                sku = next(sku_iter, None) if filled else None
                slots.append({
                    "slot_id": f"{shelf_id}P{p+1}",
                    "sku_id": sku["sku_id"] if sku else None,
                    "facings": (2 + ((b + s + p) % 3)) if sku else 0,
                    "x_m": round(0.05 + p * 0.55, 3),
                    "width_m": SLOT_WIDTH_M,
                    "height_m": SLOT_HEIGHT_M,
                })
            shelves.append({
                "shelf_id": shelf_id,
                "height_m": SHELF_HEIGHTS[s],
                "level": LEVEL_BY_INDEX[s],
                "slots": slots,
            })
        ad_slots = []
        if b == 0:
            ad_slots.append({"ad_slot_id": "B1_TALKER", "type": "shelf_talker",
                             "attached_to": "B1S3", "x_m": 0.40, "width_m": 0.30, "creative_id": None})
        if b == 1:
            ad_slots.append({"ad_slot_id": "B2_DECAL", "type": "floor_decal",
                             "attached_to": "B2", "x_m": 0.30, "width_m": 0.60, "creative_id": None})
        if b == 2:
            ad_slots.append({"ad_slot_id": "B3_ENDCAP", "type": "endcap_header",
                             "attached_to": "B3", "x_m": 0.20, "width_m": 0.80, "creative_id": "AD_1"})
        bays.append({
            "bay_id": bay_id,
            "type": "endcap" if b == 2 else "shelf",
            "width_m": 1.2,
            "height_m": 1.8,
            "station": {"camera_pos": [b * 1.5, 1.5, 2.2], "look_at": [b * 1.5, 1.1, 0.0]},
            "shelves": shelves,
            "ad_slots": ad_slots,
        })

    return {
        "planogram_id": "demo_aisle",
        "name": "Demo snacks aisle",
        "source": "manual",
        "bays": bays,
        "skus": skus,
        "creatives": [
            {"creative_id": "AD_1", "brand": "Crunch", "texture_url": "/textures/ad_1.png"},
            {"creative_id": "AD_2", "brand": "Zapp", "texture_url": "/textures/ad_2.png"},
        ],
    }


VARIANTS = [
    {
        "variant_id": "A",
        "base_planogram_id": "demo_aisle",
        "name": "Baseline",
        "patches": [],
    },
    {
        "variant_id": "B",
        "base_planogram_id": "demo_aisle",
        "name": "Focal SKU moved to eye level (known effect)",
        # SKU_008 sits on the bottom shelf of bay 1 in the baseline.
        # B1S3P2 is the deliberately free eye-level slot. This is the known effect
        # both panels must recover: attention on SKU_008 should rise sharply.
        "patches": [{"op": "move_sku", "sku_id": "SKU_008", "to_slot_id": "B1S3P2"}],
    },
    {
        "variant_id": "C",
        "base_planogram_id": "demo_aisle",
        "name": "Ad creative moved to the bay 1 shelf talker",
        "patches": [
            {"op": "set_ad_creative", "ad_slot_id": "B3_ENDCAP", "creative_id": None},
            {"op": "set_ad_creative", "ad_slot_id": "B1_TALKER", "creative_id": "AD_1"},
        ],
    },
    {
        "variant_id": "D",
        "base_planogram_id": "demo_aisle",
        "name": "Control arm - no ad creative anywhere",
        # A, B and C all carry AD_1 -- C only relocates it -- so before D there
        # was no unexposed arm anywhere in the data and no Brand Lift was
        # possible. D is A with every ad slot blanked, so it controls the ad and
        # nothing else. The slots survive with creative_id: null, mirroring the
        # rule for empty shelf positions.
        "patches": [
            {"op": "set_ad_creative", "ad_slot_id": "B1_TALKER", "creative_id": None},
            {"op": "set_ad_creative", "ad_slot_id": "B2_DECAL", "creative_id": None},
            {"op": "set_ad_creative", "ad_slot_id": "B3_ENDCAP", "creative_id": None},
        ],
    },
]

PERSONAS = [
    {"persona_id": "mission", "archetype": "mission", "share_of_population": 0.35,
     "description": "Comes with a list, time-pressed, low exploration, buys the first acceptable match."},
    {"persona_id": "browser", "archetype": "browser", "share_of_population": 0.25,
     "description": "No fixed list, high exploration, long dwell, open to promotions and new packs."},
    {"persona_id": "loyalist", "archetype": "loyalist", "share_of_population": 0.25,
     "description": "Strong affinity to one brand, ignores competitors, low price sensitivity."},
    {"persona_id": "switcher", "archetype": "switcher", "share_of_population": 0.15,
     "description": "Price and promotion driven, compares options, switches brand readily."},
]


# ===========================================================================
# Textures
# ===========================================================================
#
# How big a letter ends up on the shopper's screen
# -----------------------------------------------------------------------
# A texture pixel is not a screen pixel, and the ratio between them is brutal.
# Every station camera sits at z = 2.2 (see `build_planogram`) looking at a bay
# whose shelf face is at z = 0.2 -- geometry.ts's `FRONT_Z`, half the 0.4 m bay
# depth -- through the 50 degree vertical fov `CAMERA_FOV` declares. So the
# camera sees a 1.865 m tall window onto the shelf face, and on a 900 px canvas
# that is 483 screen pixels per metre.
#
# Follow a pack through that. Its texture is 384 px tall and it is stretched
# onto a slot 0.22 m tall, so one texture pixel is 0.276 screen pixels
# vertically. Pillow's default bitmap face has a cap height around 6 px, which
# lands at 1.7 screen pixels: the "grey smear" this module used to produce.
# Sizes below are chosen against that arithmetic, not by eye.
STATION_CAMERA_Z_M = 2.2
SHELF_FACE_Z_M = 0.2
CAMERA_FOV_DEG = 50.0
# The reference canvas the legibility floor is quoted against: a 1600x900
# laptop, the smaller of the screens this demo gets recorded on. Bigger screens
# only make the type larger.
REFERENCE_VIEWPORT_H_PX = 900
# Cap height in screen pixels below which antialiased type on a lit, textured
# 3D plane stops being reliably readable. Everything drawn on a fixture that
# stands upright has to clear this.
LEGIBLE_CAP_HEIGHT_PX = 8.0

# --- Pack faces ------------------------------------------------------------
PACK_W, PACK_H = 256, 384
PACK_BORDER_INSET = 12             # where the white border rectangle is drawn
PACK_BORDER_WIDTH = 6              # Pillow strokes a rectangle outline inward
PACK_PAD = 8                       # breathing room between stroke and lettering
NAME_MAX_SIZE = 72
NAME_MIN_SIZE = 24
NAME_MAX_LINES = 3                 # every SKU name in this planogram is 3 words
LINE_GAP_FRACTION = 0.12           # leading, as a fraction of the line's ink

# --- Ad sheets -------------------------------------------------------------
# The headline is two lines -- brand, then the offer -- rather than one. A
# single "CRUNCH - SAVE TODAY" line is nineteen characters competing for one
# fixture width, and on the 0.30 m shelf talker that caps the type at about
# 8 screen pixels however well it is fitted. Split in two, the longest line is
# ten characters and the same fixture carries roughly twice the cap height.
AD_HEADLINE_TAIL = "SAVE TODAY"
AD_CANVAS_H = 256                  # width follows from the fixture's aspect
AD_MAX_SIZE = 220
AD_MIN_SIZE = 24
# Fraction of the sheet width the widest headline line should occupy. The bug
# being fixed here is a headline covering 19% of its own sheet, which no
# fixture size can rescue.
AD_WIDTH_FRACTION = 0.80
AD_BORDER_FRACTION = 0.03          # border inset, stroke and padding, each
# Which cut `ad_N.png` itself gets when no ad slot in the base planogram books
# that creative. AdSlot.tsx loads `creative.texture_url` verbatim, so the plain
# file is the one the store actually shows; the endcap header is the largest
# fixture and the one the baseline books, which makes it the safe default.
AD_DEFAULT_FIXTURE = "endcap_header"

# Height of each ad fixture's plane in metres, mirroring web/src/store/
# geometry.ts: AD_HEADER_HEIGHT_M, AD_TALKER_HEIGHT_M and FLOOR_DECAL_DEPTH_M.
# The widths are not constants over there -- `adSlotSize` reads each ad slot's
# own `width_m` out of the planogram -- so `ad_fixture_planes` below reads them
# from the planogram document rather than duplicating them here.
AD_FIXTURE_HEIGHT_M = {
    "endcap_header": 0.2,
    "shelf_talker": 0.1,
    "floor_decal": 0.3,
}


@dataclasses.dataclass(frozen=True)
class TextLine:
    """One laid-out line: the ink box it will occupy, and the font that draws it.

    `x`/`y` are the top-left of the *ink*, not of the glyph cell, so a test can
    assert containment in a border box without knowing anything about font
    metrics -- and so `draw_line` can place the ink exactly where the layout
    said it would go.
    """
    text: str
    font: object
    x: int
    y: int
    w: int
    h: int
    role: str


def load_font(size):
    """Pillow's own bundled Aileron face at a real size.

    `ImageFont.load_default()` with no `size` returns a fixed ~8 px bitmap font
    and is what every `ImageDraw.text()` call in this module used to fall back
    to. Passing `size` (Pillow >= 10.1; we pin 11.1.0) returns a FreeType font
    instead, from a face Pillow ships itself -- so this needs no font asset in
    the repository and no new dependency.
    """
    from PIL import ImageFont

    return ImageFont.load_default(size=size)


def ink_box(font, text):
    """(x0, y0, w, h) of the ink `text` puts down, relative to the draw origin."""
    x0, y0, x1, y1 = font.getbbox(text)
    return x0, y0, x1 - x0, y1 - y0


def draw_line(draw, line, fill):
    """Draw a laid-out line so its ink lands exactly on the box the layout gave it."""
    x0, y0, _, _ = ink_box(line.font, line.text)
    draw.text((line.x - x0, line.y - y0), line.text, font=line.font, fill=fill)


def station_visible_height_m():
    """Height of the shelf face the station camera can see, in metres."""
    throw = STATION_CAMERA_Z_M - SHELF_FACE_Z_M
    return 2.0 * throw * math.tan(math.radians(CAMERA_FOV_DEG / 2.0))


def screen_cap_height_px(ink_h_px, texture_h_px, plane_h_m,
                         viewport_h_px=REFERENCE_VIEWPORT_H_PX):
    """Turn a height in texture pixels into a height on the shopper's screen."""
    px_per_m = viewport_h_px / station_visible_height_m()
    return ink_h_px / texture_h_px * plane_h_m * px_per_m


def wrap_to_width(font, text, max_width, max_lines):
    """Greedy word wrap, or None if `text` cannot be broken to fit.

    Returns None rather than dropping or hyphenating a word: a pack that shows
    "Crunch Biscuits" where the SKU is "Crunch Biscuits 100g" is a quieter
    failure than no pack at all, and the caller's job is to try a smaller size.
    """
    lines = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and ink_box(font, candidate)[2] > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
        if ink_box(font, current)[2] > max_width:
            return None                      # a single word that will not fit
    if current:
        lines.append(current)
    return lines if 0 < len(lines) <= max_lines else None


def stack(lines, font, left, top, box_width, role):
    """Centre `lines` horizontally in `box_width` and stack them from `top` down."""
    laid = []
    y = top
    for text in lines:
        _, _, w, h = ink_box(font, text)
        laid.append(TextLine(
            text=text,
            font=font,
            x=int(round(left + (box_width - w) / 2)),
            y=int(round(y)),
            w=w,
            h=h,
            role=role,
        ))
        y += h * (1.0 + LINE_GAP_FRACTION)
    return laid


def lay_block(lines, font, box, top, role):
    """Lay `lines` out centred in `box` from `top` down, or None if they overflow.

    Returning None rather than clipping is what lets the size search below
    treat "does not fit" as an ordinary answer.
    """
    if lines is None:
        return None
    left, _, right, bottom = box
    box_width = right - left
    laid = stack(lines, font, left, top, box_width, role)
    last = laid[-1]
    if last.y + last.h > bottom or any(ln.w > box_width for ln in laid):
        return None
    return laid


def largest_font_that_fits(fits, max_size, min_size, what):
    """The biggest integer font size for which `fits(font)` holds.

    Searching downward, rather than hard-coding a size, is the point: a size
    typed in once stops being checked the moment a word changes, and the word
    that decides this one -- "Biscuits" -- is a category name that could be
    edited by somebody who never opens this file.
    """
    for size in range(max_size, min_size - 1, -1):
        font = load_font(size)
        if fits(font):
            return font
    raise ValueError(f"no font size between {min_size} and {max_size} fits {what}")


def pack_text_box():
    """The usable rectangle inside the pack's white border: (left, top, right, bottom).

    Pillow strokes `rectangle(..., width=n)` *inward* from the given bounds, so
    the border eats `PACK_BORDER_INSET + PACK_BORDER_WIDTH` on every side
    before the padding is taken off.
    """
    edge = PACK_BORDER_INSET + PACK_BORDER_WIDTH + PACK_PAD
    return edge, edge, PACK_W - edge, PACK_H - edge


def centre_vertically(lines, box):
    """Shift a laid-out block down so it sits centred in `box`."""
    top = min(line.y for line in lines)
    height = max(line.y + line.h for line in lines) - top
    dy = int(round(box[1] + (box[3] - box[1] - height) / 2 - top))
    return [dataclasses.replace(line, y=line.y + dy) for line in lines]


def pack_name_block(sku, font):
    """This SKU's name, wrapped and stacked at `font`, or None if it will not fit."""
    box = pack_text_box()
    box_width = box[2] - box[0]
    return lay_block(
        wrap_to_width(font, sku["name"], box_width, NAME_MAX_LINES),
        font, box, box[1], "name",
    )


def pack_name_font(skus):
    """One name size for the whole shelf: the biggest that fits *every* name.

    Fitted per SKU instead, this planogram spreads from 58 to 72 -- "Zapp
    Chips 120g" a quarter larger than "Crunch Biscuits 140g", and only that
    close because `NAME_MAX_SIZE` caps it -- purely because its longest word
    is five letters and the other's is eight. Packs on one shelf at visibly
    different type sizes read as a rendering bug, and worse, they would hand
    the short-named SKUs a legibility advantage that exists in neither the
    planogram nor `sim/saliency.py`: a confound invented by a texture script.
    """
    return largest_font_that_fits(
        lambda font: all(pack_name_block(sku, font) is not None for sku in skus),
        NAME_MAX_SIZE, NAME_MIN_SIZE, "every SKU name",
    )


def pack_layouts(skus):
    """sku_id -> the lines to letter onto that pack: its full name, wrapped.

    This is the point of the fix. Before it, a pack showed its brand and a
    synthetic "SKU 001", so all six packs of a brand were identical on the
    shelf and no shopper -- real or synthetic -- could tell them apart.

    There is no separate brand kicker above the name, and that is deliberate:
    `build_skus` composes every name as "{brand} {category} {size}g", so the
    brand is already the name's own first word. A kicker printed "Crunch"
    directly above "Crunch / Chips / 100g", which reads as a rendering bug and
    spent a third of the pack face saying nothing. The brand still leads the
    pack, in the brand's own colour, as line one.
    """
    box = pack_text_box()
    font = pack_name_font(skus)
    return {
        sku["sku_id"]: centre_vertically(pack_name_block(sku, font), box)
        for sku in skus
    }


def ad_canvas_size(width_m, height_m):
    """Sheet dimensions for a fixture plane, at that plane's own aspect ratio.

    One 3.2:1 sheet used to be stretched onto a 4:1 endcap header, a 3:1 shelf
    talker and a 2:1 floor decal, which distorted the type on all three.
    """
    return int(round(AD_CANVAS_H * width_m / height_m)), AD_CANVAS_H


def ad_border_edge(canvas_h):
    """The ad sheet's border inset, which is also its stroke width and its padding.

    Scaled off the canvas height rather than fixed, because these sheets are
    cut at three different sizes and a fixed 8 px border is a different weight
    on each of them.
    """
    return int(round(AD_BORDER_FRACTION * canvas_h))


def ad_text_box(canvas_w, canvas_h):
    """The usable rectangle inside an ad sheet's border, same rule as the packs.

    Inset, then the stroke (Pillow strokes a rectangle outline inward), then
    the padding -- three edges in from every side.
    """
    edge = 3 * ad_border_edge(canvas_h)
    return edge, edge, canvas_w - edge, canvas_h - edge


def ad_layout(brand, width_m, height_m):
    """The two headline lines for one creative on one fixture."""
    canvas_w, canvas_h = ad_canvas_size(width_m, height_m)
    box = ad_text_box(canvas_w, canvas_h)
    lines = [brand.upper(), AD_HEADLINE_TAIL]
    # Width is capped at the target rather than at the border, so the headline
    # lands at ~80% of the sheet rather than running right out to the margin.
    target_width = AD_WIDTH_FRACTION * canvas_w

    def fits(font):
        if any(ink_box(font, text)[2] > target_width for text in lines):
            return False
        return lay_block(lines, font, box, box[1], "headline") is not None

    font = largest_font_that_fits(fits, AD_MAX_SIZE, AD_MIN_SIZE,
                                  f"a {canvas_w}x{canvas_h} ad sheet")
    return centre_vertically(lay_block(lines, font, box, box[1], "headline"), box)


def ad_fixture_planes(planogram):
    """Fixture type -> (width_m, height_m) of the plane its sheet is drawn on.

    Widths come from the planogram's own ad slots so a sheet can never be cut
    to an aspect ratio the store does not actually render it at.
    """
    planes = {}
    for bay in planogram["bays"]:
        for ad in bay["ad_slots"]:
            plane = (ad["width_m"], AD_FIXTURE_HEIGHT_M[ad["type"]])
            seen = planes.setdefault(ad["type"], plane)
            if seen != plane:
                raise ValueError(
                    f"two {ad['type']} slots want different sheet aspects: "
                    f"{seen} and {plane}. Cut the sheets per ad slot, not per type."
                )
    return planes


def booked_fixtures(planogram):
    """creative_id -> fixture type, for creatives the base planogram books."""
    return {
        ad["creative_id"]: ad["type"]
        for bay in planogram["bays"]
        for ad in bay["ad_slots"]
        if ad["creative_id"] is not None
    }


def make_textures(skus, planogram, out_dir=None):
    """Draw one pack face per SKU and one ad sheet per creative per fixture."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("! Pillow not installed - skipping textures. pip install pillow, then rerun.")
        return
    out = Path(out_dir) if out_dir is not None else ROOT / "web" / "public" / "textures"
    out.mkdir(parents=True, exist_ok=True)

    layouts = pack_layouts(skus)
    for sku in skus:
        img = Image.new("RGB", (PACK_W, PACK_H), BRAND_RGB[sku["brand"]])
        d = ImageDraw.Draw(img)
        d.rectangle(
            [PACK_BORDER_INSET, PACK_BORDER_INSET,
             PACK_W - PACK_BORDER_INSET, PACK_H - PACK_BORDER_INSET],
            outline=(255, 255, 255), width=PACK_BORDER_WIDTH,
        )
        for line in layouts[sku["sku_id"]]:
            draw_line(d, line, (255, 255, 255))
        # The filename comes from the planogram's own texture_url, so a renamed
        # SKU texture cannot 404 in the browser while this script stays quiet.
        img.save(out / PurePosixPath(sku["texture_url"]).name)

    planes = ad_fixture_planes(planogram)
    booked = booked_fixtures(planogram)
    for creative in planogram["creatives"]:
        stem = PurePosixPath(creative["texture_url"]).stem
        default = booked.get(creative["creative_id"], AD_DEFAULT_FIXTURE)
        for fixture, (width_m, height_m) in planes.items():
            canvas = ad_canvas_size(width_m, height_m)
            img = Image.new("RGB", canvas, BRAND_RGB[creative["brand"]])
            d = ImageDraw.Draw(img)
            edge = ad_border_edge(canvas[1])
            d.rectangle(
                [edge, edge, canvas[0] - edge, canvas[1] - edge],
                outline=(255, 255, 0), width=edge,
            )
            for line in ad_layout(creative["brand"], width_m, height_m):
                draw_line(d, line, (255, 255, 255))
            img.save(out / f"{stem}_{fixture}.png")
            if fixture == default:
                # AdSlot.tsx loads `creative.texture_url` verbatim, so this is
                # the file the store shows today. The per-fixture cuts beside
                # it are ready for whatever reads `ad.type` and picks one.
                img.save(out / f"{stem}.png")

    n_sheets = len(planogram["creatives"]) * len(planes)
    print(f"+ textures written to {out}  ({len(skus)} packs, {n_sheets} ad sheets)")


def write_json(path, doc):
    """Write a seed document with LF newlines, on every platform.

    `Path.write_text` translates "\\n" to `os.linesep`, so on Windows this
    script rewrote all thirteen data files with CRLF and every one of them
    showed as modified in `git status` with an empty `git diff`. The repository
    root's `.gitattributes` stops git being confused by that; writing LF stops
    it happening at all, so the working tree matches what is committed.
    """
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8", newline="\n")


def main():
    skus = build_skus()
    pg = build_planogram(skus)

    (ROOT / "data" / "planograms").mkdir(parents=True, exist_ok=True)
    (ROOT / "data" / "variants").mkdir(parents=True, exist_ok=True)
    (ROOT / "data" / "personas").mkdir(parents=True, exist_ok=True)

    p = ROOT / "data" / "planograms" / "demo_aisle.json"
    write_json(p, pg)
    print(f"+ {p}  ({len(pg['bays'])} bays, {len(skus)} SKUs)")

    for v in VARIANTS:
        vp = ROOT / "data" / "variants" / f"{v['variant_id']}.json"
        write_json(vp, v)
        print(f"+ {vp}")

    for persona in PERSONAS:
        pp = ROOT / "data" / "personas" / f"{persona['persona_id']}.json"
        write_json(pp, persona)
        print(f"+ {pp}")

    total_share = sum(x["share_of_population"] for x in PERSONAS)
    assert abs(total_share - 1.0) < 1e-9, f"persona shares must sum to 1, got {total_share}"

    make_textures(skus, pg)
    print("\nSeed data complete.")


if __name__ == "__main__":
    main()
