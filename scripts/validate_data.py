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


def variant_patch_errors(base: dict, variant: dict) -> list:
    """Return one message per patch reference that `resolve()` would refuse.

    This is the pre-flight twin of `api/app/resolve.py`, and the reason it
    exists separately is timing: resolve() runs when a variant is served, this
    runs over the committed files before anything is served, so a broken
    variant fails the build rather than a demo. The messages deliberately echo
    resolve()'s own wording -- when both fire on the same document they should
    read as one voice, not two opinions.

    The rule that had drifted, and the one thing to preserve when a new op is
    added: **ids are checked against the planogram as the patches have left
    it, walking them in patch order**, because that is exactly what resolve()
    does. `add_ad_slot` creates an ad_slot_id, so a `set_ad_creative` that
    follows it in the same variant is booking something that will exist. The
    older lint built its id set from the base planogram alone and had no
    `add_ad_slot` branch, so it failed the install-then-book pair that
    `_apply_add_ad_slot`'s docstring tells you to write, and it waved through
    the two things resolve() raises PatchError for -- an id that is already
    taken, and an `attached_to` that names no bay or shelf.

    Two sku sets, not one, and they are not interchangeable. `move_sku` moves
    a facing: resolve() hunts for the slot currently holding the sku, so the
    check is against the skus a shelf actually carries. `set_price` and
    `swap_texture` edit the catalogue entry, so those check `planogram["skus"]`.
    A sku in the catalogue and on no shelf is legal, may be repriced, and
    cannot be moved.

    Nothing creates a slot, a shelf or a bay, so those three sets are read once
    from the base and never updated.
    """
    sku_ids = {s["sku_id"] for s in base["skus"]}
    creative_ids = {c["creative_id"] for c in base["creatives"]} | {None}
    placed_sku_ids, slot_ids, bay_ids, shelf_ids = set(), set(), set(), set()
    ad_ids = set()
    for bay in base["bays"]:
        bay_ids.add(bay["bay_id"])
        for sh in bay["shelves"]:
            shelf_ids.add(sh["shelf_id"])
            for sl in sh["slots"]:
                slot_ids.add(sl["slot_id"])
                if sl["sku_id"] is not None:
                    placed_sku_ids.add(sl["sku_id"])
        for ad in bay["ad_slots"]:
            ad_ids.add(ad["ad_slot_id"])

    out = []
    for p in variant.get("patches", []):
        op = p["op"]
        if op == "move_sku":
            if p["to_slot_id"] not in slot_ids:
                out.append(f"move_sku: unknown to_slot_id {p['to_slot_id']!r}")
            if p["sku_id"] not in placed_sku_ids:
                out.append(
                    f"move_sku: unknown sku_id {p['sku_id']!r} "
                    "(not placed in any slot)"
                )
        elif op == "set_ad_creative":
            if p["ad_slot_id"] not in ad_ids:
                out.append(f"set_ad_creative: unknown ad_slot_id {p['ad_slot_id']!r}")
            if p["creative_id"] not in creative_ids:
                out.append(
                    f"set_ad_creative: unknown creative_id {p['creative_id']!r}"
                )
        elif op in ("swap_texture", "set_price"):
            if p["sku_id"] not in sku_ids:
                out.append(f"{op}: unknown sku_id {p['sku_id']!r}")
        elif op == "add_ad_slot":
            ad_slot_id = p["ad_slot_id"]
            if ad_slot_id in ad_ids:
                out.append(f"add_ad_slot: ad_slot_id {ad_slot_id!r} already exists")
            if p["attached_to"] not in bay_ids | shelf_ids:
                out.append(
                    f"add_ad_slot: unknown attached_to {p['attached_to']!r} "
                    "(no bay or shelf carries that id)"
                )
            # Registered even when `attached_to` was rejected above. The fixture
            # is what the author meant to install, and a later set_ad_creative
            # on it is not a second fault -- reporting it as one would send
            # whoever reads the build log to edit the wrong line.
            ad_ids.add(ad_slot_id)
        else:
            out.append(f"unknown patch op {op!r}")
    return out


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
    for f in sorted((ROOT / "data" / "variants").glob("*.json")):
        for message in variant_patch_errors(pg, json.loads(f.read_text())):
            print(f"FAIL {f.name}: {message}")
            errors += 1

    print(f"\n{checked} files checked, {errors} error(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
