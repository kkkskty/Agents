from uuid import uuid4

from app.core.state import (
    ConversationState,
    ConversationTurn,
    GraphState,
    HandoffState,
    ObservabilityState,
    OrderTraceState,
    OrderContext,
    RagTraceState,
    RuntimeState,
    SessionState,
    SqlQueryTraceState,
    TraceState,
)


class SessionStore:
    def __init__(self) -> None:
        self._orders: dict[str, OrderContext] = {}
        self._handoffs: dict[str, HandoffState] = {}
        self._graph_states: dict[str, GraphState] = {}

    def ensure_session(self, session_id: str | None) -> str:
        return session_id or str(uuid4())

    def get_or_create_order(self, session_id: str, user_id: str) -> OrderContext:
        ctx = self._orders.get(session_id)
        if ctx is None:
            ctx = OrderContext(session_id=session_id, user_id=user_id)
            self._orders[session_id] = ctx
        return ctx

    def get_order(self, session_id: str) -> OrderContext | None:
        return self._orders.get(session_id)

    def clear_order(self, session_id: str) -> None:
        if session_id in self._orders:
            del self._orders[session_id]

    def get_or_create_handoff(self, session_id: str) -> HandoffState:
        hs = self._handoffs.get(session_id)
        if hs is None:
            hs = HandoffState()
            self._handoffs[session_id] = hs
        return hs

    def get_handoff(self, session_id: str) -> HandoffState | None:
        return self._handoffs.get(session_id)

    def get_or_create_graph_state(self, session_id: str, user_id: str) -> GraphState:
        state = self._graph_states.get(session_id)
        if state is None:
            conv = ConversationState(session_id=session_id, user_id=user_id)
            session: SessionState = {
                "conversation": conv,
            }
            runtime: RuntimeState = {
                "text": "",
                "route": "unknown",
                "sub_tasks": [],
                "task_results": [],
                "task_context": {},
                "current_task_index": 0,
                "pending_actions": [],
            }
            trace: TraceState = {
                "rag_trace": RagTraceState(),
                "sql_query_trace": SqlQueryTraceState(),
                "order_trace": OrderTraceState(),
                "observability": ObservabilityState(),
            }
            state = {"session": session, "runtime": runtime, "trace": trace}
            self._graph_states[session_id] = state
        else:
            for key in ("session", "runtime", "trace"):
                if key not in state:
                    raise RuntimeError(f"GraphState 缺少分区 {key!r}，请清理旧会话状态后重试。")
            trace = state["trace"]
            if not hasattr(trace["sql_query_trace"], "records"):
                raise RuntimeError("GraphState.trace.sql_query_trace 结构非法，请重置会话状态。")
            if not hasattr(trace["rag_trace"], "records"):
                raise RuntimeError("GraphState.trace.rag_trace 结构非法，请重置会话状态。")
            if not hasattr(trace["order_trace"], "records"):
                raise RuntimeError("GraphState.trace.order_trace 结构非法，请重置会话状态。")
        return state

    def save_graph_state(self, session_id: str, state: GraphState) -> None:
        self._graph_states[session_id] = state

    def append_history(self, session_id: str, role: str, content: str, intent: str | None = None) -> None:
        state = self._graph_states.get(session_id)
        if not state or "session" not in state:
            return
        conv = state["session"]["conversation"]
        conv.history.append(ConversationTurn(role=role, content=content, intent=intent))
        conv.turn_index += 1

    def set_handoff(
        self, session_id: str, enabled: bool, reason: str | None = None, assigned_to: str | None = None
    ) -> None:
        handoff = self.get_or_create_handoff(session_id)
        handoff.enabled = enabled
        handoff.reason = reason
        handoff.assigned_to = assigned_to
        handoff.status = "active" if enabled else "inactive"
