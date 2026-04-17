from fastapi import APIRouter
from pydantic import BaseModel

from app.deps import orchestrator

router = APIRouter(tags=["compat"])


class LegacyMessage(BaseModel):
    role: str
    content: str


class LegacyChatRequest(BaseModel):
    messages: list[LegacyMessage]
    user_username: str | None = None


@router.get("/health")
def legacy_health() -> dict:
    # 兼容旧前端健康检查格式
    return {"ok": True, "sessions_persistence": False}


@router.post("/api/chat")
def legacy_chat(req: LegacyChatRequest) -> dict:
    # 兼容旧前端：取最后一条用户消息
    user_text = ""
    for m in reversed(req.messages):
        if m.role == "user":
            user_text = m.content.strip()
            break
    if not user_text:
        user_text = req.messages[-1].content.strip() if req.messages else ""

    user_id = (req.user_username or "guest").strip() or "guest"
    try:
        _, result = orchestrator.process_message(user_id=user_id, text=user_text, session_id=None)
        return {"reply": result.message, "citations": result.citations}
    except Exception as exc:
        return {"reply": "当前请求处理超时或失败，请稍后重试。", "citations": None, "error": str(exc)}
