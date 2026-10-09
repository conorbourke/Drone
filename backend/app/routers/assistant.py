"""The assistant chat of a project: send a message (streamed answer), read and clear the thread.

``POST /api/projects/{id}/assistant/messages`` answers ``text/event-stream``. Events (each
``event: <name>`` then ``data: <json>``):

* ``text`` ``{"text": "..."}``: a piece of the answer, in order. After an ``error`` with code
  ``refusal`` the text streamed before it is not part of the stored conversation; drop it.
* ``tool_call`` ``{"id", "name", "label"}``: a tool started, with a plain label such as
  "Running a quick analysis…".
* ``proposal`` ``{"patch": {dotted.path: value}, "summary": "...", "base": "draft"}``: a
  suggested change; "Try as new version" posts it to
  ``POST /api/projects/{id}/versions/from-patch``.
* ``done`` ``{"message_id", "stop_reason", "tool_calls", "model", "usage"}``: the answer is
  complete and stored.
* ``error`` ``{"message", "code"}``: plain message; ``code`` is one of ``refusal``,
  ``max_tokens``, ``budget``, ``api_error``, ``busy``, ``internal``. The stream ends.

Before streaming starts: a missing API key answers a plain 503, another answer already running
in this project a plain 409.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, select

from app.assistant import chat
from app.deps import AppSettings, CurrentUser, DbSession, current_user
from app.models import AssistantThread
from app.routers.common import owned_project

log = logging.getLogger("app.assistant")

router = APIRouter(prefix="/api", tags=["assistant"], dependencies=[Depends(current_user)])

BUSY_MESSAGE = "The assistant is already answering in this project. Wait for it to finish."


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(max_length=chat.MAX_USER_TEXT, description="The owner's message.")

    @field_validator("text")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Type a message first.")
        return value


@router.get("/assistant/status")
def assistant_status(settings: AppSettings) -> dict[str, Any]:
    """Whether the assistant is available, so the panel can show the plain message up front."""
    available = chat.is_available(settings)
    return {
        "available": available,
        "model": settings.claude_model,
        "message": None if available else chat.MISSING_KEY_MESSAGE,
        "max_tool_calls": chat.MAX_TOOL_CALLS,
    }


@router.get("/projects/{project_id}/assistant/messages")
def get_thread(
    project_id: int, db: DbSession, user: CurrentUser, settings: AppSettings
) -> dict[str, Any]:
    """``{thread_id, available, messages: [...]}``; each message is
    ``{id, role: "user", text, created_at}``,
    ``{id, role: "assistant", text, tool_calls: [{id, name, label, is_error}],
    proposals: [{patch, summary, base}], created_at}`` or
    ``{id, role: "notice", text, code, created_at}``."""
    project = owned_project(db, user, project_id)
    thread = db.scalar(select(AssistantThread).where(AssistantThread.project_id == project.id))
    return {
        "thread_id": thread.id if thread else None,
        "available": chat.is_available(settings),
        "messages": chat.display_thread(db, thread),
    }


@router.delete(
    "/projects/{project_id}/assistant/messages",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
def clear_thread(project_id: int, db: DbSession, user: CurrentUser) -> Any:
    project = owned_project(db, user, project_id)
    lock = chat.project_lock(project.id)
    if not lock.acquire(blocking=False):
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": BUSY_MESSAGE},
        )
    try:
        db.execute(delete(AssistantThread).where(AssistantThread.project_id == project.id))
        db.commit()
    finally:
        lock.release()
    return None


@router.post(
    "/projects/{project_id}/assistant/messages",
    responses={
        200: {"content": {"text/event-stream": {}}},
        409: {"description": "An answer is already running in this project"},
        503: {"description": "No Claude API key configured"},
    },
)
def post_message(
    project_id: int,
    body: MessageIn,
    request: Request,
    db: DbSession,
    user: CurrentUser,
    settings: AppSettings,
) -> Any:
    project = owned_project(db, user, project_id)
    if not chat.is_available(settings):
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": chat.MISSING_KEY_MESSAGE},
        )
    lock = chat.project_lock(project.id)
    if lock.locked():
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": BUSY_MESSAGE})
    try:
        model = chat.make_model(settings)
    except chat.ChatError as exc:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"detail": str(exc)}
        )
    thread = chat.get_or_create_thread(db, user.id, project.id)
    session_factory = request.app.state.session_factory
    owner_id, thread_id = user.id, thread.id

    def stream() -> Iterator[str]:
        # The lock is taken here, not before the response, so a client that disconnects
        # before the body starts can never leave it held.
        if not lock.acquire(blocking=False):
            yield chat.sse("error", {"message": BUSY_MESSAGE, "code": "busy"})
            return
        try:
            yield from chat.run_turn(
                session_factory=session_factory,
                settings=settings,
                owner_id=owner_id,
                project_id=project_id,
                thread_id=thread_id,
                text=body.text,
                model=model,
            )
        except Exception:
            log.exception("Assistant turn failed")
            yield chat.sse("error", {"message": chat.UNEXPECTED_MESSAGE, "code": "internal"})
        finally:
            lock.release()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
