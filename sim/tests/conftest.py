"""Shared fixtures for the saliency and simulator tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft7Validator

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
SCHEMAS = ROOT / "schemas"

PERSONA_IDS = ("mission", "browser", "loyalist", "switcher")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def planogram() -> dict:
    """The committed demo aisle. Variant A has no patches, so this is variant A resolved."""
    return load_json(DATA / "planograms" / "demo_aisle.json")


@pytest.fixture(scope="session")
def policies() -> dict:
    return {p: load_json(DATA / "cache" / "policies" / f"{p}_demo_aisle.json") for p in PERSONA_IDS}


@pytest.fixture(scope="session")
def personas() -> dict:
    return {p: load_json(DATA / "personas" / f"{p}.json") for p in PERSONA_IDS}


@pytest.fixture(scope="session")
def policy_validator() -> Draft7Validator:
    return Draft7Validator(load_json(SCHEMAS / "policy.schema.json"))


@pytest.fixture(scope="session")
def simresult_validator() -> Draft7Validator:
    return Draft7Validator(load_json(SCHEMAS / "simresult.schema.json"))


def sku(sku_id: str, *, brand: str = "Crunch", category: str = "chips",
        price: float = 30.0, promo: bool = False,
        color_lab: tuple[float, float, float] = (60.0, 10.0, 10.0)) -> dict:
    return {
        "sku_id": sku_id,
        "name": f"{brand} {category} {sku_id}",
        "brand": brand,
        "category": category,
        "price": price,
        "promo": promo,
        "texture_url": f"/textures/{sku_id.lower()}.png",
        "color_lab": list(color_lab),
    }


def slot(slot_id: str, sku_id: str | None, *, facings: int = 3, x_m: float = 0.35,
         width_m: float = 0.5, height_m: float = 0.22) -> dict:
    return {
        "slot_id": slot_id,
        "sku_id": sku_id,
        "facings": facings,
        "x_m": x_m,
        "width_m": width_m,
        "height_m": height_m,
    }


def one_bay_planogram(shelves: list[dict], *, ad_slots: list[dict] | None = None,
                      skus: list[dict] | None = None, creatives: list[dict] | None = None) -> dict:
    """A single-bay planogram, so 'all else equal' comparisons are actually equal."""
    return {
        "planogram_id": "synthetic",
        "name": "Synthetic test bay",
        "source": "manual",
        "bays": [
            {
                "bay_id": "S1",
                "type": "shelf",
                "width_m": 1.2,
                "height_m": 1.8,
                "station": {"camera_pos": [0.0, 1.5, 2.2], "look_at": [0.0, 1.1, 0.0]},
                "shelves": shelves,
                "ad_slots": ad_slots or [],
            }
        ],
        "skus": skus or [],
        "creatives": creatives or [],
    }


def slot_categories(planogram: dict) -> dict:
    """slot_id -> category, for occupied slots only."""
    by_sku = {s["sku_id"]: s["category"] for s in planogram["skus"]}
    return {
        sl["slot_id"]: by_sku[sl["sku_id"]]
        for bay in planogram["bays"]
        for sh in bay["shelves"]
        for sl in sh["slots"]
        if sl["sku_id"] is not None
    }


def empty_slot_ids(planogram: dict) -> list[str]:
    return [
        sl["slot_id"]
        for bay in planogram["bays"]
        for sh in bay["shelves"]
        for sl in sh["slots"]
        if sl["sku_id"] is None
    ]


def ad_slot_ids(planogram: dict) -> list[str]:
    return [a["ad_slot_id"] for bay in planogram["bays"] for a in bay["ad_slots"]]
