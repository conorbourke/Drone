"""Claude image readings: fake-response seam, ratio-to-millimetre conversion, clamping,
layout mapping, refusals, missing key and the typed SDK error chain."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.assistant import vision
from app.assistant.proposal import build_proposal, normalise_layout
from app.config import Settings
from app.defaults import DEFAULT_DESIGN_PARAMETERS
from tests.test_images import make_image, upload

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FAKE_RESPONSE = FIXTURES / "vision_fake_response.json"
FAKE_REFUSAL = FIXTURES / "vision_fake_refusal.json"
SPAN_REF = {"parameter": "wing.span_mm", "value_mm": 2000}


@pytest.fixture
def fake_answer() -> dict[str, Any]:
    return json.loads(FAKE_RESPONSE.read_text())


@pytest.fixture
def with_fake(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> Path:
    monkeypatch.setattr(settings, "claude_fake_response_file", FAKE_RESPONSE)
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    return FAKE_RESPONSE


@pytest.fixture
def no_claude(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    monkeypatch.setattr(settings, "claude_fake_response_file", None)
    monkeypatch.setattr(settings, "anthropic_api_key", None)


@pytest.fixture
def with_key(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> str:
    key = "sk-ant-test-key-never-echoed"
    monkeypatch.setattr(settings, "claude_fake_response_file", None)
    monkeypatch.setattr(settings, "anthropic_api_key", SecretStr(key))
    return key


def _images(client: TestClient, pid: int, n: int = 2) -> list[int]:
    views = ["top", "side", "front", "three_quarter", "other", "other"]
    return [
        upload(
            client, pid, make_image("JPEG", (2400, 1200)), "p.jpg", "image/jpeg", views[i]
        ).json()["id"]
        for i in range(n)
    ]


def _read(client: TestClient, pid: int, **body: Any):
    return client.post(f"/api/projects/{pid}/image-readings", json={"reference": SPAN_REF, **body})


# --- through the API with the fake response -------------------------------------------------


def test_reading_with_fake_response(
    auth_client: TestClient, project: dict, with_fake: Path
) -> None:
    pid = project["id"]
    ids = _images(auth_client, pid)
    response = _read(auth_client, pid)
    assert response.status_code == 201, response.text
    reading = response.json()
    assert reading["status"] == "ok" and reading["error"] is None
    assert reading["image_ids"] == ids
    assert reading["reference"] == {"parameter": "wing.span_mm", "value_mm": 2000.0}
    assert reading["model"] == "claude-opus-5-5"
    assert reading["usage"]["fake"] is True
    proposal = reading["proposal"]
    assert proposal["layout"] == "front_tilt"
    assert proposal["layout_confidence"] == 0.8
    assert proposal["layout_reason"].startswith("Four motors")
    p = proposal["parameters"]
    # Ratios to the wingspan become millimetres.
    assert p["wing.span_mm"] == {
        "value": 2000,
        "unit": "mm",
        "confidence": 1.0,
        "note": "The wingspan you entered.",
    }
    assert p["fuselage.length_mm"]["value"] == 960  # 0.48 x 2000
    assert p["wing.root_chord_mm"]["value"] == 260  # 0.13 x 2000
    assert p["wing.tip_chord_mm"]["value"] == 170
    assert p["booms.x_offset_mm"]["value"] == -280
    # Ratios to the fuselage length use the derived fuselage length.
    assert p["wing.x_le_mm"]["value"] == round(0.34 * 960)
    assert p["nose_bay.length_mm"]["value"] == 192
    # Angles, counts and choices pass through.
    assert p["tail.v_angle_deg"] == {
        "value": 35.0,
        "unit": "°",
        "confidence": 0.55,
        "note": "Front view, panels hang about 35 degrees below horizontal.",
    }
    assert p["propulsion.prop_blades"]["value"] == 2
    assert p["tail.type"]["value"] == "inverted_v"
    assert p["fuselage.cross_section"]["value"] == "ellipse"
    # Not visible: omitted, so the draft keeps its value; Claude's list is passed on.
    assert "battery.x_mm" not in p and "propulsion.prop_pitch_mm" not in p
    assert "Not visible: propeller pitch" in proposal["unmapped_notes"]
    assert proposal["warnings"] == []

    listed = auth_client.get(f"/api/projects/{pid}/image-readings").json()
    assert [r["id"] for r in listed] == [reading["id"]]


def test_readings_newest_first_and_image_choice(
    auth_client: TestClient, project: dict, with_fake: Path
) -> None:
    pid = project["id"]
    ids = _images(auth_client, pid, 3)
    first = _read(auth_client, pid, image_ids=[ids[2], ids[0]]).json()
    assert first["image_ids"] == [ids[2], ids[0]]
    second = _read(auth_client, pid).json()
    listed = auth_client.get(f"/api/projects/{pid}/image-readings").json()
    assert [r["id"] for r in listed] == [second["id"], first["id"]]


def test_reading_needs_one_to_four_project_images(
    auth_client: TestClient, project: dict, with_fake: Path
) -> None:
    pid = project["id"]
    none = _read(auth_client, pid)
    assert none.status_code == 422
    assert "at least one" in none.json()["detail"]
    ids = _images(auth_client, pid, 5)
    too_many = _read(auth_client, pid)
    assert too_many.status_code == 422
    assert "at most 4" in too_many.json()["detail"]
    other = auth_client.post("/api/projects", json={"name": "Other"}).json()
    foreign = _images(auth_client, other["id"], 1)
    wrong = _read(auth_client, pid, image_ids=[ids[0], foreign[0]])
    assert wrong.status_code == 422
    assert _read(auth_client, pid, image_ids=ids[:4]).status_code == 201


def test_reference_validation(auth_client: TestClient, project: dict, with_fake: Path) -> None:
    pid = project["id"]
    _images(auth_client, pid, 1)
    for ref in (
        {"parameter": "wing.root_chord_mm", "value_mm": 200},
        {"parameter": "wing.span_mm", "value_mm": 0},
        {"parameter": "wing.span_mm"},
    ):
        response = auth_client.post(f"/api/projects/{pid}/image-readings", json={"reference": ref})
        assert response.status_code == 422, ref


def test_fuselage_length_as_reference(
    auth_client: TestClient, project: dict, with_fake: Path
) -> None:
    pid = project["id"]
    _images(auth_client, pid, 1)
    response = auth_client.post(
        f"/api/projects/{pid}/image-readings",
        json={"reference": {"parameter": "fuselage.length_mm", "value_mm": 960}},
    )
    p = response.json()["proposal"]["parameters"]
    assert p["fuselage.length_mm"]["value"] == 960 and p["fuselage.length_mm"]["confidence"] == 1
    assert p["wing.span_mm"]["value"] == 2000  # 960 / 0.48
    assert p["wing.span_mm"]["confidence"] == 0.75
    assert p["wing.root_chord_mm"]["value"] == 260


def test_refusal_from_fake(
    auth_client: TestClient, project: dict, monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    monkeypatch.setattr(settings, "claude_fake_response_file", FAKE_REFUSAL)
    pid = project["id"]
    _images(auth_client, pid, 1)
    response = _read(auth_client, pid)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "refused"
    assert body["proposal"] is None
    assert body["error"].startswith("Claude declined")
    assert auth_client.get(f"/api/projects/{pid}/image-readings").json()[0]["status"] == "refused"


def test_missing_key_gives_plain_503(
    auth_client: TestClient, project: dict, no_claude: None
) -> None:
    pid = project["id"]
    _images(auth_client, pid, 1)
    response = _read(auth_client, pid)
    assert response.status_code == 503
    assert response.json() == {
        "detail": "Add the ANTHROPIC_API_KEY secret in GitHub and redeploy to enable image reading"
    }
    assert auth_client.get(f"/api/projects/{pid}/image-readings").json() == []
    status = auth_client.get("/api/image-readings/status").json()
    assert status["available"] is False and status["message"] == response.json()["detail"]


def test_status_when_available(auth_client: TestClient, with_fake: Path) -> None:
    status = auth_client.get("/api/image-readings/status").json()
    assert status == {"available": True, "model": "claude-opus-5-5", "message": None}


def test_fake_file_ignored_in_production(tmp_path: Path) -> None:
    from tests.conftest import make_settings

    prod = make_settings(
        tmp_path,
        app_env="production",
        claude_fake_response_file=FAKE_RESPONSE,
    )
    assert prod.fake_claude_response_file is None
    assert not vision.is_available(prod)
    with pytest.raises(vision.VisionNotConfigured):
        vision.read_images(prod, [], "wing.span_mm")


# --- the SDK path, with the network call replaced ------------------------------------------


def _message(text: str | None = None, stop_reason: str = "end_turn", **extra: Any) -> Any:
    content = [SimpleNamespace(type="thinking", thinking="")]
    if text is not None:
        content.append(SimpleNamespace(type="text", text=text))
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=content,
        model="claude-opus-5-5",
        usage=SimpleNamespace(input_tokens=5123, output_tokens=812, iterations=None),
        stop_details=extra.get("stop_details"),
    )


def test_sdk_request_shape_and_success(
    auth_client: TestClient,
    project: dict,
    with_key: str,
    monkeypatch: pytest.MonkeyPatch,
    fake_answer: dict,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_send(client: anthropic.Anthropic, **kwargs: Any) -> Any:
        calls.append(kwargs)
        return _message(json.dumps(fake_answer))

    monkeypatch.setattr(vision, "_send", fake_send)
    pid = project["id"]
    _images(auth_client, pid, 2)
    response = _read(auth_client, pid)
    assert response.status_code == 201, response.text
    assert response.json()["usage"] == {"input_tokens": 5123, "output_tokens": 812}
    assert response.json()["proposal"]["parameters"]["wing.root_chord_mm"]["value"] == 260

    (kwargs,) = calls
    assert kwargs["model"] == "claude-opus-5-5"
    assert kwargs["thinking"] == {"type": "adaptive"}
    assert kwargs["output_config"]["effort"] == "high"
    assert kwargs["output_config"]["format"]["type"] == "json_schema"
    assert kwargs["output_config"]["format"]["schema"] == vision.ANSWER_SCHEMA
    assert kwargs["betas"] == ["server-side-fallback-2026-07-01"]
    assert kwargs["fallbacks"] == "default"
    for forbidden in ("temperature", "top_p", "top_k", "tool_choice", "tools"):
        assert forbidden not in kwargs
    assert "budget_tokens" not in json.dumps(kwargs["thinking"])
    (message,) = kwargs["messages"]
    assert message["role"] == "user"
    blocks = message["content"]
    assert [b["type"] for b in blocks] == ["text", "image", "text", "image", "text"]
    assert blocks[0]["text"] == "Image 1: top view (plan)."
    assert blocks[2]["text"] == "Image 2: side view."
    import base64
    import io

    from PIL import Image

    img = Image.open(io.BytesIO(base64.b64decode(blocks[1]["source"]["data"])))
    assert blocks[1]["source"]["media_type"] == "image/jpeg"
    assert img.format == "JPEG" and max(img.size) == 1568  # 2400 px resized


def test_sdk_refusal_is_stored(
    auth_client: TestClient, project: dict, with_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        vision,
        "_send",
        lambda client, **kw: _message(
            None, "refusal", stop_details=SimpleNamespace(category="cyber")
        ),
    )
    pid = project["id"]
    _images(auth_client, pid, 1)
    response = _read(auth_client, pid)
    assert response.status_code == 200
    assert response.json()["status"] == "refused"
    assert response.json()["error"].endswith("(category: cyber)")


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (lambda: _message("{not json"), "could not be read"),
        (lambda: _message(json.dumps({"layout": "x"})), "could not be read"),
        (lambda: _message(None, "max_tokens"), "cut off"),
        (lambda: _message(None), "empty"),
    ],
)
def test_sdk_bad_answers_are_502(
    auth_client: TestClient,
    project: dict,
    with_key: str,
    monkeypatch: pytest.MonkeyPatch,
    message: Any,
    expected: str,
) -> None:
    monkeypatch.setattr(vision, "_send", lambda client, **kw: message())
    pid = project["id"]
    _images(auth_client, pid, 1)
    response = _read(auth_client, pid)
    assert response.status_code == 502
    assert expected in response.json()["detail"]
    stored = auth_client.get(f"/api/projects/{pid}/image-readings").json()[0]
    assert stored["status"] == "error" and expected in stored["error"]


def _status_error(cls: type[anthropic.APIStatusError], code: int) -> anthropic.APIStatusError:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx2.Response(code, request=request, json={"type": "error"})
    return cls("boom sk-ant-test-key-never-echoed", response=response, body=None)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (lambda: _status_error(anthropic.AuthenticationError, 401), "did not accept the API key"),
        (lambda: _status_error(anthropic.NotFoundError, 404), "is not available"),
        (lambda: _status_error(anthropic.RateLimitError, 429), "too many requests"),
        (lambda: _status_error(anthropic.InternalServerError, 500), "temporarily unavailable"),
        (lambda: _status_error(anthropic.BadRequestError, 400), "could not process"),
        (
            lambda: anthropic.APITimeoutError(
                request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
            ),
            "too long",
        ),
        (
            lambda: anthropic.APIConnectionError(
                request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
            ),
            "could not reach Claude",
        ),
    ],
)
def test_sdk_errors_become_plain_502(
    auth_client: TestClient,
    project: dict,
    with_key: str,
    monkeypatch: pytest.MonkeyPatch,
    error: Any,
    expected: str,
) -> None:
    def raising(client: anthropic.Anthropic, **kwargs: Any) -> Any:
        raise error()

    monkeypatch.setattr(vision, "_send", raising)
    pid = project["id"]
    _images(auth_client, pid, 1)
    response = _read(auth_client, pid)
    assert response.status_code == 502
    assert expected in response.json()["detail"]
    assert with_key not in response.text
    listed = auth_client.get(f"/api/projects/{pid}/image-readings")
    assert with_key not in listed.text


# --- proposal maths ------------------------------------------------------------------------


def _answer(payload: dict[str, Any]) -> vision.VisionAnswer:
    return vision.VisionAnswer.model_validate(payload)


def test_clamping_to_plausible_ranges(fake_answer: dict) -> None:
    payload = copy.deepcopy(fake_answer)
    by_key = {m["key"]: m for m in payload["measurements"]}
    by_key["wing.root_chord_mm"]["value"] = 0.01  # 1 % of span: below 4 %
    by_key["wing.sweep_deg"]["value"] = 50.0  # above 35 degrees
    by_key["propulsion.prop_blades"]["value"] = 9
    payload["fuselage_length_to_span"]["value"] = 3.0  # above 150 %
    proposal = build_proposal(
        _answer(payload), "wing.span_mm", 2000, copy.deepcopy(DEFAULT_DESIGN_PARAMETERS)
    )
    p = proposal["parameters"]
    assert p["wing.root_chord_mm"]["value"] == 80  # 4 % of 2000
    assert p["wing.sweep_deg"]["value"] == 35.0
    assert p["propulsion.prop_blades"]["value"] == 6
    assert p["fuselage.length_mm"]["value"] == 3000
    # The tip estimate (170 mm) is now larger than the clamped root: set equal to it.
    assert p["wing.tip_chord_mm"]["value"] == 80
    text = " ".join(proposal["warnings"])
    assert "Root chord: Claude's estimate (1.0 % of the wingspan)" in text
    assert "clamped to 4.0 %" in text
    assert "Sweep: Claude's estimate (50°) was outside the plausible range -5° to 35°" in text
    assert "Fuselage length" in text
    assert "Tip chord" in text


def test_layout_mapping() -> None:
    assert normalise_layout("front_tilt") == "front_tilt"
    assert normalise_layout("Front-Tilt") == "front_tilt"
    assert normalise_layout("tiltrotor") == "front_tilt"
    assert normalise_layout("rear tilt") == "rear_tilt"
    assert normalise_layout("Quad + Pusher") == "quad_pusher"
    assert normalise_layout("quadplane") == "quad_pusher"
    assert normalise_layout("tailsitter") is None


def test_unsupported_layout_keeps_current(fake_answer: dict) -> None:
    payload = copy.deepcopy(fake_answer)
    payload["layout"]["value"] = "tailsitter"
    draft = copy.deepcopy(DEFAULT_DESIGN_PARAMETERS)
    draft["layout"] = "quad_pusher"
    proposal = build_proposal(_answer(payload), "wing.span_mm", 2000, draft)
    assert proposal["layout"] == "quad_pusher"
    assert proposal["layout_confidence"] == 0.0
    assert "tailsitter" in proposal["warnings"][0]


def test_unknown_keys_and_choices_are_unmapped(fake_answer: dict) -> None:
    payload = copy.deepcopy(fake_answer)
    payload["measurements"].append(
        {"key": "wing.winglet_mm", "value": 0.1, "confidence": 0.5, "note": "Winglets."}
    )
    payload["choices"][0]["value"] = "t_tail"
    proposal = build_proposal(
        _answer(payload), "wing.span_mm", 2000, copy.deepcopy(DEFAULT_DESIGN_PARAMETERS)
    )
    assert "tail.type" not in proposal["parameters"]
    assert any("wing.winglet_mm" in n for n in proposal["unmapped_notes"])
    assert any("t_tail" in n for n in proposal["unmapped_notes"])
    assert any("Tail type" in w for w in proposal["warnings"])


def test_inconsistent_values_produce_a_warning(fake_answer: dict) -> None:
    payload = copy.deepcopy(fake_answer)
    by_key = {m["key"]: m for m in payload["measurements"]}
    by_key["wing.x_le_mm"]["value"] = 0.7  # wing root would run past the fuselage end
    by_key["wing.root_chord_mm"]["value"] = 0.25
    proposal = build_proposal(
        _answer(payload), "wing.span_mm", 2000, copy.deepcopy(DEFAULT_DESIGN_PARAMETERS)
    )
    assert any("would not pass the design checks" in w for w in proposal["warnings"])
    assert any("extend past the end of the fuselage" in w for w in proposal["warnings"])
