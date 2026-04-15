from fastapi import APIRouter

from app.core.settings import load_settings
from app.deps import orchestrator
from app.schemas.chat import ChatMessageRequest, ChatMessageResponse, Citation

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/message", response_model=ChatMessageResponse)
def post_chat_message(req: ChatMessageRequest) -> ChatMessageResponse:
    settings = load_settings()
    sid = req.session_id or "unknown"
    try:
        sid, result = orchestrator.process_message(
            user_id=req.user_id, text=req.text, session_id=req.session_id
        )
    except Exception as exc:
        return ChatMessageResponse(
            session_id=sid,
            route="unknown",
            reply="当前请求处理超时或失败，请稍后重试。",
            status="error",
            error=str(exc),
        )
    return ChatMessageResponse(
        session_id=sid,
        route=result.route,  # type: ignore[arg-type]
        reply=result.message,
        status=result.status,
        action_required=result.action_required,
        order_link=result.order_link,
        citations=[Citation(**c) for c in result.citations] if result.citations else None,
        error=result.error,
        request_id=result.request_id,
        workflow_step=result.workflow_step,
        handoff_status=result.handoff_status,
        debug_trace=result.debug_trace if settings.graph_debug_trace_enabled else None,
        sub_task_count=result.sub_task_count,
        sub_task_progress=result.sub_task_progress,
        pending_actions=result.pending_actions,
    )
