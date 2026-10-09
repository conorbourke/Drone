from __future__ import annotations

import copy
from collections.abc import Callable

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.defaults import DEFAULT_SETTINGS, SETTINGS_META
from app.models import AppSettings
from app.routers.settings import deep_merge, diff_against, flatten


def test_get_settings_returns_defaults_with_meta(auth_client: TestClient) -> None:
    response = auth_client.get("/api/settings")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"settings", "meta", "warnings"}
    assert body["warnings"] == []
    assert body["settings"] == DEFAULT_SETTINGS
    assert body["settings"]["schema_version"] == 1
    paths = set(body["meta"])
    assert paths == set(
        flatten({k: v for k, v in DEFAULT_SETTINGS.items() if k != "schema_version"})
    )
    assert paths == set(SETTINGS_META)
    for path, meta in body["meta"].items():
        assert set(meta) == {"label", "description", "source", "is_default"}
        assert meta["label"] and meta["description"] and meta["source"], path
        assert not meta["label"].endswith(" min"), path
        assert meta["is_default"] is True
    assert "proposed, confirm in Phase 3" in body["meta"]["checks.static_margin_min"]["source"]


def test_put_persists_only_the_diff(app: FastAPI, auth_client: TestClient) -> None:
    doc = copy.deepcopy(DEFAULT_SETTINGS)
    doc["limits"]["warn_mtow_kg"] = 22.0
    doc["printer"]["name"] = "Other printer"
    response = auth_client.put("/api/settings", json=doc)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["settings"]["limits"]["warn_mtow_kg"] == 22.0
    assert body["meta"]["limits.warn_mtow_kg"]["is_default"] is False
    assert body["meta"]["printer.name"]["is_default"] is False
    assert body["meta"]["limits.design_mtow_kg"]["is_default"] is True

    with app.state.session_factory() as db:
        row = db.scalar(select(AppSettings))
        assert row is not None
        assert row.data == {
            "schema_version": 1,
            "limits": {"warn_mtow_kg": 22.0},
            "printer": {"name": "Other printer"},
        }

    assert auth_client.get("/api/settings").json() == body

    # Back to defaults: the stored document shrinks to nothing but its version.
    response = auth_client.put("/api/settings", json=DEFAULT_SETTINGS)
    assert response.status_code == 200
    assert all(m["is_default"] for m in response.json()["meta"].values())
    with app.state.session_factory() as db:
        row = db.scalar(select(AppSettings))
        assert row is not None and row.data == {"schema_version": 1}


def test_put_invariants_give_plain_language_422(auth_client: TestClient) -> None:
    def put(mutate: Callable[[dict], None]) -> tuple[int, str]:
        doc = copy.deepcopy(DEFAULT_SETTINGS)
        mutate(doc)
        r = auth_client.put("/api/settings", json=doc)
        return r.status_code, r.text

    status, text = put(lambda d: d["limits"].__setitem__("legal_mtow_kg", 26))
    assert status == 422 and "25 kg" in text
    status, text = put(lambda d: d["limits"].__setitem__("design_mtow_kg", 25.5))
    assert status == 422 and "legal" in text
    status, text = put(lambda d: d["limits"].__setitem__("warn_mtow_kg", 24.5))
    assert status == 422 and "warning threshold" in text
    status, text = put(lambda d: d["printer"]["usable_envelope_mm"].__setitem__("z", 300))
    assert status == 422 and "build volume" in text
    status, text = put(lambda d: d["printer"]["build_volume_mm"].__setitem__("x", 0))
    assert status == 422
    status, text = put(lambda d: d["checks"].__setitem__("static_margin_min", 0.3))
    assert status == 422 and "static margin" in text
    status, text = put(lambda d: d["checks"].__setitem__("static_margin_min", 0))
    assert status == 422
    status, text = put(lambda d: d["checks"].__setitem__("battery_reserve_fraction", 1.0))
    assert status == 422 and "reserve" in text
    status, text = put(
        lambda d: d["checks"].__setitem__("battery_current_max_fraction_of_rating", 0)
    )
    assert status == 422 and "current" in text
    status, text = put(lambda d: d["checks"].__setitem__("hover_thrust_to_weight_min", 1.0))
    assert status == 422 and "thrust-to-weight" in text
    status, text = put(lambda d: d["checks"].__setitem__("cruise_to_stall_speed_ratio_min", 0.9))
    assert status == 422 and "stall" in text
    status, text = put(lambda d: d["units"].__setitem__("system", "imperial"))
    assert status == 422
    status, text = put(lambda d: d.__setitem__("extra", 1))
    assert status == 422


def test_diff_and_merge_helpers() -> None:
    defaults = {"a": {"b": 1, "c": 2}, "d": 3}
    assert diff_against(defaults, {"a": {"b": 1, "c": 5}, "d": 3}) == {"a": {"c": 5}}
    assert diff_against(defaults, copy.deepcopy(defaults)) == {}
    merged = deep_merge(defaults, {"a": {"c": 5}})
    assert merged == {"a": {"b": 1, "c": 5}, "d": 3}
    assert defaults == {"a": {"b": 1, "c": 2}, "d": 3}  # untouched
