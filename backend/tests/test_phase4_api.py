"""Phase 4 API: seed and migration, parts list, lock/replace, payloads, analyses with parts,
supplier refresh (fake Claude, mocked link checks), settings schema 3, assistant tool."""

from __future__ import annotations

import copy
import json
import sqlite3
import time
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import suppliers
from app.db import utcnow
from app.defaults import DEFAULT_SETTINGS
from app.models import Part, PartListing, PartSelection
from app.parts_catalog import validate_spec
from app.parts_catalog.load import load_file
from app.schemas.migrate import upgrade_settings
from app.schemas.parts import PartCreate
from tests.conftest import BACKEND_DIR, make_settings

SEED = BACKEND_DIR / "seed" / "parts.json"
EXAMPLE = BACKEND_DIR / "seed" / "parts.example.json"


@pytest.fixture
def seeded(app: FastAPI) -> None:
    with app.state.session_factory() as db:
        load_file(db, SEED)


def _alembic(url: str, target: str) -> None:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, target)


# ---------------------------------------------------------------------------
# Seed and migration
# ---------------------------------------------------------------------------


def test_seed_file_loads_and_validates() -> None:
    entries = json.loads(SEED.read_text(encoding="utf-8"))
    assert len(entries) == 53
    assert sum(len(e["listings"]) for e in entries) == 69
    categories = {e["category"] for e in entries}
    assert categories == {
        "motor",
        "propeller",
        "esc",
        "servo",
        "battery",
        "cell",
        "autopilot",
        "gps",
        "radio",
        "telemetry",
        "carbon_tube",
    }
    for e in entries:
        data = PartCreate.model_validate(e)
        validate_spec(data.category, data.spec)
        assert e["verified"] is False
        assert all(li["country"] in ("IE", "UK") for li in e["listings"])


def _insert_examples(db_file: Path, edited_notes: str | None = None) -> None:
    """The Phase 1 loader's rows (validated, normalised specs) written straight into a 0003
    database (the ORM models already have the 0004 columns)."""
    con = sqlite3.connect(db_file)
    now = "2026-10-01 10:00:00"
    for i, entry in enumerate(json.loads(EXAMPLE.read_text(encoding="utf-8"))):
        notes = entry.get("notes", "")
        if edited_notes is not None and i == 0:
            notes = edited_notes
        cur = con.execute(
            "insert into parts (category, manufacturer, model, mass_g, price_eur_estimate, spec,"
            " source, verified, notes, created_at, updated_at) values (?,?,?,?,?,?,?,?,?,?,?)",
            (
                entry["category"],
                entry["manufacturer"],
                entry["model"],
                entry["mass_g"],
                entry.get("price_eur_estimate"),
                json.dumps(validate_spec(entry["category"], entry["spec"])),
                entry.get("source", ""),
                0,
                notes,
                now,
                now,
            ),
        )
        for li in entry.get("listings", []):
            con.execute(
                "insert into part_listings (part_id, supplier_name, country, url, price_eur, "
                "in_stock, last_checked_at, created_at, updated_at) values (?,?,?,?,?,?,?,?,?)",
                (
                    cur.lastrowid,
                    li["supplier_name"],
                    li["country"],
                    li["url"],
                    li.get("price_eur"),
                    li.get("in_stock"),
                    None,
                    now,
                    now,
                ),
            )
    con.commit()
    con.close()


def test_migration_0004_replaces_untouched_examples_with_the_seed(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'a.db'}"
    _alembic(url, "0003")
    _insert_examples(tmp_path / "a.db")
    _alembic(url, "head")
    con = sqlite3.connect(tmp_path / "a.db")
    models = [r[0] for r in con.execute("select model from parts")]
    assert len(models) == 53
    assert not any("placeholder" in m for m in models)
    assert con.execute("select count(*) from part_listings").fetchone()[0] == 69
    assert con.execute("select count(*) from parts where verified = 1").fetchone()[0] == 0
    con.close()


def test_migration_0004_keeps_an_owner_catalogue(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'b.db'}"
    _alembic(url, "0003")
    _insert_examples(tmp_path / "b.db", edited_notes="My own notes")
    _alembic(url, "head")
    con = sqlite3.connect(tmp_path / "b.db")
    models = [r[0] for r in con.execute("select model from parts")]
    con.close()
    # The edited example stays, the untouched ones go, and the seed is skipped (not empty).
    assert models == ["EM-4110-400 (placeholder)"]


# ---------------------------------------------------------------------------
# Parts list
# ---------------------------------------------------------------------------


