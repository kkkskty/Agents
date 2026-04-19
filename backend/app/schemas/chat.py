from typing import Literal

from pydantic import BaseModel, Field


IntentType = Literal["query", "rule", "order", "handoff", "unknown", "session_meta"]


class ChatMessageRequest(BaseModel):
    user_id: str = Field(..., description="User identifier")
    text: str = Field(..., min_length=1, description="User input text")
    session_id: str | None = Field(default=None, description="Conversation session ID")


class Citation(BaseModel):
    source: str
    chunk_id: int
    distance: float | None = None
    snippet: str | None = None


class ChatMessageResponse(BaseModel):
    session_id: str
    turn_id: str | None = Field(default=None, description="本轮用户请求的 turn_id")
    route: IntentType
    reply: str
    status: str
    action_required: str | None = None
    order_link: str | None = None
    citations: list[Citation] | None = None
    error: str | None = None
    request_id: str | None = None
    workflow_step: str | None = None
    handoff_status: str | None = None
    debug_trace: dict | None = None
    sub_task_count: int | None = None
    sub_task_progress: str | None = None
    pending_actions: list[dict] | None = None
