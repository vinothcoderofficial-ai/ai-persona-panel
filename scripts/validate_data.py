"""Validate every data file against its schema. Run: python scripts/validate_data.py"""
import json
import sys
from pathlib import Path

from jsonschema import Draft7Validator

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "schemas"

CHECKS = [
    ("planogram.schema.json", ROOT / "data" / "planograms", "*.json"),
    ("variant.schema.json", ROOT / "data" / "variants", "*.json"),
    ("persona.schema.json", ROOT / "data" / "personas", "*.json"),
    ("policy.schema.json", ROOT / "data" / "cache" / "policies", "*.json"),
]


def main() -> int:
    errors = 0
    checked = 0
    for schema_name, folder, pattern in CHECKS:
        schema = json.loads((SCHEMAS / schema_name).read_text())
        validator = Draft7Validator(schema)
        for f in sorted(folder.glob(pattern)):
            data = json.loads(f.read_text())
            found = sorted(validator.iter_errors(data), key=lambda e: e.path)
            checked += 1
            if found:
                errors += len(found)
                print(f"FAIL {f.relative_to(ROOT)}")
                for e in found:
                    print(f"     {'/'.join(str(p) for p in e.path)}: {e.message}")
            else:
                print(f"ok   {f.relative_to(ROOT)}")

    # Referential integrity on planograms
    for f in sorted((ROOT / "data" / "planograms").glob("*.json")):
        pg = json.loads(f.read_text())
        sku_ids = {s["sku_id"] for s in pg["skus"]}
        creative_ids = {c["creative_id"] for c in pg["creatives"]}
        shelf_ids, bay_ids, slot_ids = set(), set(), set()
        for bay in pg["bays"]:
            bay_ids.add(bay["bay_id"])
            for sh in bay["shelves"]:
                shelf_ids.add(sh["shelf_id"])
                for sl in sh["slots"]:
                    slot_ids.add(sl["slot_id"])
                    if sl["sku_id"] is not None and sl["sku_id"] not in sku_ids:
                        print(f"FAIL {f.name}: slot {sl['slot_id']} -> unknown sku {sl['sku_id']}")
                        errors += 1
            for ad in bay["ad_slots"]:
                if ad["attached_to"] not in shelf_ids | bay_ids:
                    print(f"FAIL {f.name}: ad {ad['ad_slot_id']} -> unknown target {ad['attached_to']}")
                    errors += 1
                if ad["creative_id"] and ad["creative_id"] not in creative_ids:
                    print(f"FAIL {f.name}: ad {ad['ad_slot_id']} -> unknown creative {ad['creative_id']}")
                    errors += 1

    # Variant patches must reference things that exist
    pg = json.loads((ROOT / "data" / "planograms" / "demo_aisle.json").read_text())
    sku_ids = {s["sku_id"] for s in pg["skus"]}
    creative_ids = {c["creative_id"] for c in pg["creatives"]} | {None}
    slot_ids = {sl["slot_id"] for b in pg["bays"] for sh in b["shelves"] for sl in sh["slots"]}
    ad_ids = {a["ad_slot_id"] for b in pg["bays"] for a in b["ad_slots"]}
    for f in sorted((ROOT / "data" / "variants").glob("*.json")):
        v = json.loads(f.read_text())
        for p in v["patches"]:
            if p["op"] == "move_sku" and (p["sku_id"] not in sku_ids or p["to_slot_id"] not in slot_ids):
                print(f"FAIL {f.name}: move_sku references unknown sku/slot")
                errors += 1
            if p["op"] == "set_ad_creative" and (p["ad_slot_id"] not in ad_ids or p["creative_id"] not in creative_ids):
                print(f"FAIL {f.name}: set_ad_creative references unknown ad slot/creative")
                errors += 1
            if p["op"] in ("swap_texture", "set_price") and p["sku_id"] not in sku_ids:
                print(f"FAIL {f.name}: {p['op']} references unknown sku")
                errors += 1

    print(f"\n{checked} files checked, {errors} error(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
