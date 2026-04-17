from time import perf_counter
from typing import Any
from uuid import uuid4
from langgraph.graph import END, StateGraph

from app.chains.order_chain import OrderChain
from app.agents.intent_router import IntentRouterAgent
from app.agents.order_agent import OrderAgent
from app.agents.rag_agent import RAGTool
from app.agents.search_agent import SearchAgent
from app.agents.summarizer_agent import SummarizerAgent
from app.core.session_store import SessionStore
from app.core.settings import load_settings
from app.core.state import (
    AgentResult,
    GraphState,
    HandoffTask,
    OrderTask,
    OrderTaskRecord,
    ObservabilityState,
    QueryTask,
    RagTraceState,
    RuleTask,
    SqlQueryTraceState,
    OrderTraceState,
    Task,
    UnknownTask,
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
            {"decompose": "decompose"},
        )
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
        conv = gs["session"]["conversation"]
        state: GraphState = {
            "session": {
                "conversation": conv,
            },
            "runtime": {
                "text": text,
                "route": "unknown",
                "sub_tasks": [],
                "task_results": [],
                "task_context": {},
                "current_task_index": 0,
                "pending_actions": [],
            },
            "trace": {
                "rag_trace": RagTraceState(),
                "sql_query_trace": SqlQueryTraceState(),
                "order_trace": OrderTraceState(),
                "observability": ObservabilityState(),
            },
        }
        out = self.graph.invoke(state)
        return sid, out["runtime"]["result"]

    def order_confirm(self, session_id: str, user_id: str, confirm: bool) -> AgentResult:
        ctx = self.session_store.get_or_create_order(session_id, user_id)
        raw = self.order_agent.handle_confirm(ctx, confirm)
        if raw.status == "closed":
            self.session_store.clear_order(session_id)
        state = self.session_store.get_or_create_graph_state(session_id, user_id)
        conv = state["session"]["conversation"]
        mini_state: GraphState = {
            "session": {
                "conversation": conv,
            },
            "runtime": {
                "text": "订单确认",
                "route": "order",
                "sub_tasks": [OrderTask(id="task_1", text="订单确认")],
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
                "current_task_index": 0,
                "pending_actions": [],
                "raw": raw,
            },
            "trace": {
                "rag_trace": RagTraceState(),
                "sql_query_trace": SqlQueryTraceState(),
                "order_trace": OrderTraceState(),
                "observability": ObservabilityState(),
            },
        }
        return self.summarizer.summarize_with_state(mini_state)["runtime"]["result"]

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
        conv = state["session"]["conversation"]
        mini_state: GraphState = {
            "session": {
                "conversation": conv,
            },
            "runtime": {
                "text": "订单执行",
                "route": "order",
                "sub_tasks": [OrderTask(id="task_1", text="订单执行")],
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
                "current_task_index": 0,
                "pending_actions": [],
                "raw": raw,
            },
            "trace": {
                "rag_trace": RagTraceState(),
                "sql_query_trace": SqlQueryTraceState(),
                "order_trace": OrderTraceState(),
                "observability": ObservabilityState(),
            },
        }
        return self.summarizer.summarize_with_state(mini_state)["runtime"]["result"]

    def _load_state_node(self, state: GraphState) -> GraphState:
        obs = state["trace"]["observability"]
        obs.request_id = str(uuid4())
        obs.node_logs = []
        obs.errors = []
        return state

    def _route_after_load(self, state: GraphState) -> str:
        return "decompose"

    def _session_recall_node(self, state: GraphState) -> GraphState:
        # session_meta 节点已停用，统一走意图拆解流程
        return state

    def _decompose_node(self, state: GraphState) -> GraphState:
        sid = state["session"]["conversation"].session_id
        active_order = self.session_store.get_order(sid)
        if active_order and active_order.status in ("collecting_info", "awaiting_pre_confirm"):
            conv = state["session"]["conversation"]
            conv.last_intent = conv.active_intent
            conv.active_intent = "order"
            return {
                "runtime": {
                    "text": state["runtime"]["text"],
                    "route": "order",
                    "sub_tasks": [
                        OrderTask(
                            id="task_1",
                            text=state["runtime"]["text"],
                            order_operation_hint=active_order.operation,
                        )
                    ],
                    "current_task_index": 0,
                    "task_results": [],
                    "task_context": {},
                    "pending_actions": [],
                },
                "trace": {
                    "rag_trace": RagTraceState(),
                    "sql_query_trace": SqlQueryTraceState(),
                    "order_trace": OrderTraceState(),
                    "observability": state["trace"]["observability"],
                },
            }
        route, tasks = self.intent_router.analyze(state["runtime"]["text"])
        if not tasks:
            tasks = [UnknownTask(id="task_1", text=state["runtime"]["text"])]
        tasks = [self._to_task(t) for t in tasks]
        tasks = tasks[: max(1, self.settings.max_sub_tasks)]
        conv = state["session"]["conversation"]
        conv.last_intent = conv.active_intent
        conv.active_intent = route
        return {
            "runtime": {
                "text": state["runtime"]["text"],
                "route": route,
                "sub_tasks": tasks,
                "current_task_index": 0,
                "task_results": [],
                "task_context": {},
                "pending_actions": [],
            },
            "trace": {
                "rag_trace": RagTraceState(),
                "sql_query_trace": SqlQueryTraceState(),
                "order_trace": OrderTraceState(),
                "observability": state["trace"]["observability"],
            },
        }

    def _to_task(self, task: Any) -> Task:
        if isinstance(task, (QueryTask, RuleTask, OrderTask, HandoffTask, UnknownTask)):
            return task
        intent = getattr(task, "intent", "unknown")
        task_id = getattr(task, "id", "task_0")
        text = getattr(task, "text", "")
        status = getattr(task, "status", "pending")
        depends_on = list(getattr(task, "depends_on", []) or [])
        if intent == "query":
            return QueryTask(id=task_id, text=text, status=status, depends_on=depends_on)
        if intent == "rule":
            return RuleTask(id=task_id, text=text, status=status, depends_on=depends_on)
        if intent == "order":
            return OrderTask(
                id=task_id,
                text=text,
                status=status,
                depends_on=depends_on,
                order_operation_hint=getattr(task, "order_operation_hint", None),
            )
        if intent == "handoff":
            return HandoffTask(id=task_id, text=text, status=status, depends_on=depends_on)
        return UnknownTask(id=task_id, text=text, status=status, depends_on=depends_on)

    def _deps_satisfied(self, task: Task, task_map: dict[str, Task]) -> bool:
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

    def _next_ready_task_index(self, tasks: list[Task]) -> int | None:
        task_map = {t.id: t for t in tasks}
        for i, t in enumerate(tasks):
            if t.status in {"done", "failed"}:
                continue
            if self._deps_satisfied(t, task_map):
                return i
        return None

    def _dispatch_node(self, state: GraphState) -> GraphState:
        start = perf_counter()
        runtime = state["runtime"]
        tasks = runtime.get("sub_tasks", [])
        idx = self._next_ready_task_index(tasks)
        if idx is None:
            return {"runtime": {**runtime, "route": "all_done"}}
        task = tasks[idx]
        sid = state["session"]["conversation"].session_id
        active_order = self.session_store.get_order(sid)
        intent = task.intent
        obs = state["trace"]["observability"]
        obs.node_timings["dispatch_ms"] = (perf_counter() - start) * 1000
        obs.node_logs.append(f"task={task.id}, route={intent}")
        handoff = self.session_store.get_handoff(sid)
        if handoff and handoff.enabled and self.settings.handoff_enabled:
            return {"runtime": {**runtime, "route": "handoff"}}
        if active_order and active_order.status not in {"closed", "failed"}:
            if intent in {"query", "rule"}:
                return {"runtime": {**runtime, "route": intent}}
            return {"runtime": {**runtime, "route": "order"}}
        return {"runtime": {**runtime, "route": intent, "current_task_index": idx}}

    def _route_selector(self, state: GraphState) -> str:
        if state["trace"]["observability"].errors:
            return "safe_response"
        route = state["runtime"].get("route", "unknown")
        if route in {"query", "rule", "order", "handoff", "all_done"}:
            return route
        return "unknown"

    def _query_node(self, state: GraphState) -> GraphState:
        start = perf_counter()
        runtime = state["runtime"]
        idx = runtime["current_task_index"]
        task = runtime["sub_tasks"][idx]
        out = self.search_agent.handle_with_state(state, task.text)
        state["trace"]["observability"].node_timings["query_agent_ms"] = (perf_counter() - start) * 1000
        return out

    def _rule_node(self, state: GraphState) -> GraphState:
        start = perf_counter()
        runtime = state["runtime"]
        idx = runtime["current_task_index"]
        task = runtime["sub_tasks"][idx]
        out = self.rag_tool.handle_with_state(state, task.text)
        state["trace"]["observability"].node_timings["rule_agent_ms"] = (perf_counter() - start) * 1000
        return out

    def _order_node(self, state: GraphState) -> GraphState:
        start = perf_counter()
        runtime = state["runtime"]
        conv = state["session"]["conversation"]
        idx = runtime["current_task_index"]
        task = runtime["sub_tasks"][idx]
        ctx = self.session_store.get_or_create_order(conv.session_id, conv.user_id)
        dep_ids = list(task.depends_on or [])
        loaded_items_count = 0

        # 若订单任务依赖查询任务，先把前序输出中的商品清单注入订单上下文
        if dep_ids:
            merged_items: list[dict[str, Any]] = []
            seen: set[tuple[str, str]] = set()
            task_ctx = runtime.get("task_context", {})
            for dep_id in dep_ids:
                dep_ctx = task_ctx.get(dep_id) if isinstance(task_ctx, dict) else None
                if not isinstance(dep_ctx, dict):
                    continue
                outputs = dep_ctx.get("outputs")
                if not isinstance(outputs, dict):
                    continue
                proposed = outputs.get("proposed_order_items") or []
                if not isinstance(proposed, list):
                    continue
                for item in proposed:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("item_name") or item.get("name") or "").strip()
                    qty = str(item.get("quantity") or item.get("qty") or "").strip() or "1"
                    if not name:
                        continue
                    key = (name, qty)
                    if key in seen:
                        continue
                    seen.add(key)
                    merged_items.append({"item_name": name, "quantity": qty})
            if merged_items:
                loaded_items_count = len(merged_items)
                ctx.items = merged_items

            # 依赖了查询但没有可下单商品时，给明确提示，不要进入模糊补字段流程
            if loaded_items_count == 0:
                raw = AgentResult(
                    route="order",
                    status="collecting_info",
                    message="未从前序查询提取到可下单商品，请先确认降价商品列表后再下单。",
                    error="missing_dependency_items",
                )
                state["trace"]["order_trace"].records.append(
                    OrderTaskRecord(
                        task_id=task.id,
                        operation=None,
                        source_dep_task_ids=dep_ids,
                        loaded_items_count=0,
                        status=raw.status,
                        message=raw.message,
                        order_link=raw.order_link,
                        error=raw.error,
                    )
                )
                state["trace"]["observability"].node_timings["order_agent_ms"] = (perf_counter() - start) * 1000
                return {"runtime": {**runtime, "raw": raw}}

        user_text = runtime.get("text") or ""
        combined = f"{user_text}\n{task.text}".strip()
        hint = task.order_operation_hint if isinstance(task, OrderTask) else None
        resolved = OrderChain.resolve_order_operation(combined, hint, None)
        chain_op = resolved if resolved in ("create", "cancel", "modify") else hint
        out = self.order_agent.handle_with_state(state, ctx, task.text, operation_hint=chain_op)
        state["trace"]["order_trace"].records.append(
            OrderTaskRecord(
                task_id=task.id,
                operation=ctx.operation,
                source_dep_task_ids=dep_ids,
                loaded_items_count=loaded_items_count,
                status=out["runtime"]["raw"].status if out.get("runtime", {}).get("raw") else None,
                message=out["runtime"]["raw"].message if out.get("runtime", {}).get("raw") else None,
                order_link=out["runtime"]["raw"].order_link if out.get("runtime", {}).get("raw") else None,
                error=out["runtime"]["raw"].error if out.get("runtime", {}).get("raw") else None,
            )
        )
        state["trace"]["observability"].node_timings["order_agent_ms"] = (perf_counter() - start) * 1000
        return out

    def _handoff_node(self, state: GraphState) -> GraphState:
        return {
            "runtime": {
                **state["runtime"],
                "raw": AgentResult(
                    route="unknown",
                    status="handoff",
                    message="当前已转人工处理，请稍候客服接入。",
                    handoff_status="active",
                ),
            }
        }

    def _unknown_node(self, state: GraphState) -> GraphState:
        return {
            "runtime": {
                **state["runtime"],
                "raw": AgentResult(
                    route="unknown",
                    status="clarify",
                    message="未识别意图，请说明您是要查询信息、咨询规则，还是处理订单（下单/退单/修改）。",
                ),
            }
        }

    def _safe_response_node(self, state: GraphState) -> GraphState:
        return {
            "runtime": {
                **state["runtime"],
                "raw": AgentResult(
                    route="unknown",
                    status="safe_response",
                    message="系统繁忙，请稍后重试或换一种问法。",
                    error="graph_safe_response",
                ),
            }
        }

    def _summarize_node(self, state: GraphState) -> GraphState:
        start = perf_counter()
        out = self.summarizer.summarize_with_state(state)
        state["trace"]["observability"].node_timings["summarize_ms"] = (perf_counter() - start) * 1000
        return out

    def _collect_result_node(self, state: GraphState) -> GraphState:
        runtime = state["runtime"]
        trace = state["trace"]
        idx = runtime["current_task_index"]
        task = runtime["sub_tasks"][idx]
        raw = runtime["raw"]
        task.status = "done" if raw.status not in {"error", "failed"} else "failed"
        task_result = {
            "task_id": task.id,
            "intent": task.intent,
            "status": raw.status,
            "message": raw.message,
            "error": raw.error,
            "action_required": raw.action_required,
            "order_link": raw.order_link,
            "handoff_status": raw.handoff_status,
        }
        runtime["task_results"].append(task_result)
        task_ctx = runtime.setdefault("task_context", {})
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
        for rec in trace["sql_query_trace"].records:
            if rec.task_id == task.id:
                task_ctx[task.id]["sql_citations"] = list(rec.citations)
                task_ctx[task.id]["order_line_items_by_order_id"] = dict(rec.order_line_items_by_order_id)
                break
        for rec in trace["rag_trace"].records:
            if rec.task_id == task.id:
                task_ctx[task.id]["rag_selected_citations"] = list(rec.selected_citations)
                task_ctx[task.id]["rag_retrieval_query"] = rec.retrieval_query
                break
        if raw.route == "order" and raw.status == "awaiting_pre_confirm":
            runtime["pending_actions"].append(
                {"type": "order_confirm", "task_id": task.id, "hint": "订单任务待确认，需用户二次确认后执行"}
            )
        return {"runtime": {**runtime, "current_task_index": idx + 1}}

    def _save_state_node(self, state: GraphState) -> GraphState:
        runtime = state["runtime"]
        conv = state["session"]["conversation"]
        ctx = self.session_store.get_order(conv.session_id)
        if ctx and ctx.status == "executed_waiting_click" and ctx.order_link:
            self.session_store.clear_order(conv.session_id)
        self.session_store.append_history(conv.session_id, "user", runtime["text"], conv.active_intent)
        self.session_store.append_history(
            conv.session_id,
            "assistant",
            runtime["result"].message,
            runtime["result"].route,
        )
        self.session_store.save_graph_state(conv.session_id, state)
        return state
