from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.models import Part, PartListing
from app.parts_catalog import CATEGORY_KEYS
from app.parts_catalog.load import load_file

SEED = Path(__file__).resolve().parent.parent / "seed" / "parts.example.json"

MOTOR_SPEC = {
    "kv_rpm_per_v": 400,
    "resistance_ohm": 0.08,
    "no_load_current_a": 0.6,
    "max_current_a": 30,
    "max_power_w": 700,
    "lipo_cells_min": 4,
    "lipo_cells_max": 6,
    "stator_size": "4110",
    "shaft_mm": 5,
    "mount_pattern": "25x25 M3",
    "thrust_data": [
        {
            "prop": "15x5.5",
            "voltage_v": 22.2,
            "throttle_pct": 50,
            "thrust_g": 1100,
            "current_a": 7.5,
            "power_w": 166,
            "rpm": 5200,
        }
    ],
}


def _motor(**overrides: object) -> dict:
    body = {
        "category": "motor",
        "manufacturer": "Acme",
        "model": "M1",
        "mass_g": 150,
        "spec": MOTOR_SPEC,
        "source": "unit test",
        "listings": [
            {
                "supplier_name": "Shop IE",
                "country": "IE",
                "url": "https://example.ie/m1",
                "price_eur": 50,
                "in_stock": True,
                "last_checked_at": "2026-10-01T10:00:00Z",
            }
        ],
    }
    body.update(overrides)
    return body


def test_categories_generated_from_models(auth_client: TestClient) -> None:
    response = auth_client.get("/api/parts/categories")
    assert response.status_code == 200
    cats = response.json()
    assert [c["key"] for c in cats] == list(CATEGORY_KEYS)
    assert len(cats) == 11
    motor = next(c for c in cats if c["key"] == "motor")
    assert set(motor) == {"key", "label", "description", "fields"}
    field_names = [f["name"] for f in motor["fields"]]
    assert field_names[:3] == ["kv_rpm_per_v", "resistance_ohm", "no_load_current_a"]
    assert "thrust_data" in field_names
    kv = motor["fields"][0]
    assert kv["unit"] == "rpm/V" and kv["type"] == "number" and kv["required"] is True
    assert kv["description"]
    for cat in cats:
        for field in cat["fields"]:
            assert {"name", "label", "unit", "type", "required", "description"} <= set(field)
            assert field["description"], (cat["key"], field["name"])
    prop = next(c for c in cats if c["key"] == "propeller")
    trade = next(f for f in prop["fields"] if f["name"] == "trade_size")
    assert trade["required"] is False and trade["type"] == "string"
    battery = next(c for c in cats if c["key"] == "battery")
    chem = next(f for f in battery["fields"] if f["name"] == "chemistry")
    assert [e["value"] for e in chem["enum"]] == ["lipo", "li-ion"]


def test_create_part_with_listings_and_get(auth_client: TestClient) -> None:
    response = auth_client.post("/api/parts", json=_motor())
    assert response.status_code == 201, response.text
    part = response.json()
    assert part["category"] == "motor"
    assert part["spec"]["kv_rpm_per_v"] == 400
    assert part["verified"] is False
    assert len(part["listings"]) == 1
    assert part["listings"][0]["country"] == "IE"
    assert part["listings"][0]["last_checked_at"] == "2026-10-01T10:00:00Z"
    assert auth_client.get(f"/api/parts/{part['id']}").json() == part


def test_create_part_spec_validation(auth_client: TestClient) -> None:
    bad_spec = dict(MOTOR_SPEC)
    del bad_spec["kv_rpm_per_v"]
    bad_spec["unknown_field"] = 1
    response = auth_client.post("/api/parts", json=_motor(spec=bad_spec))
    assert response.status_code == 422
    locs = [tuple(err["loc"]) for err in response.json()["detail"]]
    assert ("body", "spec", "kv_rpm_per_v") in locs
    assert ("body", "spec", "unknown_field") in locs

    wrong_category = auth_client.post("/api/parts", json=_motor(category="wheel"))
    assert wrong_category.status_code == 422
    assert "wheel" in wrong_category.text

    cells = dict(MOTOR_SPEC, lipo_cells_min=6, lipo_cells_max=4)
    assert auth_client.post("/api/parts", json=_motor(spec=cells)).status_code == 422

    tube = {
        "category": "carbon_tube",
        "manufacturer": "T",
        "model": "x",
        "mass_g": 10,
        "spec": {
            "outer_diameter_mm": 10,
            "inner_diameter_mm": 12,
            "length_mm": 1000,
            "layup": "pultruded",
            "mass_per_m_g": 50,
        },
    }
    assert auth_client.post("/api/parts", json=tube).status_code == 422


