"""Airfoil library endpoints and the committed XFOIL table."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.engine.airfoils import LIBRARY, POLARS_FILE, SUMMARY_KEYS, naca4, section_geometry

EXPECTED_IDS = [
    "sd7037",
    "sd7062",
    "e387",
    "mh32",
    "s3021",
    "ag35",
    "clarky",
    "naca2412",
    "naca4412",
    "naca0009",
    "naca0012",
]
REYNOLDS = [60_000, 100_000, 200_000, 400_000, 800_000, 1_500_000, 3_000_000]


def test_list_airfoils(auth_client: TestClient) -> None:
    response = auth_client.get("/api/airfoils")
    assert response.status_code == 200
    items = response.json()
    assert [a["id"] for a in items] == EXPECTED_IDS
    for a in items:
        assert set(a) == {
            "id",
            "name",
            "description",
            "use",
            "thickness_pct",
            "x_thickness_pct",
            "camber_pct",
            "x_camber_pct",
            "source",
            "polar_summary",
        }
        assert a["use"] == ("tail" if a["id"] in ("naca0009", "naca0012") else "wing")
        assert a["description"] and a["source"]
        assert [p["re"] for p in a["polar_summary"]] == REYNOLDS
        for p in a["polar_summary"]:
            assert set(p) == set(SUMMARY_KEYS)
            assert 0.5 < p["cl_max"] < 2.2, (a["id"], p)
            assert 0.002 < p["cd_min"] < 0.05, (a["id"], p)
            assert 4.0 < p["cl_alpha_per_rad"] < 9.0, (a["id"], p)


def test_get_airfoil_detail(auth_client: TestClient) -> None:
    response = auth_client.get("/api/airfoils/sd7037")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "sd7037"
    coords = body["coordinates"]
    assert len(coords) > 50
    assert coords[0][0] > 0.99 and coords[-1][0] > 0.99  # Selig order: TE -> LE -> TE
    assert min(c[0] for c in coords) < 0.001
    assert len(body["polars"]) == 7
    polar = body["polars"][2]
    assert polar["re"] == 200_000
    assert len(polar["alpha"]) == len(polar["cl"]) == len(polar["cd"]) == len(polar["cm"])
    assert polar["converged_points"] == len(polar["alpha"])
    assert body["polar_settings"]["n_crit"] == 9.0
    assert auth_client.get("/api/airfoils/nope").status_code == 404


def test_polar_table_covers_every_library_airfoil() -> None:
    table = json.loads(POLARS_FILE.read_text())
    assert table["tool"]["xfoil_version"] == "6.99"
    assert table["settings"]["reynolds"] == REYNOLDS
    assert table["settings"]["alpha_step_deg"] == 0.5
    for entry in LIBRARY:
        polars = table["airfoils"][entry.id]["polars"]
        assert [p["re"] for p in polars] == REYNOLDS, entry.id
        for p in polars:
            assert p["converged_points"] >= 35, (entry.id, p["re"])
            assert p["converged_points"] + p["non_converged_points"] == 45
            assert all(cd > 0 for cd in p["cd"])


def test_known_values() -> None:
    table = json.loads(POLARS_FILE.read_text())
    # Published XFOIL result for NACA 0012, Re 1e6, Ncrit 9, alpha 4: CL 0.43, CD 0.0073.
    got = table["sanity_check"]["got"]
    assert abs(got["cl"] - 0.43) < 0.01 and abs(got["cd"] - 0.0073) < 0.0004
    sd7037 = {p["re"]: p for p in table["airfoils"]["sd7037"]["polars"]}
    assert 1.2 <= sd7037[200_000]["cl_max"] <= 1.4
    # Symmetric sections: zero lift at zero angle, no pitching moment.
    for sym in ("naca0009", "naca0012"):
        for p in table["airfoils"][sym]["polars"]:
            assert abs(p["alpha_zero_lift_deg"]) < 0.05 and abs(p["cm0"]) < 0.005
    # Cambered sections lift at negative angles.
    assert all(p["alpha_zero_lift_deg"] < -1 for p in table["airfoils"]["naca4412"]["polars"])


def test_naca_generator_and_geometry() -> None:
    pts = naca4("2412")
    assert len(pts) == 161  # 160 panels
    assert pts[80] == (0.0, 0.0)
    geo = section_geometry("naca2412")
    assert abs(geo["thickness_pct"] - 12.0) < 0.1
    assert abs(geo["camber_pct"] - 2.0) < 0.05 and abs(geo["x_camber_pct"] - 40) < 1.5
    assert section_geometry("naca0012")["camber_pct"] == 0.0
    sd = section_geometry("sd7037")
    assert 9.0 < sd["thickness_pct"] < 9.5 and 2.8 < sd["camber_pct"] < 3.2
