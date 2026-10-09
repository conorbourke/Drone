"""Phase 3 assistant: SSE conversation through the scripted fake, tools, budgets, storage, and
the real-client request shape with the SDK replaced."""

from __future__ import annotations

import itertools
import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anthropic
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select

from app.assistant import chat
from app.assistant.tools import TOOLS
from app.models import AssistantMessage, User

FIXTURES = Path(__file__).parent / "fixtures"
SCRIPT = FIXTURES / "chat_fake_script.json"


def parse_sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    events = []
    for chunk in text.strip().split("\n\n"):
        lines = chunk.split("\n")
        assert lines[0].startswith("event: "), chunk
        assert lines[1].startswith("data: "), chunk
        events.append((lines[0][7:], json.loads(lines[1][6:])))
    return events


@pytest.fixture
def use_settings(app: FastAPI) -> Iterator[Any]:
    """Swap the app's settings for one test (the session app is shared)."""
    original = app.state.settings

    def apply(**update: Any) -> None:
        app.state.settings = original.model_copy(update=update)

    yield apply
    app.state.settings = original


def write_script(tmp_path: Path, responses: list[dict[str, Any]]) -> Path:
    path = tmp_path / "script.json"
    path.write_text(json.dumps({"responses": responses}))
    return path


def send(client: TestClient, project_id: int, text: str = "What if the wing is longer?") -> Any:
    return client.post(f"/api/projects/{project_id}/assistant/messages", json={"text": text})


def test_missing_key_is_a_plain_503(auth_client: TestClient, project: dict) -> None:
    status = auth_client.get("/api/assistant/status").json()
    assert status["available"] is False
    assert "ANTHROPIC_API_KEY" in status["message"]
    response = send(auth_client, project["id"])
    assert response.status_code == 503
    assert response.json() == {"detail": chat.MISSING_KEY_MESSAGE}
    thread = auth_client.get(f"/api/projects/{project['id']}/assistant/messages").json()
    assert thread == {"thread_id": None, "available": False, "messages": []}