def test_create_part_identity_conflict(auth_client: TestClient) -> None:
    assert auth_client.post("/api/parts", json=_motor()).status_code == 201
    response = auth_client.post("/api/parts", json=_motor())
    assert response.status_code == 409
    assert "Acme M1" in response.json()["detail"]


def test_list_parts_filters(auth_client: TestClient) -> None:
    auth_client.post("/api/parts", json=_motor(model="Alpha 400"))
    auth_client.post("/api/parts", json=_motor(model="Beta 700", listings=[]))
    auth_client.post(
        "/api/parts",
        json={
            "category": "propeller",
            "manufacturer": "Acme",
            "model": "P15",
            "mass_g": 20,
            "spec": {
                "diameter_mm": 381,
                "pitch_mm": 140,
                "blades": 2,
                "folding": True,
                "hub_bore_mm": 6,
                "material": "carbon",
            },
        },
    )
    assert len(auth_client.get("/api/parts").json()) == 3
    motors = auth_client.get("/api/parts", params={"category": "motor"}).json()
    assert [m["model"] for m in motors] == ["Alpha 400", "Beta 700"]
    assert len(motors[0]["listings"]) == 1
    found = auth_client.get("/api/parts", params={"q": "beta"}).json()
    assert [p["model"] for p in found] == ["Beta 700"]
    both = auth_client.get("/api/parts", params={"category": "propeller", "q": "acme"}).json()
    assert [p["model"] for p in both] == ["P15"]


def test_patch_and_delete_part(app: FastAPI, auth_client: TestClient) -> None:
    part = auth_client.post("/api/parts", json=_motor()).json()
    patched = auth_client.patch(
        f"/api/parts/{part['id']}", json={"mass_g": 160, "verified": True, "notes": "checked"}
    )
    assert patched.status_code == 200
    assert patched.json()["mass_g"] == 160 and patched.json()["verified"] is True
    bad = auth_client.patch(f"/api/parts/{part['id']}", json={"spec": {"kv_rpm_per_v": -1}})
    assert bad.status_code == 422
    assert auth_client.delete(f"/api/parts/{part['id']}").status_code == 204
    assert auth_client.get(f"/api/parts/{part['id']}").status_code == 404
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count(PartListing.id))) == 0  # cascaded


def test_listings_add_and_delete(auth_client: TestClient) -> None:
    part = auth_client.post("/api/parts", json=_motor(listings=[])).json()
    created = auth_client.post(
        f"/api/parts/{part['id']}/listings",
        json={"supplier_name": "Shop UK", "country": "UK", "url": "https://example.co.uk/m1"},
    )
    assert created.status_code == 201
    listing = created.json()
    assert listing["part_id"] == part["id"] and listing["price_eur"] is None
    bad_country = auth_client.post(
        f"/api/parts/{part['id']}/listings",
        json={"supplier_name": "Shop", "country": "DE", "url": "https://example.de"},
    )
    assert bad_country.status_code == 422
    bad_url = auth_client.post(
        f"/api/parts/{part['id']}/listings",
        json={"supplier_name": "Shop", "country": "IE", "url": "javascript:alert(1)"},
    )
    assert bad_url.status_code == 422
    assert len(auth_client.get(f"/api/parts/{part['id']}").json()["listings"]) == 1
    assert auth_client.delete(f"/api/parts/listings/{listing['id']}").status_code == 204
    assert auth_client.delete(f"/api/parts/listings/{listing['id']}").status_code == 404
    assert auth_client.get(f"/api/parts/{part['id']}").json()["listings"] == []


def test_seed_loader_is_idempotent(app: FastAPI, auth_client: TestClient) -> None:
    with app.state.session_factory() as db:
        created, updated = load_file(db, SEED)
        assert created == 4 and updated == 0
        created2, updated2 = load_file(db, SEED)
        assert created2 == 0 and updated2 == 4
        assert db.scalar(select(func.count(Part.id))) == 4
        assert db.scalar(select(func.count(PartListing.id))) == 1
        for part in db.scalars(select(Part)):
            assert part.verified is False
            assert part.source == "example placeholder, not verified"
    parts = auth_client.get("/api/parts", params={"category": "motor"}).json()
    assert len(parts) == 1 and len(parts[0]["listings"]) == 1