def _list(client: TestClient, project_id: int, **params: Any) -> dict[str, Any]:
    response = client.get(f"/api/projects/{project_id}/parts-list", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _role(body: dict[str, Any], role: str) -> dict[str, Any]:
    return next(r for r in body["roles"] if r["role"] == role)


def test_parts_list_for_the_default_design(
    seeded: None, auth_client: TestClient, project: dict
) -> None:
    body = _list(auth_client, project["id"])
    assert set(body) >= {
        "roles",
        "consumables",
        "totals",
        "upgrades",
        "uk_import_note",
        "generated_at",
        "source",
        "stored",
        "unfilled",
    }
    assert "analysis_parts" not in body
    assert body["source"] == {"kind": "draft", "version_id": None, "version_number": None}
    assert body["stored"]["in_sync"] is False  # GET never writes
    for row in body["roles"]:
        assert set(row) >= {
            "role",
            "label",
            "system",
            "part",
            "quantity",
            "unit_mass_g",
            "line_mass_g",
            "unit_price_eur",
            "line_price_eur",
            "best_listing",
            "listings",
            "reasoning",
            "alternatives",
            "flags",
            "locked",
        }
        assert row["filled"], row
    t = body["totals"]
    assert set(t) >= {"mass_g", "mass_vs_tier1_g", "cost_eur", "budget_eur", "budget_status"}
    assert t["budget_eur"] == 5000.0 and t["budget_status"] in ("under", "near", "over")
    with auth_client.app.state.session_factory() as db:  # type: ignore[attr-defined]
        assert db.scalar(select(PartSelection)) is None
    # The draft payload has no stored selection yet.
    assert auth_client.get(f"/api/projects/{project['id']}/draft").json()["parts_selection"] is None


def test_recompute_replace_lock_unlock(
    seeded: None, app: FastAPI, auth_client: TestClient, project: dict
) -> None:
    pid = project["id"]
    stored = auth_client.post(f"/api/projects/{pid}/parts-list/recompute").json()
    assert stored["stored"]["in_sync"] is True
    assert _list(auth_client, pid)["stored"]["in_sync"] is True
    draft = auth_client.get(f"/api/projects/{pid}/draft").json()
    sel = draft["parts_selection"]
    assert sel is not None and set(sel["roles"]) == {r["role"] for r in stored["roles"]}
    assert sel["masses_g"]["lift_motor_each"] == _role(stored, "lift_motor")["part"]["mass_g"]
    tier1_before = stored["totals"]["mass_g"]

    # Replace the GPS with a heavier alternative: locked, kept by later recomputes.
    gps = _role(stored, "gps")
    alt = max(gps["alternatives"], key=lambda a: a["part"]["mass_g"])
    replaced = auth_client.put(
        f"/api/projects/{pid}/parts-list/roles/gps", json={"part_id": alt["part"]["id"]}
    )
    assert replaced.status_code == 200, replaced.text
    body = replaced.json()
    assert _role(body, "gps")["part"]["id"] == alt["part"]["id"]
    assert _role(body, "gps")["locked"] is True
    assert body["totals"]["mass_g"] != tier1_before
    again = auth_client.post(f"/api/projects/{pid}/parts-list/recompute").json()
    assert _role(again, "gps")["part"]["id"] == alt["part"]["id"]
    draft = auth_client.get(f"/api/projects/{pid}/draft").json()
    assert draft["parts_selection"]["roles"]["gps"]["locked"] is True
    assert draft["parts_selection"]["roles"]["gps"]["part_id"] == alt["part"]["id"]

    # Wrong category and unknown role are refused plainly.
    motor_id = _role(body, "lift_motor")["part"]["id"]
    wrong = auth_client.put(f"/api/projects/{pid}/parts-list/roles/gps", json={"part_id": motor_id})
    assert wrong.status_code == 422 and "GPS must be a gps part" in wrong.text
    assert (
        auth_client.put(
            f"/api/projects/{pid}/parts-list/roles/wings", json={"part_id": motor_id}
        ).status_code
        == 422
    )
    assert (
        auth_client.put(
            f"/api/projects/{pid}/parts-list/roles/cruise_motor", json={"part_id": motor_id}
        ).status_code
        == 422
    )

    # Unlock: the engine's pick comes back.
    unlocked = auth_client.delete(f"/api/projects/{pid}/parts-list/roles/gps/lock").json()
    assert _role(unlocked, "gps")["part"]["id"] == gps["part"]["id"]
    assert _role(unlocked, "gps")["locked"] is False

    # A version saved from the draft keeps the selection; deleting it cascades.
    v = auth_client.post(f"/api/projects/{pid}/versions", json={"name": "v1"}).json()
    assert v["parts_selection"] is not None
    vlist = _list(auth_client, pid, source=str(v["id"]))
    assert vlist["source"]["version_id"] == v["id"] and vlist["stored"]["in_sync"] is True
    assert auth_client.delete(f"/api/versions/{v['id']}").status_code == 204
    with app.state.session_factory() as db:
        assert db.scalar(select(PartSelection).where(PartSelection.version_id == v["id"])) is None
    bad = auth_client.get(f"/api/projects/{pid}/parts-list", params={"source": "999999"})
    assert bad.status_code == 422


def test_custom_cell_pack_lock_and_quantity_rules(
    seeded: None, app: FastAPI, auth_client: TestClient, project: dict
) -> None:
    pid = project["id"]
    with app.state.session_factory() as db:
        cell = db.scalar(select(Part).where(Part.model == "INR-21700-P50B"))
        pack = db.scalar(select(Part).where(Part.model.like("Semi-solid 300Wh%")))
        assert cell is not None and pack is not None
        cell_id, pack_id = cell.id, pack.id
    r = auth_client.put(
        f"/api/projects/{pid}/parts-list/roles/battery", json={"part_id": cell_id, "quantity": 13}
    )
    assert r.status_code == 422 and "multiple of 6" in r.text
    r = auth_client.put(f"/api/projects/{pid}/parts-list/roles/battery", json={"part_id": pack_id})
    assert r.status_code == 422 and "12S" in r.text
    r = auth_client.put(
        f"/api/projects/{pid}/parts-list/roles/battery", json={"part_id": cell_id, "quantity": 24}
    )
    assert r.status_code == 200, r.text
    batt = _role(r.json(), "battery")
    assert batt["part"]["id"] == cell_id and batt["quantity"] == 24 and batt["locked"]
    assert batt["custom_pack"]["cells_parallel"] == 4


def test_analysis_uses_the_selected_parts(
    seeded: None, app: FastAPI, auth_client: TestClient, project: dict
) -> None:
    pid = project["id"]
    created = auth_client.post(f"/api/projects/{pid}/analyses", json={"source": "draft"})
    assert created.status_code == 202, created.text
    assert created.json()["parts"] == "selected"
    generic = auth_client.post(
        f"/api/projects/{pid}/analyses", json={"source": "draft", "parts": "generic"}
    )
    assert generic.json()["parts"] == "generic"
    assert generic.json()["inputs_hash"] != created.json()["inputs_hash"]
    # The queued analysis stored the picks as the draft's record.
    draft = auth_client.get(f"/api/projects/{pid}/draft").json()
    assert draft["parts_selection"] is not None
    from app.models import Analysis

    with app.state.session_factory() as db:
        row = db.get(Analysis, created.json()["id"])
        assert row is not None
        parts = row.inputs["parts"]
        assert {"lift_motor", "lift_prop", "esc", "battery", "avionics"} <= set(parts)
        assert "thrust_data" in parts["lift_motor"]
        assert (
            row.inputs["parts_selection"]["lift_motor"]["part_id"] == parts["lift_motor"]["part_id"]
        )


# ---------------------------------------------------------------------------
# Supplier refresh
# ---------------------------------------------------------------------------

FAKE_ANSWER = {
    "listings": [
        {
            "supplier_name": "3DXR",
            "country": "UK",
            "url": "https://www.3dxr.co.uk/product/t-motor-mn4014-kv400/",
            "price_eur": 110.5,
            "price_as_shown": "GBP 94.44 inc VAT",
            "in_stock": True,
            "match": "exact",
            "note": "",
        },
        {
            "supplier_name": "New Shop IE",
            "country": "IE",
            "url": "https://shop.example.ie/mn4014",
            "price_eur": 120.0,
            "price_as_shown": "EUR 120.00",
            "in_stock": None,
            "match": "exact",
            "note": "",
        },
        {
            "supplier_name": "Other",
            "country": "UK",
            "url": "https://other.example.co.uk/mn4014-kv330",
            "price_eur": 99.0,
            "price_as_shown": "",
            "in_stock": True,
            "match": "variant",
            "note": "KV330",
        },
        {"supplier_name": "Broken", "country": "FR", "url": "ftp://x", "match": "exact"},
    ],
    "summary": "Two shops found.",
}


@pytest.fixture
def fake_supplier(
    app: FastAPI, auth_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    path = tmp_path / "supplier.json"
    path.write_text(json.dumps(FAKE_ANSWER), encoding="utf-8")
    original = app.state.settings
    fake = original.model_copy(update={"claude_fake_supplier_file": path})
    app.state.settings = fake
    worker = app.state.analysis_worker
    monkeypatch.setattr(worker, "settings", fake)
    checked: list[str] = []

    def fake_check(url: str) -> tuple[bool, int | None]:
        checked.append(url)
        return ("example.ie" not in url, 200 if "example.ie" not in url else 404)

    monkeypatch.setattr(suppliers, "check_url", fake_check)
    yield path
    app.state.settings = original


def _mn4014(app: FastAPI) -> int:
    with app.state.session_factory() as db:
        part = db.scalar(select(Part).where(Part.model.like("MN4014%")))
        assert part is not None
        return part.id


def test_refresh_listings_with_fake_claude(
    seeded: None, app: FastAPI, auth_client: TestClient, fake_supplier: Path
) -> None:
    part_id = _mn4014(app)
    r = auth_client.post(f"/api/parts/{part_id}/refresh-listings")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["refresh"]["status"] == "done"
    assert body["refresh"]["checked"] == 2 and body["refresh"]["added"] == 1
    assert body["refresh"]["updated"] == 1 and body["refresh"]["ignored"] == 1
    listings = {li["supplier_name"]: li for li in body["part"]["listings"]}
    assert listings["3DXR"]["price_eur"] == 110.5 and listings["3DXR"]["url_ok"] is True
    assert listings["3DXR"]["url_status"] == 200 and listings["3DXR"]["stale"] is False
    assert listings["New Shop IE"]["url_ok"] is False  # stored, but not as working
    assert "Other" not in listings
    assert body["part"]["listings_refresh_status"] == "done"
    # One refresh per part per hour.
    again = auth_client.post(f"/api/parts/{part_id}/refresh-listings")
    assert again.status_code == 429
    assert "less than an hour ago" in again.json()["detail"]
    assert int(again.headers["retry-after"]) > 3000


def test_refresh_refusal_and_missing_key(
    seeded: None, app: FastAPI, auth_client: TestClient, fake_supplier: Path
) -> None:
    part_id = _mn4014(app)
    fake_supplier.write_text(
        json.dumps({"stop_reason": "refusal", "stop_details": {"category": "cyber"}}),
        encoding="utf-8",
    )
    r = auth_client.post(f"/api/parts/{part_id}/refresh-listings")
    assert r.status_code == 200
    assert r.json()["refresh"]["status"] == "refused"
    with app.state.session_factory() as db:
        assert db.scalar(select(PartListing).where(PartListing.part_id == part_id)).url_ok is None
    original = app.state.settings
    app.state.settings = original.model_copy(
        update={"claude_fake_supplier_file": None, "anthropic_api_key": None}
    )
    try:
        r = auth_client.post(f"/api/parts/{part_id}/refresh-listings")
        assert r.status_code == 503
        assert r.json() == {"detail": suppliers.MISSING_KEY_MESSAGE}
        r = auth_client.post("/api/projects/1/parts-list/refresh-all")
        assert r.status_code == 503
    finally:
        app.state.settings = original


def test_refresh_all_queues_on_the_worker(
    seeded: None, app: FastAPI, auth_client: TestClient, project: dict, fake_supplier: Path
) -> None:
    part_id = _mn4014(app)
    with app.state.session_factory() as db:
        part = db.get(Part, part_id)
        assert part is not None
        part.listings_refreshed_at = utcnow() - timedelta(minutes=10)
        db.commit()
    r = auth_client.post(f"/api/projects/{project['id']}/parts-list/refresh-all")
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["queued"] and all("part_id" in q for q in body["queued"])
    if any(q["part_id"] == part_id for q in body["queued"]):
        pytest.fail("a part refreshed 10 minutes ago must be skipped")
    queued_ids = {q["part_id"] for q in body["queued"]}
    deadline = time.time() + 60
    while time.time() < deadline:
        with app.state.session_factory() as db:
            statuses = {
                p.listings_refresh_status
                for p in db.scalars(select(Part).where(Part.id.in_(queued_ids)))
            }
        if statuses <= {"done"}:
            break
        time.sleep(0.2)
    assert statuses == {"done"}


def test_check_url_refuses_private_hosts() -> None:
    assert suppliers.check_url("http://127.0.0.1/admin") == (False, None)
    assert suppliers.check_url("http://localhost:8080/") == (False, None)
    assert suppliers.check_url("ftp://example.com/x") == (False, None)


def test_supplier_lookup_handles_pause_turn_and_search_errors(
    app: FastAPI, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real request path with the SDK call replaced: a paused server-tool turn is sent back
    and resumed; search errors arrive as result blocks."""
    from types import SimpleNamespace

    calls: list[dict[str, Any]] = []

    class Block(dict):
        def to_dict(self) -> dict[str, Any]:
            return dict(self)

    def fake_send(_client: Any, **kwargs: Any) -> Any:
        calls.append(kwargs)
        if len(calls) == 1:
            return SimpleNamespace(
                stop_reason="pause_turn",
                model="claude-opus-5-5",
                usage=None,
                content=[
                    Block(type="server_tool_use", id="s1", name="web_search", input={"query": "x"}),
                    Block(
                        type="web_search_tool_result",
                        tool_use_id="s1",
                        content={
                            "type": "web_search_tool_result_error",
                            "error_code": "unavailable",
                        },
                    ),
                ],
            )
        return SimpleNamespace(
            stop_reason="end_turn",
            model="claude-opus-5-5",
            usage=None,
            content=[Block(type="text", text=json.dumps(FAKE_ANSWER))],
        )

    monkeypatch.setattr(suppliers, "_send", fake_send)
    settings = make_settings(tmp_path, anthropic_api_key="sk-test")
    part = Part(
        category="motor",
        manufacturer="T-Motor",
        model="MN4014 KV400",
        mass_g=171,
        spec={},
        source="",
        listings=[],
    )
    result = suppliers.lookup(settings, part)
    assert result.status == "ok"
    assert len(calls) == 2
    assert calls[0]["model"] == "claude-opus-5-5"
    assert calls[0]["thinking"] == {"type": "adaptive"}
    assert calls[0]["output_config"]["effort"] == "medium"
    assert calls[0]["output_config"]["format"]["type"] == "json_schema"
    assert calls[0]["fallbacks"] == "default"
    assert calls[0]["tools"][0]["type"] == "web_search_20260209"
    assert calls[0]["tools"][0]["user_location"]["country"] == "IE"
    # The paused turn is echoed back after the user message, without a new user turn.
    assert [m["role"] for m in calls[1]["messages"]] == ["user", "assistant"]
    assert result.search_errors == ["search: unavailable"]
    assert [li.supplier_name for li in result.answer.listings] == ["3DXR", "New Shop IE", "Other"]


# ---------------------------------------------------------------------------
# Settings schema 3 and the assistant
# ---------------------------------------------------------------------------


def test_settings_schema_3_budget(auth_client: TestClient) -> None:
    upgraded = upgrade_settings({"schema_version": 2, "checks": {"static_margin_min": 0.06}})
    assert upgraded["schema_version"] == 3 and upgraded["budget"] == {"prototype_eur": 5000.0}
    body = auth_client.get("/api/settings").json()
    assert body["settings"]["budget"]["prototype_eur"] == 5000.0
    assert body["meta"]["budget.prototype_eur"]["label"] == "Prototype budget"
    doc = copy.deepcopy(DEFAULT_SETTINGS)
    doc["budget"]["prototype_eur"] = 0
    assert auth_client.put("/api/settings", json=doc).status_code == 422
    doc["budget"]["prototype_eur"] = 4000
    assert auth_client.put("/api/settings", json=doc).status_code == 200


def test_assistant_get_parts_list(seeded: None, app: FastAPI, project: dict) -> None:
    from app.assistant.tools import ToolContext
    from app.models import User

    with app.state.session_factory() as db:
        owner = db.scalar(select(User).where(User.is_owner.is_(True)))
        assert owner is not None
        owner_id = owner.id
    ctx = ToolContext(app.state.session_factory, app.state.settings, owner_id, project["id"])
    out = json.loads(ctx.execute("get_parts_list", {}).content)
    assert out["roles"] and out["totals"]["budget_eur"] == 5000.0
    assert any(r["part"] for r in out["roles"])
    assert "budget_message" in out["totals"]


def test_parts_routes_need_csrf_header(
    seeded: None, auth_client: TestClient, project: dict
) -> None:
    r = auth_client.post(
        f"/api/projects/{project['id']}/parts-list/recompute",
        headers={"X-Requested-With": ""},
    )
    assert r.status_code == 403