def test_fake_conversation_with_quick_analysis_and_proposal(
    app: FastAPI, auth_client: TestClient, project: dict, use_settings: Any
) -> None:
    use_settings(claude_fake_chat_file=SCRIPT)
    assert auth_client.get("/api/assistant/status").json()["available"] is True
    response = send(auth_client, project["id"], "What if I use a 6000 mAh battery?")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store"
    events = parse_sse(response.text)
    names = [name for name, _ in events]
    assert names[-1] == "done"
    assert names.count("text") > 3  # streamed in pieces
    tool_calls = [data for name, data in events if name == "tool_call"]
    assert [t["name"] for t in tool_calls] == ["run_quick_analysis", "propose_change"]
    assert tool_calls[0]["label"] == "Running a quick analysis…"
    proposals = [data for name, data in events if name == "proposal"]
    assert proposals == [
        {
            "patch": {"battery.capacity_mah": 6000},
            "summary": "Increase the battery capacity from 5000 mAh to 6000 mAh (quick analysis "
            "above).",
            "base": "draft",
        }
    ]
    assert names.index("tool_call") < names.index("proposal") < names.index("done")
    text = "".join(data["text"] for name, data in events if name == "text")
    assert "(no result)" not in text

    # The answer quotes the tool result: the numbers are the ones the tool returned.
    with app.state.session_factory() as db:
        rows = db.scalars(select(AssistantMessage).order_by(AssistantMessage.id)).all()
    results = [r for r in rows if (r.meta or {}).get("kind") == "tool_results"]
    quick = json.loads(results[0].content[0]["content"])
    after = quick["after"]["key_numbers"]["endurance_cruise"]
    before = quick["before"]["key_numbers"]["endurance_cruise"]
    assert after["value"] != before["value"]
    assert chat._format_value(after) in text
    assert chat._format_value(before) in text
    assert quick["changes"] == {"battery.capacity_mah": {"from": 5000.0, "to": 6000}}
    assert chat._format_value(after) != chat._format_value(before)
    assert "battery.\n\nThe quick analysis" in text  # responses are separated
    assert "visual line of sight" in text

    # Stored append-only, every tool call followed by its results.
    roles = [(r.role, (r.meta or {}).get("kind")) for r in rows]
    assert roles == [
        ("user", "user_text"),
        ("assistant", "assistant"),
        ("user", "tool_results"),
        ("assistant", "assistant"),
        ("user", "tool_results"),
        ("assistant", "assistant"),
    ]
    for i, row in enumerate(rows):
        uses = [b["id"] for b in row.content if b.get("type") == "tool_use"]
        if uses:
            answered = [b["tool_use_id"] for b in rows[i + 1].content]
            assert answered == uses

    thread = auth_client.get(f"/api/projects/{project['id']}/assistant/messages").json()
    assert thread["available"] is True
    messages = thread["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["text"] == "What if I use a 6000 mAh battery?"
    assistant = messages[1]
    assert [c["label"] for c in assistant["tool_calls"]] == [
        "Running a quick analysis…",
        "Preparing a suggested change…",
    ]
    assert assistant["proposals"] == proposals
    assert chat._format_value(after) in assistant["text"]

    # The proposal can be tried as a new version.
    response = auth_client.post(
        f"/api/projects/{project['id']}/versions/from-patch",
        json={"name": "Assistant proposal", "patch": proposals[0]["patch"]},
    )
    assert response.status_code == 201

    # A second message continues the same thread; clearing empties it.
    events = parse_sse(send(auth_client, project["id"], "And again?").text)
    assert events[-1][0] == "done"
    thread = auth_client.get(f"/api/projects/{project['id']}/assistant/messages").json()
    assert [m["role"] for m in thread["messages"]] == ["user", "assistant", "user", "assistant"]
    response = auth_client.delete(f"/api/projects/{project['id']}/assistant/messages")
    assert response.status_code == 204
    thread = auth_client.get(f"/api/projects/{project['id']}/assistant/messages").json()
    assert thread["messages"] == [] and thread["thread_id"] is None


def test_history_sent_to_the_model_is_append_only(
    auth_client: TestClient, project: dict, use_settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[list[dict[str, Any]]] = []
    original = chat.FakeModel.respond

    def spy(self: chat.FakeModel, messages: list[dict[str, Any]]) -> Any:
        seen.append(json.loads(json.dumps(messages)))
        return original(self, messages)

    monkeypatch.setattr(chat.FakeModel, "respond", spy)
    use_settings(claude_fake_chat_file=SCRIPT)
    send(auth_client, project["id"])
    send(auth_client, project["id"], "Second question")
    assert len(seen) == 6
    for earlier, later in itertools.pairwise(seen):
        assert later[: len(earlier)] == earlier  # every request extends the previous one
    assert seen[3][-1] == {"role": "user", "content": [{"type": "text", "text": "Second question"}]}


def test_tool_budget_per_turn(
    auth_client: TestClient, project: dict, use_settings: Any, tmp_path: Path
) -> None:
    many = [{"type": "tool_use", "name": "get_parts_list", "input": {}} for _ in range(8)]
    script = write_script(
        tmp_path,
        [
            {"stop_reason": "tool_use", "content": many},
            {"stop_reason": "end_turn", "content": [{"type": "text", "text": "Done."}]},
        ],
    )
    use_settings(claude_fake_chat_file=script)
    events = parse_sse(send(auth_client, project["id"]).text)
    assert sum(1 for name, _ in events if name == "tool_call") == chat.MAX_TOOL_CALLS == 6
    assert events[-1] == ("done", events[-1][1])
    assert events[-1][1]["tool_calls"] == 6
    thread = auth_client.get(f"/api/projects/{project['id']}/assistant/messages").json()
    calls = thread["messages"][1]["tool_calls"]
    assert len(calls) == 8
    assert [c["is_error"] for c in calls] == [False] * 6 + [True] * 2


def test_refusal_is_not_stored_as_an_answer(
    app: FastAPI, auth_client: TestClient, project: dict, use_settings: Any, tmp_path: Path
) -> None:
    script = write_script(
        tmp_path,
        [
            {
                "stop_reason": "refusal",
                "stop_details": {"category": "bio"},
                "content": [{"type": "text", "text": "partial"}],
            }
        ],
    )
    use_settings(claude_fake_chat_file=script)
    events = parse_sse(send(auth_client, project["id"]).text)
    assert events[-1][0] == "error"
    assert events[-1][1]["code"] == "refusal"
    assert "(category: bio)" in events[-1][1]["message"]
    with app.state.session_factory() as db:
        rows = db.scalars(select(AssistantMessage)).all()
    assert [r.role for r in rows] == ["user", "notice"]
    thread = auth_client.get(f"/api/projects/{project['id']}/assistant/messages").json()
    assert thread["messages"][-1]["role"] == "notice"
    assert thread["messages"][-1]["code"] == "refusal"


def test_truncated_tool_call_is_never_run(
    app: FastAPI, auth_client: TestClient, project: dict, use_settings: Any, tmp_path: Path
) -> None:
    script = write_script(
        tmp_path,
        [
            {
                "stop_reason": "max_tokens",
                "content": [
                    {"type": "text", "text": "Let me check"},
                    {"type": "tool_use", "name": "propose_change", "input": {"summary": "x"}},
                ],
            }
        ],
    )
    use_settings(claude_fake_chat_file=script)
    events = parse_sse(send(auth_client, project["id"]).text)
    assert "tool_call" not in [name for name, _ in events]
    assert events[-1][0] == "error" and events[-1][1]["code"] == "max_tokens"
    with app.state.session_factory() as db:
        rows = db.scalars(select(AssistantMessage).order_by(AssistantMessage.id)).all()
    assert [b["type"] for b in rows[1].content] == ["text"]


def test_tools_are_owner_and_project_scoped(
    app: FastAPI, auth_client: TestClient, project: dict, settings: Any
) -> None:
    from app.assistant.tools import ToolContext

    other = auth_client.post("/api/projects", json={"name": "Other"}).json()
    auth_client.post(f"/api/projects/{other['id']}/versions", json={"name": "secret"})
    with app.state.session_factory() as db:
        owner_id = db.scalar(select(User.id).where(User.is_owner.is_(True)))
    ctx = ToolContext(app.state.session_factory, settings, owner_id, project["id"])
    versions = json.loads(ctx.execute("get_versions", {}).content)
    assert versions["versions"] == []
    assert ctx.execute("get_design", {"source": "version", "version_number": 1}).is_error
    wrong_owner = ToolContext(app.state.session_factory, settings, 999, project["id"])
    outcome = wrong_owner.execute("get_design", {"source": "draft", "version_number": None})
    assert outcome.is_error and "no longer exists" in outcome.content
    assert ctx.execute("nope", {}).is_error
    bad = ctx.execute(
        "run_quick_analysis", {"parameter_changes": [{"path": "wing.nope", "value": 1}]}
    )
    assert bad.is_error and "Unknown parameter" in bad.content
    latest = json.loads(ctx.execute("get_latest_analysis", {"section": "summary"}).content)
    assert latest["available"] is False
    parts = json.loads(ctx.execute("get_parts_list", {}).content)
    assert parts["parts"] == [] and "catalogue is empty" in parts["note"]
    flights = json.loads(ctx.execute("get_flight_comparisons", {}).content)
    assert flights["flights"] == [] and "Phase 6" in flights["note"]
    tier1 = json.loads(ctx.execute("get_tier1_estimates", {}).content)
    assert tier1["valid"] is True and tier1["key_numbers"]["endurance_cruise"]["unit"] == "min"


def test_tool_definitions_are_strict() -> None:
    def walk(schema: Any) -> Iterator[dict[str, Any]]:
        if isinstance(schema, dict):
            if schema.get("type") == "object":
                yield schema
            for value in schema.values():
                yield from walk(value)
        elif isinstance(schema, list):
            for value in schema:
                yield from walk(value)

    names = [t["name"] for t in TOOLS]
    assert names == [
        "get_design",
        "get_latest_analysis",
        "get_tier1_estimates",
        "get_parts_list",
        "get_versions",
        "get_flight_comparisons",
        "run_quick_analysis",
        "propose_change",
    ]
    for tool in TOOLS:
        assert tool["strict"] is True
        for obj in walk(tool["input_schema"]):
            assert obj["additionalProperties"] is False
            assert set(obj.get("required", [])) == set(obj["properties"])


def test_sanitize_for_echo_after_a_fallback() -> None:
    content = [
        {"type": "thinking", "thinking": "", "signature": "a"},
        {"type": "text", "text": "partial"},
        {"type": "tool_use", "id": "t1", "name": "get_design", "input": {}},
        {"type": "server_tool_use", "id": "s1", "name": "web_search", "input": {}},
        {"type": "fallback", "from": {"model": "a"}, "to": {"model": "b"}},
        {"type": "thinking", "thinking": "", "signature": "b"},
        {"type": "tool_use", "id": "t2", "name": "get_design", "input": {}},
    ]
    assert [b.get("id") or b["type"] for b in chat.sanitize_for_echo(content)] == [
        "text",
        "fallback",
        "thinking",
        "t2",
    ]
    plain = [{"type": "thinking", "thinking": "", "signature": "x"}, {"type": "text", "text": "y"}]
    assert chat.sanitize_for_echo(plain) == plain


# ---------------------------------------------------------------------------
# The real client, with the SDK's stream replaced
# ---------------------------------------------------------------------------


class _Block(SimpleNamespace):
    def to_dict(self) -> dict[str, Any]:
        return dict(vars(self))


class _Stream:
    def __init__(self, events: list[Any], final: Any) -> None:
        self.events, self.final = events, final

    def __enter__(self) -> _Stream:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def __iter__(self) -> Iterator[Any]:
        return iter(self.events)

    def get_final_message(self) -> Any:
        return self.final


def _final(content: list[_Block], stop_reason: str) -> Any:
    usage = SimpleNamespace(
        input_tokens=10,
        output_tokens=5,
        cache_read_input_tokens=900,
        cache_creation_input_tokens=0,
        iterations=None,
    )
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        model="claude-opus-5-5",
        usage=usage,
        stop_details=None,
    )


def test_real_client_request_shape(
    auth_client: TestClient, project: dict, use_settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[dict[str, Any]] = []
    responses = [
        _Stream(
            [SimpleNamespace(type="text", text="Checking.")],
            _final(
                [
                    _Block(type="thinking", thinking="", signature="sig"),
                    _Block(type="text", text="Checking."),
                    _Block(type="tool_use", id="tu1", name="get_parts_list", input={}),
                ],
                "tool_use",
            ),
        ),
        _Stream(
            [SimpleNamespace(type="text", text="No parts yet.")],
            _final([_Block(type="text", text="No parts yet.")], "end_turn"),
        ),
    ]

    def stream(**kwargs: Any) -> _Stream:
        requests.append(json.loads(json.dumps(kwargs)))
        return responses[len(requests) - 1]

    use_settings(anthropic_api_key=SecretStr("sk-test"))
    monkeypatch.setattr(
        anthropic.Anthropic,
        "beta",
        property(lambda self: SimpleNamespace(messages=SimpleNamespace(stream=stream))),
    )
    events = parse_sse(send(auth_client, project["id"], "Which parts?").text)
    assert [name for name, _ in events] == ["text", "tool_call", "text", "text", "done"]
    assert events[2][1] == {"text": "\n\n"}
    assert events[-1][1]["usage"]["cache_read_input_tokens"] == 1800
    first = requests[0]
    assert first["model"] == "claude-opus-5-5"
    assert first["thinking"] == {"type": "adaptive"}
    assert first["output_config"] == {"effort": "medium"}
    assert first["betas"] == ["server-side-fallback-2026-07-01"]
    assert first["fallbacks"] == "default"
    assert first["cache_control"] == {"type": "ephemeral"}
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "never produce engineering numbers" in first["system"][0]["text"]
    assert "tool_choice" not in first and "temperature" not in first
    assert all(t["strict"] for t in first["tools"])
    second = requests[1]
    # The full content (thinking block included) went back unchanged, then the tool result.
    assert second["messages"][1]["content"][0] == {
        "type": "thinking",
        "thinking": "",
        "signature": "sig",
    }
    assert second["messages"][2]["content"][0]["tool_use_id"] == "tu1"
    assert second["system"] == first["system"] and second["tools"] == first["tools"]


def test_real_client_errors_are_plain(
    auth_client: TestClient, project: dict, use_settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def stream(**kwargs: Any) -> Any:
        raise anthropic.APIConnectionError(request=None)  # type: ignore[arg-type]

    use_settings(anthropic_api_key=SecretStr("sk-test"))
    monkeypatch.setattr(
        anthropic.Anthropic,
        "beta",
        property(lambda self: SimpleNamespace(messages=SimpleNamespace(stream=stream))),
    )
    events = parse_sse(send(auth_client, project["id"]).text)
    assert events == [
        (
            "error",
            {
                "message": "The server could not reach Claude. Try again in a few minutes.",
                "code": "api_error",
            },
        )
    ]


def test_message_validation_and_busy(
    auth_client: TestClient, project: dict, use_settings: Any
) -> None:
    use_settings(claude_fake_chat_file=SCRIPT)
    for body in ({"text": "   "}, {"text": "x" * 5000}, {}):
        response = auth_client.post(f"/api/projects/{project['id']}/assistant/messages", json=body)
        assert response.status_code == 422
    lock = chat.project_lock(project["id"])
    lock.acquire()
    try:
        response = send(auth_client, project["id"])
        assert response.status_code == 409
        assert "already answering" in response.json()["detail"]
    finally:
        lock.release()
    assert (
        auth_client.post("/api/projects/999999/assistant/messages", json={"text": "hi"}).status_code
        == 404
    )
