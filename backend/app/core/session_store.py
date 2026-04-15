from uuid import uuid4

from app.core.state import (
    ConversationState,
    ConversationTurn,
    GraphState,
    HandoffState,
    ObservabilityState,
    OrderContext,
    OrderWorkflowState,
    RagTraceState,
    SqlQueryTraceState,
)


class SessionStore:
    def __init__(self) -> None:
        self._orders: dict[str, OrderContext] = {}
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
        if session_id in self._graph_states:
            self._graph_states[session_id]["order_workflow"] = OrderWorkflowState()

    def get_or_create_graph_state(self, session_id: str, user_id: str) -> GraphState:
        state = self._graph_states.get(session_id)
        if state is None:
            state = GraphState(
                conversation=ConversationState(session_id=session_id, user_id=user_id),
                order_workflow=OrderWorkflowState(),
                rag_trace=RagTraceState(),
                sql_query_trace=SqlQueryTraceState(),
                observability=ObservabilityState(),
                handoff=HandoffState(),
                text="",
                sub_tasks=[],
                task_results=[],
                task_context={},
                current_task_index=0,
                pending_actions=[],
            )
            self._graph_states[session_id] = state
        else:
            state.setdefault("sub_tasks", [])
            state.setdefault("task_results", [])
            state.setdefault("task_context", {})
            state.setdefault("current_task_index", 0)
            state.setdefault("pending_actions", [])
            state.setdefault("sql_query_trace", SqlQueryTraceState())
            state.setdefault("rag_trace", RagTraceState())
            if not hasattr(state["sql_query_trace"], "records"):
                state["sql_query_trace"] = SqlQueryTraceState()
            if not hasattr(state["rag_trace"], "records"):
                state["rag_trace"] = RagTraceState()
        return state

    def save_graph_state(self, session_id: str, state: GraphState) -> None:
        self._graph_states[session_id] = state

    def append_history(self, session_id: str, role: str, content: str, intent: str | None = None) -> None:
        state = self._graph_states.get(session_id)
        if not state or "conversation" not in state:
            return
        conv = state["conversation"]
        conv.history.append(ConversationTurn(role=role, content=content, intent=intent))
        conv.turn_index += 1

    def set_handoff(
        self, session_id: str, enabled: bool, reason: str | None = None, assigned_to: str | None = None
    ) -> None:
        state = self._graph_states.get(session_id)
        if not state or "handoff" not in state:
            return
        handoff = state["handoff"]
        handoff.enabled = enabled
        handoff.reason = reason
        handoff.assigned_to = assigned_to
        handoff.status = "active" if enabled else "inactive"
