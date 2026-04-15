from time import perf_counter
from uuid import uuid4
from langgraph.graph import END, StateGraph

from app.chains.order_chain import OrderChain
from app.agents.intent_router import IntentRouterAgent
from app.agents.order_agent import OrderAgent
from app.agents.rag_agent import RAGTool
from app.agents.search_agent import SearchAgent
from app.agents.summarizer_agent import SummarizerAgent
from app.core.session_meta import format_meta_session_reply, is_session_meta_question
from app.core.session_store import SessionStore
from app.core.settings import load_settings
from app.core.state import (
    AgentResult,
    GraphState,
    ObservabilityState,
    OrderWorkflowState,
    RagTraceState,
    SqlQueryTraceState,
    SubTask,
)


class MultiAgentOrchestrator:
    def __init__(self, session_store: SessionStore) -> None:
        self.settings = load_settings()
        self.session_store = session_store
        self.intent_router = IntentRouterAgent()
        self.search_agent = SearchAgent()
        self.rag_tool = RAGTool()
        self.order_agent = OrderAgent()
        self.summarizer = SummarizerAgent()
        self.graph = self._build_graph()

    def _build_graph(self):
        graph = StateGraph(GraphState)
        graph.add_node("load_state", self._load_state_node)
        graph.add_node("session_recall", self._session_recall_node)
        graph.add_node("decompose", self._decompose_node)
        graph.add_node("dispatch", self._dispatch_node)
        graph.add_node("query_agent", self._query_node)
        graph.add_node("rule_agent", self._rule_node)
        graph.add_node("order_agent", self._order_node)
        graph.add_node("handoff_node", self._handoff_node)
        graph.add_node("unknown_agent", self._unknown_node)
        graph.add_node("safe_response", self._safe_response_node)
        graph.add_node("collect_result", self._collect_result_node)
        graph.add_node("summarize", self._summarize_node)
        graph.add_node("save_state", self._save_state_node)
        graph.set_entry_point("load_state")
        graph.add_conditional_edges(
            "load_state",
            self._route_after_load,
            {"session_recall": "session_recall", "decompose": "decompose"},
        )
        graph.add_edge("session_recall", "collect_result")
        graph.add_edge("decompose", "dispatch")
        graph.add_conditional_edges(
            "dispatch",
            self._route_selector,
            {
                "query": "query_agent",
                "rule": "rule_agent",
                "order": "order_agent",
                "handoff": "handoff_node",
                "unknown": "unknown_agent",
                "safe_response": "safe_response",
                "all_done": "summarize",
            },
        )
        graph.add_edge("query_agent", "collect_result")
        graph.add_edge("rule_agent", "collect_result")
        graph.add_edge("order_agent", "collect_result")
        graph.add_edge("handoff_node", "collect_result")
        graph.add_edge("unknown_agent", "collect_result")
        graph.add_edge("safe_response", "collect_result")
        graph.add_edge("collect_result", "dispatch")
        graph.add_edge("summarize", "save_state")
        graph.add_edge("save_state", END)
        return graph.compile()

    def process_message(self, user_id: str, text: str, session_id: str | None = None) -> tuple[str, AgentResult]:
        sid = self.session_store.ensure_session(session_id)
        gs = self.session_store.get_or_create_graph_state(sid, user_id)
        state: GraphState = {
            "text": text,
            "conversation": gs["conversation"],
            "order_workflow": gs["order_workflow"],
            "rag_trace": RagTraceState(),
            "sql_query_trace": SqlQueryTraceState(),
            "observability": ObservabilityState(),
            "handoff": gs["handoff"],
            "sub_tasks": [],
            "task_results": [],
            "task_context": {},
            "current_task_index": 0,
            "pending_actions": [],
            "session_meta_recall": False,
        }
        out = self.graph.invoke(state)
        return sid, out["result"]

    def order_confirm(self, session_id: str, user_id: str, confirm: bool) -> AgentResult:
        ctx = self.session_store.get_or_create_order(session_id, user_id)
        raw = self.order_agent.handle_confirm(ctx, confirm)
        if raw.status == "closed":
            self.session_store.clear_order(session_id)
        state = self.session_store.get_or_create_graph_state(session_id, user_id)
        mini_state: GraphState = {
            "text": "订单确认",
            "conversation": state["conversation"],
            "order_workflow": state["order_workflow"],
            "rag_trace": RagTraceState(),
            "sql_query_trace": SqlQueryTraceState(),
            "observability": ObservabilityState(),
            "handoff": state["handoff"],
            "sub_tasks": [SubTask(id="task_1", text="订单确认", intent="order")],
            "task_results": [
                {
                    "task_id": "task_1",
                    "intent": "order",
                    "status": raw.status,
                    "message": raw.message,
                    "error": raw.error,
                    "order_link": raw.order_link,
                }
            ],
            "task_context": {
                "task_1": {
                    "task_id": "task_1",
                    "intent": "order",
                    "question": "订单确认",
                    "status": raw.status,
                    "error": raw.error,
                    "message": raw.message,
                    "order_link": raw.order_link,
                }
            },
            "pending_actions": [],
            "raw": raw,
        }
        return self.summarizer.summarize_with_state(mini_state)["result"]

    def order_finalize(self, session_id: str, user_id: str, clicked: bool) -> AgentResult:
        ctx = self.session_store.get_order(session_id)
        if ctx is None:
            return AgentResult(
                route="order",
                status="closed",
                message="订单已结束或无需再次确认。",
            )
        raw = self.order_agent.finalize(ctx, clicked)
        if raw.status == "closed":
            self.session_store.clear_order(session_id)
        state = self.session_store.get_or_create_graph_state(session_id, user_id)
        mini_state: GraphState = {
            "text": "订单执行",
            "conversation": state["conversation"],
            "order_workflow": state["order_workflow"],
            "rag_trace": RagTraceState(),
            "sql_query_trace": SqlQueryTraceState(),
            "observability": ObservabilityState(),
            "handoff": state["handoff"],
            "sub_tasks": [SubTask(id="task_1", text="订单执行", intent="order")],
            "task_results": [
                {
                    "task_id": "task_1",
                    "intent": "order",
                    "status": raw.status,
                    "message": raw.message,
                    "error": raw.error,
                    "order_link": raw.order_link,
                }
            ],
            "task_context": {
                "task_1": {
                    "task_id": "task_1",
                    "intent": "order",
                    "question": "订单执行",
                    "status": raw.status,
                    "error": raw.error,
                    "message": raw.message,
                    "order_link": raw.order_link,
                }
            },
            "pending_actions": [],
            "raw": raw,
        }
        return self.summarizer.summarize_with_state(mini_state)["result"]

    def _load_state_node(self, state: GraphState) -> GraphState:
        obs = state["observability"]
        obs.request_id = str(uuid4())
        obs.node_logs = []
        obs.errors = []
        return state

    def _route_after_load(self, state: GraphState) -> str:
        if is_session_meta_question(state.get("text") or ""):
            return "session_recall"
        return "decompose"

    def _session_recall_node(self, state: GraphState) -> GraphState:
        conv = state["conversation"]
        text = state.get("text") or ""
        msg = format_meta_session_reply(
            list(conv.history),
            max_history_turns=self.settings.max_history_turns,
            current_text=text,
        )
        return {
            "sub_tasks": [
                SubTask(id="task_1", text=text.strip() or "（空）", intent="session_meta"),
            ],
            "current_task_index": 0,
            "task_results": [],
            "task_context": {},
            "pending_actions": [],
            "raw": AgentResult(
                route="session_meta",
                status="ok",
                message=msg,
            ),
            "session_meta_recall": True,
            "rag_trace": RagTraceState(),
            "sql_query_trace": SqlQueryTraceState(),
            "continuing_order_session": False,
        }

    def _decompose_node(self, state: GraphState) -> GraphState:
        sid = state["conversation"].session_id
        active_order = self.session_store.get_order(sid)
        if active_order and active_order.status in ("collecting_info", "awaiting_pre_confirm"):
            conv = state["conversation"]
            conv.last_intent = conv.active_intent
            conv.active_intent = "order"
            return {
                "sub_tasks": [
                    SubTask(
                        id="task_1",
                        text=state["text"],
                        intent="order",
                        order_operation_hint=active_order.operation,
                    )
                ],
                "current_task_index": 0,
                "task_results": [],
                "task_context": {},
                "pending_actions": [],
                "rag_trace": RagTraceState(),
                "sql_query_trace": SqlQueryTraceState(),
                "continuing_order_session": True,
            }
        route, tasks = self.intent_router.analyze(state["text"])
        if not tasks:
            tasks = [SubTask(id="task_1", text=state["text"], intent="unknown")]
        tasks = tasks[: max(1, self.settings.max_sub_tasks)]
        conv = state["conversation"]
        conv.last_intent = conv.active_intent
        conv.active_intent = route
        return {
            "sub_tasks": tasks,
            "current_task_index": 0,
            "task_results": [],
            "task_context": {},
            "pending_actions": [],
            "rag_trace": RagTraceState(),
            "sql_query_trace": SqlQueryTraceState(),
            "continuing_order_session": False,
        }

    def _deps_satisfied(self, task: SubTask, task_map: dict[str, SubTask]) -> bool:
        deps = task.depends_on or []
        if not deps:
            return True
        for dep_id in deps:
            dep = task_map.get(dep_id)
            if dep is None:
                continue
            if dep.status not in {"done", "failed"}:
                return False
        return True

    def _next_ready_task_index(self, tasks: list[SubTask]) -> int | None:
        task_map = {t.id: t for t in tasks}
        for i, t in enumerate(tasks):
            if t.status in {"done", "failed"}:
                continue
            if self._deps_satisfied(t, task_map):
                return i
        return None

    def _dispatch_node(self, state: GraphState) -> GraphState:
        start = perf_counter()
        tasks = state.get("sub_tasks", [])
        idx = self._next_ready_task_index(tasks)
        if idx is None:
            return {"route": "all_done"}
        task = tasks[idx]
        sid = state["conversation"].session_id
        active_order = self.session_store.get_order(sid)
        intent = task.intent
        obs = state["observability"]
        obs.node_timings["dispatch_ms"] = (perf_counter() - start) * 1000
        obs.node_logs.append(f"task={task.id}, route={intent}")
        if state["handoff"].enabled and self.settings.handoff_enabled:
            return {"route": "handoff"}
        if active_order and active_order.status not in {"closed", "failed"}:
            if intent in {"query", "rule"}:
                return {"route": intent}
            return {"route": "order"}
        return {"route": intent, "current_task_index": idx}

    def _route_selector(self, state: GraphState) -> str:
        if state["observability"].errors:
            return "safe_response"
        route = state.get("route", "unknown")
        if route in {"query", "rule", "order", "handoff", "all_done"}:
            return route
        return "unknown"

    def _query_node(self, state: GraphState) -> GraphState:
        start = perf_counter()
        idx = state["current_task_index"]
        task = state["sub_tasks"][idx]
        out = self.search_agent.handle_with_state(state, task.text)
        state["observability"].node_timings["query_agent_ms"] = (perf_counter() - start) * 1000
        return out

    def _rule_node(self, state: GraphState) -> GraphState:
        start = perf_counter()
        idx = state["current_task_index"]
        task = state["sub_tasks"][idx]
        out = self.rag_tool.handle_with_state(state, task.text)
        state["observability"].node_timings["rule_agent_ms"] = (perf_counter() - start) * 1000
        return out

    def _order_node(self, state: GraphState) -> GraphState:
        start = perf_counter()
        conv = state["conversation"]
        idx = state["current_task_index"]
        task = state["sub_tasks"][idx]
        ctx = self.session_store.get_or_create_order(conv.session_id, conv.user_id)
        user_text = state.get("text") or ""
        combined = f"{user_text}\n{task.text}".strip()
        hint = getattr(task, "order_operation_hint", None)
        resolved = OrderChain.resolve_order_operation(combined, hint, None)
        chain_op = resolved if resolved in ("create", "cancel", "modify") else hint
        out = self.order_agent.handle_with_state(state, ctx, task.text, operation_hint=chain_op)
        state["observability"].node_timings["order_agent_ms"] = (perf_counter() - start) * 1000
        return out

    def _handoff_node(self, state: GraphState) -> GraphState:
        return {
            "raw": AgentResult(
                route="unknown",
                status="handoff",
                message="当前已转人工处理，请稍候客服接入。",
                handoff_status="active",
            )
        }

    def _unknown_node(self, state: GraphState) -> GraphState:
        return {
            "raw": AgentResult(
                route="unknown",
                status="clarify",
                message="未识别意图，请说明您是要查询信息、咨询规则，还是处理订单（下单/退单/修改）。",
            )
        }

    def _safe_response_node(self, state: GraphState) -> GraphState:
        return {
            "raw": AgentResult(
                route="unknown",
                status="safe_response",
                message="系统繁忙，请稍后重试或换一种问法。",
                error="graph_safe_response",
            )
        }

    def _summarize_node(self, state: GraphState) -> GraphState:
        start = perf_counter()
        out = self.summarizer.summarize_with_state(state)
        state["observability"].node_timings["summarize_ms"] = (perf_counter() - start) * 1000
        return out

    def _collect_result_node(self, state: GraphState) -> GraphState:
        idx = state["current_task_index"]
        task = state["sub_tasks"][idx]
        raw = state["raw"]
        task.status = "done" if raw.status not in {"error", "failed"} else "failed"
        task_result = {
            "task_id": task.id,
            "intent": task.intent,
            "status": raw.status,
            "message": raw.message,
            "error": raw.error,
            "action_required": raw.action_required,
        }
        state["task_results"].append(task_result)
        task_ctx = state.setdefault("task_context", {})
        prev_ctx = task_ctx.get(task.id, {})
        task_ctx[task.id] = {
            "task_id": task.id,
            "intent": task.intent,
            "question": task.text,
            "status": raw.status,
            "error": raw.error,
            "message": raw.message,
            "sql_query": raw.sql_query,
            "depends_on": list(task.depends_on or []),
            "citations": list(raw.citations) if raw.citations else [],
            "outputs": dict(prev_ctx.get("outputs", {})) if isinstance(prev_ctx, dict) else {},
        }
        for rec in state["sql_query_trace"].records:
            if rec.task_id == task.id:
                task_ctx[task.id]["sql_citations"] = list(rec.citations)
                task_ctx[task.id]["order_line_items_by_order_id"] = dict(rec.order_line_items_by_order_id)
                break
        for rec in state["rag_trace"].records:
            if rec.task_id == task.id:
                task_ctx[task.id]["rag_selected_citations"] = list(rec.selected_citations)
                task_ctx[task.id]["rag_retrieval_query"] = rec.retrieval_query
                break
        if raw.route == "order" and raw.status == "awaiting_pre_confirm":
            state["pending_actions"].append(
                {"type": "order_confirm", "task_id": task.id, "hint": "订单任务待确认，需用户二次确认后执行"}
            )
        return {"current_task_index": idx + 1}

    def _save_state_node(self, state: GraphState) -> GraphState:
        conv = state["conversation"]
        ctx = self.session_store.get_order(conv.session_id)
        if ctx and ctx.status == "executed_waiting_click" and ctx.order_link:
            self.session_store.clear_order(conv.session_id)
            state["order_workflow"] = OrderWorkflowState()
        self.session_store.append_history(conv.session_id, "user", state["text"], conv.active_intent)
        self.session_store.append_history(conv.session_id, "assistant", state["result"].message, state["result"].route)
        self.session_store.save_graph_state(conv.session_id, state)
        return state
