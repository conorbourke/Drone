from __future__ import annotations

import copy

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import Project

VERSION_KEYS = {
    "id",
    "project_id",
    "number",
    "name",
    "notes",
    "parameters",
    "mission",
    "parent_version_id",
    "created_at",
    "parts_selection",
}


def _save(client: TestClient, pid: int, name: str, **extra: object) -> dict:
    response = client.post(f"/api/projects/{pid}/versions", json={"name": name, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def test_save_version_snapshots_draft_and_updates_lineage(
    auth_client: TestClient, project: dict
) -> None:
    pid = project["id"]
    draft = project["draft"]
    draft["parameters"]["wing"]["span_mm"] = 1900
    auth_client.put(
        f"/api/projects/{pid}/draft",
        json={"parameters": draft["parameters"], "mission": draft["mission"]},
    )
    v1 = _save(auth_client, pid, "v1", notes="first")
    assert set(v1) == VERSION_KEYS
    assert v1["number"] == 1
    assert v1["notes"] == "first"
    assert v1["parent_version_id"] is None
    assert v1["parameters"]["wing"]["span_mm"] == 1900
    assert v1["created_at"].endswith("Z")

    # The draft now points at the version it was saved as.
    draft_after = auth_client.get(f"/api/projects/{pid}/draft").json()
    assert draft_after["based_on_version_id"] == v1["id"]

    v2 = _save(auth_client, pid, "v2")
    assert v2["number"] == 2
    assert v2["parent_version_id"] == v1["id"]
    assert auth_client.get(f"/api/projects/{pid}/draft").json()["based_on_version_id"] == v2["id"]

    listing = auth_client.get(f"/api/projects/{pid}/versions").json()
    assert [v["number"] for v in listing] == [2, 1]
    assert set(listing[0]) == {"id", "number", "name", "notes", "parent_version_id", "created_at"}


def test_explicit_body_has_null_parent_and_leaves_draft_pointer(
    auth_client: TestClient, project: dict
) -> None:
    pid = project["id"]
    v1 = _save(auth_client, pid, "v1")
    params = copy.deepcopy(project["draft"]["parameters"])
    params["wing"]["span_mm"] = 2200
    v2 = _save(auth_client, pid, "explicit", parameters=params, mission=project["draft"]["mission"])
    assert v2["parent_version_id"] is None
    assert v2["parameters"]["wing"]["span_mm"] == 2200
    assert auth_client.get(f"/api/projects/{pid}/draft").json()["based_on_version_id"] == v1["id"]

    half = auth_client.post(
        f"/api/projects/{pid}/versions", json={"name": "half", "parameters": params}
    )
    assert half.status_code == 422
    bad = auth_client.post(
        f"/api/projects/{pid}/versions",
        json={"name": "bad", "parameters": {"layout": "nope"}, "mission": {}},
    )
    assert bad.status_code == 422


def test_version_name_conflict(auth_client: TestClient, project: dict) -> None:
    pid = project["id"]
    _save(auth_client, pid, "v1")
    response = auth_client.post(f"/api/projects/{pid}/versions", json={"name": "v1"})
    assert response.status_code == 409
    assert "v1" in response.json()["detail"]
    # Same name in another project is fine.
    other = auth_client.post("/api/projects", json={"name": "Other"}).json()
    assert _save(auth_client, other["id"], "v1")["number"] == 1


def test_get_patch_version(auth_client: TestClient, project: dict) -> None:
    pid = project["id"]
    v1 = _save(auth_client, pid, "v1")
    _save(auth_client, pid, "v2")
    got = auth_client.get(f"/api/versions/{v1['id']}")
    assert got.status_code == 200
    assert got.json()["project_id"] == pid
    assert got.json()["parameters"]["schema_version"] == 2
    patched = auth_client.patch(f"/api/versions/{v1['id']}", json={"notes": "n", "name": "one"})
    assert patched.status_code == 200
    assert patched.json()["name"] == "one" and patched.json()["notes"] == "n"
    clash = auth_client.patch(f"/api/versions/{v1['id']}", json={"name": "v2"})
    assert clash.status_code == 409
    assert auth_client.get("/api/versions/999999").status_code == 404


def test_duplicate_naming_and_lineage(auth_client: TestClient, project: dict) -> None:
    pid = project["id"]
    v1 = _save(auth_client, pid, "v1")
    first = auth_client.post(f"/api/versions/{v1['id']}/duplicate")
    assert first.status_code == 201
    assert first.json()["name"] == "v1 (copy)"
    assert first.json()["number"] == 2
    assert first.json()["parent_version_id"] == v1["id"]
    assert first.json()["parameters"] == v1["parameters"]
    second = auth_client.post(f"/api/versions/{v1['id']}/duplicate", json={})
    assert second.json()["name"] == "v1 (copy 2)"
    third = auth_client.post(f"/api/versions/{v1['id']}/duplicate")
    assert third.json()["name"] == "v1 (copy 3)"
    named = auth_client.post(f"/api/versions/{v1['id']}/duplicate", json={"name": "Named"})
    assert named.status_code == 201 and named.json()["name"] == "Named"
    clash = auth_client.post(f"/api/versions/{v1['id']}/duplicate", json={"name": "Named"})
    assert clash.status_code == 409
    # Duplicating leaves the draft pointer alone.
    assert auth_client.get(f"/api/projects/{pid}/draft").json()["based_on_version_id"] == v1["id"]


def test_restore_copies_documents_into_draft(auth_client: TestClient, project: dict) -> None:
    pid = project["id"]
    v1 = _save(auth_client, pid, "v1")
    draft = auth_client.get(f"/api/projects/{pid}/draft").json()
    draft["parameters"]["wing"]["span_mm"] = 2500
    draft["mission"]["target_takeoff_mass_kg"] = 3.0
    auth_client.put(
        f"/api/projects/{pid}/draft",
        json={"parameters": draft["parameters"], "mission": draft["mission"]},
    )
    v2 = _save(auth_client, pid, "v2")
    assert v2["parameters"]["wing"]["span_mm"] == 2500

    restored = auth_client.post(f"/api/versions/{v1['id']}/restore")
    assert restored.status_code == 200
    body = restored.json()
    assert set(body) == {
        "parameters",
        "mission",
        "based_on_version_id",
        "updated_at",
        "parts_selection",
    }
    assert body["based_on_version_id"] == v1["id"]
    assert body["parameters"]["wing"]["span_mm"] == v1["parameters"]["wing"]["span_mm"]
    assert body["mission"]["target_takeoff_mass_kg"] == 2.5
    assert auth_client.get(f"/api/projects/{pid}/draft").json()["parameters"] == body["parameters"]


def test_delete_sets_null_and_numbers_stay_monotonic(
    app: FastAPI, auth_client: TestClient, project: dict
) -> None:
    pid = project["id"]
    v1 = _save(auth_client, pid, "v1")
    v2 = _save(auth_client, pid, "v2")  # parent v1, draft points at v2
    assert v2["parent_version_id"] == v1["id"]
    copy_of_v1 = auth_client.post(f"/api/versions/{v1['id']}/duplicate").json()
    assert copy_of_v1["parent_version_id"] == v1["id"]

    assert auth_client.delete(f"/api/versions/{v2['id']}").status_code == 204
    assert auth_client.get(f"/api/projects/{pid}/draft").json()["based_on_version_id"] is None
    assert auth_client.delete(f"/api/versions/{v2['id']}").status_code == 404

    assert auth_client.delete(f"/api/versions/{v1['id']}").status_code == 204
    remaining = auth_client.get(f"/api/projects/{pid}/versions").json()
    assert [v["number"] for v in remaining] == [3]
    assert remaining[0]["parent_version_id"] is None

    v4 = _save(auth_client, pid, "v4")
    assert v4["number"] == 4
    assert v4["parent_version_id"] is None  # draft pointer was cleared by the delete
    with app.state.session_factory() as db:
        assert db.scalar(select(Project.next_version_number).where(Project.id == pid)) == 5


def test_versions_of_unknown_project(auth_client: TestClient) -> None:
    assert auth_client.get("/api/projects/424242/versions").status_code == 404
    assert auth_client.post("/api/projects/424242/versions", json={"name": "x"}).status_code == 404
