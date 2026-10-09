from __future__ import annotations

from fastapi.testclient import TestClient

from app.defaults import DEFAULT_DESIGN_PARAMETERS, DEFAULT_MISSION, DEFAULT_SETTINGS
from app.schemas.design import DesignParameters
from app.schemas.introspect import humanize, unit_for
from app.schemas.migrate import upgrade_mission, upgrade_parameters, upgrade_settings
from app.schemas.mission import Mission
from app.schemas.settings import SettingsDocument

REAR_TILT_NOTE = (
    "Less common in ArduPilot than front tilt. ArduPilot supports it through Q_TILT_MASK; "
    "check your setup in a simulator before flying."
)


def test_design_schema_from_field_metadata(auth_client: TestClient) -> None:
    response = auth_client.get("/api/schema/design")
    assert response.status_code == 200
    schema = response.json()
    assert "schema_version" not in schema
    span = schema["wing.span_mm"]
    assert span["label"] == "Wingspan"
    assert span["unit"] == "mm"
    assert span["type"] == "number"
    assert span["min"] == 0
    assert "tip" in schema["wing.span_mm"]["description"]
    assert schema["wing.sweep_deg"]["unit"] == "°"
    assert schema["booms.count"]["type"] == "integer"
    assert schema["wing.airfoil"]["type"] == "string" and schema["wing.airfoil"]["unit"] is None

    layout = schema["layout"]
    assert layout["type"] == "string"
    options = {e["value"]: e for e in layout["enum"]}
    assert set(options) == {"front_tilt", "rear_tilt", "quad_pusher"}
    assert options["rear_tilt"]["note"] == REAR_TILT_NOTE
    assert options["front_tilt"]["label"] == "Front tilt"

    tail = schema["tail.type"]
    assert [e["value"] for e in tail["enum"]] == [
        "conventional",
        "v_tail",
        "inverted_v",
        "twin_boom_h",
    ]
    for path, meta in schema.items():
        assert {"label", "unit", "type", "description"} <= set(meta), path
        assert meta["description"], path
        assert meta["label"], path

    # Every default leaf is described, and nothing else.
    def leaves(doc: dict, prefix: str = "") -> set[str]:
        out: set[str] = set()
        for k, v in doc.items():
            if k == "schema_version":
                continue
            out |= leaves(v, f"{prefix}{k}.") if isinstance(v, dict) else {f"{prefix}{k}"}
        return out

    assert set(schema) == leaves(DEFAULT_DESIGN_PARAMETERS)


def test_mission_schema(auth_client: TestClient) -> None:
    schema = auth_client.get("/api/schema/mission").json()
    assert set(schema) == {
        "scale",
        "target_takeoff_mass_kg",
        "target_endurance_min",
        "cruise_speed_mps",
        "payload_min_g",
        "payload_max_g",
    }
    assert schema["target_takeoff_mass_kg"]["unit"] == "kg"
    assert schema["cruise_speed_mps"]["unit"] == "m/s"
    assert schema["target_endurance_min"]["unit"] == "min"
    assert schema["payload_min_g"]["unit"] == "g" and schema["payload_min_g"]["min"] == 0
    assert [e["value"] for e in schema["scale"]["enum"]] == ["prototype", "final"]


def test_defaults_are_the_single_source_of_truth() -> None:
    assert DesignParameters().model_dump() == DEFAULT_DESIGN_PARAMETERS
    assert Mission().model_dump() == DEFAULT_MISSION
    assert SettingsDocument().model_dump() == DEFAULT_SETTINGS
    assert DesignParameters.model_validate(DEFAULT_DESIGN_PARAMETERS)


def test_upgraders_are_identity_at_current_version() -> None:
    assert upgrade_parameters(DEFAULT_DESIGN_PARAMETERS) == DEFAULT_DESIGN_PARAMETERS
    assert upgrade_mission(DEFAULT_MISSION) == DEFAULT_MISSION
    assert upgrade_settings({"schema_version": 2, "limits": {"warn_mtow_kg": 1}}) == {
        "schema_version": 2,
        "limits": {"warn_mtow_kg": 1},
    }
    assert upgrade_settings(DEFAULT_SETTINGS) == DEFAULT_SETTINGS
    legacy = {k: v for k, v in DEFAULT_MISSION.items() if k != "schema_version"}
    assert upgrade_mission(legacy)["schema_version"] == 1
    assert upgrade_settings(DEFAULT_SETTINGS) is not DEFAULT_SETTINGS


def test_unit_and_label_helpers() -> None:
    assert unit_for("span_mm", None) == "mm"
    assert unit_for("cruise_speed_mps", None) == "m/s"
    assert unit_for("kv_rpm_per_v", None) == "rpm/V"
    assert unit_for("airfoil", None) is None
    assert unit_for("span_mm", "cm") == "cm"
    assert humanize("root_chord_mm") == "Root chord"
    assert humanize("no_load_current_a") == "No load current"
