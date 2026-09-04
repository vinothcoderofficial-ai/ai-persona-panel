"""Generate the seed planogram, variants and persona files.

Run once after cloning:  python scripts/make_seed_data.py
Writes:
  data/planograms/demo_aisle.json   3 bays x 5 shelves, 24 SKUs, 3 ad slots
  data/variants/A.json B.json C.json
  data/personas/*.json
  web/public/textures/*.png         (needs Pillow; skipped with a warning if absent)
"""
import json
import os
from pathlib import Path

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
                    "width_m": 0.50,
                    "height_m": 0.22,
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


def make_textures():
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("! Pillow not installed - skipping textures. pip install pillow, then rerun.")
        return
    out = ROOT / "web" / "public" / "textures"
    out.mkdir(parents=True, exist_ok=True)
    for i in range(N_SKUS):
        brand = BRANDS[i % len(BRANDS)]
        img = Image.new("RGB", (256, 384), BRAND_RGB[brand])
        d = ImageDraw.Draw(img)
        d.rectangle([12, 12, 244, 372], outline=(255, 255, 255), width=6)
        d.text((28, 40), brand, fill=(255, 255, 255))
        d.text((28, 70), f"SKU {i+1:03d}", fill=(255, 255, 255))
        img.save(out / f"sku_{i+1:03d}.png")
    for j, brand in enumerate(["Crunch", "Zapp"], start=1):
        img = Image.new("RGB", (512, 160), BRAND_RGB[brand])
        d = ImageDraw.Draw(img)
        d.rectangle([8, 8, 504, 152], outline=(255, 255, 0), width=8)
        d.text((32, 60), f"{brand.upper()} - SAVE TODAY", fill=(255, 255, 255))
        img.save(out / f"ad_{j}.png")
    print(f"+ textures written to {out}")


def main():
    skus = build_skus()
    pg = build_planogram(skus)

    (ROOT / "data" / "planograms").mkdir(parents=True, exist_ok=True)
    (ROOT / "data" / "variants").mkdir(parents=True, exist_ok=True)
    (ROOT / "data" / "personas").mkdir(parents=True, exist_ok=True)

    p = ROOT / "data" / "planograms" / "demo_aisle.json"
    p.write_text(json.dumps(pg, indent=2))
    print(f"+ {p}  ({len(pg['bays'])} bays, {len(skus)} SKUs)")

    for v in VARIANTS:
        vp = ROOT / "data" / "variants" / f"{v['variant_id']}.json"
        vp.write_text(json.dumps(v, indent=2))
        print(f"+ {vp}")

    for persona in PERSONAS:
        pp = ROOT / "data" / "personas" / f"{persona['persona_id']}.json"
        pp.write_text(json.dumps(persona, indent=2))
        print(f"+ {pp}")

    total_share = sum(x["share_of_population"] for x in PERSONAS)
    assert abs(total_share - 1.0) < 1e-9, f"persona shares must sum to 1, got {total_share}"

    make_textures()
    print("\nSeed data complete.")


if __name__ == "__main__":
    main()
