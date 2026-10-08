from __future__ import annotations

import copy

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.defaults import DEFAULT_DESIGN_PARAMETERS, DEFAULT_MISSION
from app.models import DesignVersion, Project


def test_create_project_initialises_draft_from_defaults(auth_client: TestClient) -> None:
    response = auth_client.post("/api/projects", json={"name": "  Alpha  ", "description": "d"})
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Alpha"
    assert body["description"] == "d"
    assert body["version_count"] == 0
    assert body["draft"]["parameters"] == DEFAULT_DESIGN_PARAMETERS
    assert body["draft"]["mission"] == DEFAULT_MISSION
    assert body["draft"]["based_on_version_id"] is None
    assert body["draft"]["updated_at"].endswith("Z")
    assert body["created_at"].endswith("Z")


def test_create_project_name_conflict(auth_client: TestClient) -> None:
    assert auth_client.post("/api/projects", json={"name": "Alpha"}).status_code == 201
    response = auth_client.post("/api/projects", json={"name": "Alpha"})
    assert response.status_code == 409
    assert "Alpha" in response.json()["detail"]


def test_create_project_requires_name(auth_client: TestClient) -> None:
    assert auth_client.post("/api/projects", json={"name": "   "}).status_code == 422
    assert auth_client.post("/api/projects", json={}).status_code == 422


def test_list_projects_ordered_with_counts(auth_client: TestClient) -> None:
    a = auth_client.post("/api/projects", json={"name": "A"}).json()
    b = auth_client.post("/api/projects", json={"name": "B"}).json()
    auth_client.post(f"/api/projects/{a['id']}/versions", json={"name": "v1"})
    auth_client.post(f"/api/projects/{a['id']}/versions", json={"name": "v2"})
    listing = auth_client.get("/api/projects").json()
    assert [p["id"] for p in listing] == [a["id"], b["id"]] or [p["id"] for p in listing] == [
        b["id"],
        a["id"],
    ]
    by_id = {p["id"]: p for p in listing}
    assert by_id[a["id"]]["version_count"] == 2
    assert by_id[a["id"]]["latest_version"]["number"] == 2
    assert by_id[a["id"]]["latest_version"]["name"] == "v2"
    assert by_id[b["id"]]["version_count"] == 0
    assert by_id[b["id"]]["latest_version"] is None
    assert set(listing[0]) == {
        "id",
        "name",
        "description",
        "created_at",
        "updated_at",
        "version_count",
        "latest_version",
    }


def test_get_patch_delete_project(auth_client: TestClient, project: dict) -> None:
    pid = project["id"]
    got = auth_client.get(f"/api/projects/{pid}")
    assert got.status_code == 200
    assert set(got.json()) == {
        "id",
        "name",
        "description",
        "created_at",
        "updated_at",
        "draft",
        "version_count",
    }

    patched = auth_client.patch(f"/api/projects/{pid}", json={"name": "Renamed"})
    assert patched.status_code == 200
    assert patched.json()["name"] == "Renamed"
    assert patched.json()["description"] == ""
    only_desc = auth_client.patch(f"/api/projects/{pid}", json={"description": "New"})
    assert only_desc.json()["name"] == "Renamed"
    assert only_desc.json()["description"] == "New"

    other = auth_client.post("/api/projects", json={"name": "Other"}).json()
    clash = auth_client.patch(f"/api/projects/{other['id']}", json={"name": "Renamed"})
    assert clash.status_code == 409

    assert auth_client.delete(f"/api/projects/{pid}").status_code == 204
    assert auth_client.get(f"/api/projects/{pid}").status_code == 404
    assert auth_client.delete(f"/api/projects/{pid}").status_code == 404


def test_delete_project_cascades_versions_at_ddl_level(
    app: FastAPI, auth_client: TestClient, project: dict
) -> None:
    pid = project["id"]
    v = auth_client.post(f"/api/projects/{pid}/versions", json={"name": "v1"}).json()
    auth_client.post(f"/api/versions/{v['id']}/duplicate")
    assert auth_client.delete(f"/api/projects/{pid}").status_code == 204
    with app.state.session_factory() as db:
        assert db.scalars(select(DesignVersion).where(DesignVersion.project_id == pid)).all() == []
        assert db.get(Project, pid) is None


def test_unknown_project_is_404(auth_client: TestClient) -> None:
    assert auth_client.get("/api/projects/999999").status_code == 404
    assert auth_client.get("/api/projects/999999/draft").status_code == 404


def test_draft_get_and_put(auth_client: TestClient, project: dict) -> None:
    pid = project["id"]
    draft = auth_client.get(f"/api/projects/{pid}/draft").json()
    assert set(draft) == {"parameters", "mission", "based_on_version_id", "updated_at"}
    params = copy.deepcopy(draft["parameters"])
    mission = copy.deepcopy(draft["mission"])
    params["wing"]["span_mm"] = 2000
    mission["cruise_speed_mps"] = 18
    saved = auth_client.put(
        f"/api/projects/{pid}/draft", json={"parameters": params, "mission": mission}
    )
    assert saved.status_code == 200
    assert saved.json()["parameters"]["wing"]["span_mm"] == 2000
    assert saved.json()["mission"]["cruise_speed_mps"] == 18
    assert saved.json()["updated_at"] >= draft["updated_at"]
    again = auth_client.get(f"/api/projects/{pid}/draft").json()
    assert again["parameters"] == saved.json()["parameters"]


def test_draft_accepts_over_limit_takeoff_mass(auth_client: TestClient, project: dict) -> None:
    """The 24 kg design limit is a check, never an input constraint."""
    pid = project["id"]
    draft = project["draft"]
    draft["mission"]["target_takeoff_mass_kg"] = 30.0
    response = auth_client.put(
        f"/api/projects/{pid}/draft",
        json={"parameters": draft["parameters"], "mission": draft["mission"]},
    )
    assert response.status_code == 200
    assert response.json()["mission"]["target_takeoff_mass_kg"] == 30.0


def test_draft_shape_validation(auth_client: TestClient, project: dict) -> None:
    pid = project["id"]
    base = project["draft"]

    def put(params: dict, mission: dict) -> tuple[int, str]:
        r = auth_client.put(
            f"/api/projects/{pid}/draft", json={"parameters": params, "mission": mission}
        )
        return r.status_code, r.text

    params = copy.deepcopy(base["parameters"])
    params["wing"]["tip_chord_mm"] = params["wing"]["root_chord_mm"] + 1
    status, text = put(params, base["mission"])
    assert status == 422 and "tip chord" in text

    params = copy.deepcopy(base["parameters"])
    params["wing"]["x_le_mm"] = params["fuselage"]["length_mm"]
    status, text = put(params, base["mission"])
    assert status == 422 and "fuselage" in text

    params = copy.deepcopy(base["parameters"])
    params["wing"]["span_mm"] = -5
    assert put(params, base["mission"])[0] == 422

    params = copy.deepcopy(base["parameters"])
    params["layout"] = "hexacopter"
    assert put(params, base["mission"])[0] == 422

    mission = copy.deepcopy(base["mission"])
    mission["payload_max_g"] = mission["payload_min_g"] - 1
    status, text = put(base["parameters"], mission)
    assert status == 422 and "payload" in text

    mission = copy.deepcopy(base["mission"])
    mission["target_takeoff_mass_kg"] = 0
    assert put(base["parameters"], mission)[0] == 422

    # Missing nested fields fall back to defaults (new fields must have defaults).
    params = copy.deepcopy(base["parameters"])
    del params["tail"]
    status, _ = put(params, base["mission"])
    assert status == 200
