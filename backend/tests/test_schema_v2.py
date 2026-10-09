"""Design parameters schema version 2: defaults, metadata and the 1 -> 2 upgrade."""

from __future__ import annotations

import copy
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.defaults import DEFAULT_DESIGN_PARAMETERS, DEFAULT_MISSION
from app.models import DesignVersion, Project
from app.schemas.migrate import upgrade_parameters

NEW_FIELDS = {
    "wing.twist_deg": (0.0, "°"),
    "booms.diameter_mm": (20.0, "mm"),
    "tail.v_angle_deg": (40.0, "°"),
    "tail.airfoil": ("naca0009", None),
    "propulsion.prop_diameter_mm": (330.0, "mm"),
    "propulsion.prop_pitch_mm": (140.0, "mm"),
    "propulsion.prop_blades": (2, None),
    "battery.chemistry": ("lipo", None),
    "battery.cells_series": (6, "S"),
    "battery.cells_parallel": (1, "P"),
    "battery.capacity_mah": (5000.0, "mAh"),
    "battery.x_mm": (380.0, "mm"),
    "allowances.avionics_g": (220.0, "g"),
    "allowances.wiring_fraction": (0.06, None),
}


def _get(doc: dict[str, Any], path: str) -> Any:
    for part in path.split("."):
        doc = doc[part]
    return doc


def v1_document() -> dict[str, Any]:
    """The Phase 1 default document exactly as Phase 1 stored it."""
    doc = copy.deepcopy(DEFAULT_DESIGN_PARAMETERS)
    doc["schema_version"] = 1
    for block in ("propulsion", "battery", "allowances"):
        del doc[block]
    del doc["wing"]["twist_deg"]
    del doc["booms"]["diameter_mm"]
    del doc["tail"]["v_angle_deg"]
    del doc["tail"]["airfoil"]
    return doc


def _upgraded_defaults() -> dict:
    """What a v1 default document reads back as: today's defaults, except that the 1->2
    upgrader keeps the battery position it was written with (380 mm)."""
    expected = copy.deepcopy(DEFAULT_DESIGN_PARAMETERS)
    expected["battery"]["x_mm"] = 380.0
    return expected


def test_defaults_are_version_2_with_new_fields() -> None:
    assert DEFAULT_DESIGN_PARAMETERS["schema_version"] == 2
    # New projects place the battery 90 mm further forward than the v1->v2 upgrader does (stable at
    # both payloads per the Phase 3 AVL analysis); the upgrader keeps the value it was written with.
    expected_new = {**NEW_FIELDS, "battery.x_mm": (290.0, "mm")}
    for path, (value, _unit) in expected_new.items():
        assert _get(DEFAULT_DESIGN_PARAMETERS, path) == value, path


def test_upgrade_1_to_2_adds_defaults_and_keeps_values() -> None:
    old = v1_document()
    old["wing"]["span_mm"] = 2100.0
    upgraded = upgrade_parameters(old)
    assert upgraded["schema_version"] == 2
    assert upgraded["wing"]["span_mm"] == 2100.0
    for path, (value, _unit) in NEW_FIELDS.items():
        assert _get(upgraded, path) == value, path
    assert old["schema_version"] == 1 and "battery" not in old  # input untouched


def test_schema_endpoint_serves_new_fields(auth_client: TestClient) -> None:
    schema = auth_client.get("/api/schema/design").json()
    for path, (_value, unit) in NEW_FIELDS.items():
        meta = schema[path]
        assert meta["label"] and meta["description"], path
        assert meta["unit"] == unit, (path, meta["unit"])
    assert [e["value"] for e in schema["battery.chemistry"]["enum"]] == ["lipo", "li-ion"]
    assert (
        schema["battery.cells_series"]["min"] == 1 and schema["battery.cells_series"]["max"] == 14
    )
    assert schema["battery.cells_parallel"]["max"] == 10
    assert schema["propulsion.prop_blades"]["type"] == "integer"


def test_new_project_draft_starts_at_v2(project: dict) -> None:
    assert project["draft"]["parameters"] == DEFAULT_DESIGN_PARAMETERS


def test_stored_v1_rows_read_back_as_v2(app: FastAPI, auth_client: TestClient) -> None:
    owner_id = auth_client.get("/api/auth/me").json()["id"]
    old = v1_document()
    with app.state.session_factory() as db:
        project = Project(
            owner_id=owner_id,
            name="Phase 1 project",
            description="",
            draft_parameters=old,
            draft_mission=copy.deepcopy(DEFAULT_MISSION),
            next_version_number=2,
        )
        db.add(project)
        db.flush()
        version = DesignVersion(
            project_id=project.id,
            owner_id=owner_id,
            name="v1",
            notes="",
            number=1,
            parameters=old,
            mission=copy.deepcopy(DEFAULT_MISSION),
        )
        db.add(version)
        db.commit()
        pid, vid = project.id, version.id

    draft = auth_client.get(f"/api/projects/{pid}/draft").json()
    assert draft["parameters"] == _upgraded_defaults()
    got = auth_client.get(f"/api/versions/{vid}").json()
    assert got["parameters"]["schema_version"] == 2
    assert got["parameters"]["battery"]["capacity_mah"] == 5000.0

    # Stored rows are never rewritten in place.
    with app.state.session_factory() as db:
        assert db.get(Project, pid).draft_parameters["schema_version"] == 1  # type: ignore[union-attr]
        assert db.get(DesignVersion, vid).parameters["schema_version"] == 1  # type: ignore[union-attr]

    # Restoring the old version gives a v2 draft.
    restored = auth_client.post(f"/api/versions/{vid}/restore").json()
    assert restored["parameters"]["schema_version"] == 2


def test_put_draft_accepts_a_v1_document(auth_client: TestClient, project: dict) -> None:
    """A browser tab opened before the deploy still saves: the document is upgraded."""
    body = {"parameters": v1_document(), "mission": project["draft"]["mission"]}
    response = auth_client.put(f"/api/projects/{project['id']}/draft", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["parameters"] == _upgraded_defaults()


def test_new_field_validation(auth_client: TestClient, project: dict) -> None:
    for path, bad in (
        ("battery.cells_series", 15),
        ("battery.cells_parallel", 0),
        ("battery.chemistry", "nimh"),
        ("booms.diameter_mm", 0),
        ("propulsion.prop_diameter_mm", -1),
    ):
        params = copy.deepcopy(project["draft"]["parameters"])
        block, key = path.split(".")
        params[block][key] = bad
        response = auth_client.put(
            f"/api/projects/{project['id']}/draft",
            json={"parameters": params, "mission": project["draft"]["mission"]},
        )
        assert response.status_code == 422, path
