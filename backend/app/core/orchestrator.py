from time import perf_counter
from typing import Any
from uuid import uuid4
from langgraph.graph import END, StateGraph

from app.chains.order_chain import OrderChain
from app.chains.order_field_config import (
    FIELD_LABEL_ZH as ORDER_FIELD_LABEL_ZH,
    allowed_form_field_keys,
    display_fields_for,
    readonly_fields_for,
)
from app.chains.order_validation import (
    MISSING_DEPENDENCY_DATA,
    MISSING_DEPENDENCY_ITEMS,
    MISSING_DEPENDENCY_ORDER_IDS,
    format_validation_codes,
    missing_order_field_keys,
    order_form_correction_field_keys,
    order_validation_debug_trace,
)
from app.agents.intent_router import IntentRouterAgent
from app.agents.order_agent import OrderAgent
from app.agents.rag_agent import RAGTool
from app.agents.search_agent import SearchAgent
from app.agents.summarizer_agent import SummarizerAgent
from app.core.session_store import SessionStore
from app.core.settings import load_settings
from app.tools.sql_query_tool import execute_user_scoped_sql
from app.core.state import (
    AgentResult,
    GraphState,
    HandoffTask,
    OrderCollectedItem,
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
        ##只指向编排节点 保持拓展性 以后可以加一些预处理node
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

    def order_fill_fields(
        self,
        session_id: str,
        user_id: str,
        fields: dict[str, str],
        items: list[OrderCollectedItem] | None = None,
    ) -> AgentResult:
        ctx = self.session_store.get_or_create_order(session_id, user_id)
        allowed_fields = allowed_form_field_keys(ctx.operation)
        for k, v in (fields or {}).items():
            key = str(k or "").strip()
            if not key:
                continue
            if allowed_fields and key not in allowed_fields:
                continue
            value = str(v or "").strip()
            if value:
                ctx.fields[key] = value
        if items:
            normalized_items: list[OrderCollectedItem] = []
            for it in items:
                if not isinstance(it, dict):
                    continue
                name = str(it.get("item_name") or "").strip()
                qty = str(it.get("quantity") or "").strip() or "1"
                if not name:
                    continue
                normalized_items.append({"item_name": name, "quantity": qty})
            if normalized_items:
                ctx.items = normalized_items
        raw = self.order_agent.handle_message(ctx, "", operation_hint=ctx.operation)
        raw.pending_actions = self._build_order_pending_actions(ctx, task_id="task_1") or None
        if raw.status == "closed":
            self.session_store.clear_order(session_id)
        return raw

    def order_cancel_flow(self, session_id: str, user_id: str) -> AgentResult:
        ctx = self.session_store.get_order(session_id)
        if ctx is None:
            return AgentResult(
                route="order",
                status="closed",
                message="当前没有进行中的订单流程。",
            )
        self.session_store.clear_order(session_id)
        return AgentResult(
            route="order",
            status="closed",
            message="已取消当前订单流程。",
        )


#初始化节点 设置观测信息
    def _load_state_node(self, state: GraphState) -> GraphState:
        obs = state["trace"]["observability"]
        obs.request_id = str(uuid4())
        obs.node_logs = []
        obs.errors = []
        return state
#路由后的阶段走向 直接指向意图拆解节点
    def _route_after_load(self, state: GraphState) -> str:
        return "decompose"


#意图拆解节点 拆解意图为子任务
    def _decompose_node(self, state: GraphState) -> GraphState:
        sid = state["session"]["conversation"].session_id
        active_order = self.session_store.get_order(sid)
        # 活跃订单期间严格走订单主线：不再插入其它子任务
        if active_order and active_order.status not in {"closed", "failed"}:
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
        # 无活跃订单时按常规意图拆解
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
    
#将意图拆解结果转换为子任务
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

#判断子任务依赖是否满足 没满足直接失败
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
#获取下一个可执行子任务的索引
    def _next_ready_task_index(self, tasks: list[Task]) -> int | None:
        task_map = {t.id: t for t in tasks}
        for i, t in enumerate(tasks):
            if t.status in {"done", "failed"}:
                continue
            if self._deps_satisfied(t, task_map):
                return i
        return None
#查询缺失字段 
    def _missing_order_fields(self, ctx) -> list[str]:
        return missing_order_field_keys(ctx)

    def _build_order_pending_actions(self, ctx, task_id: str = "task_1") -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        if ctx.operation in {"create", "cancel", "modify"}:
            missing = self._missing_order_fields(ctx)
            correction_keys = order_form_correction_field_keys(ctx)
            if correction_keys:
                prefill = {k: v for k, v in ctx.fields.items() if str(v or "").strip()}
                preferred = display_fields_for(ctx.operation)
                display_keys = [
                    k
                    for k in dict.fromkeys(preferred + correction_keys)
                    if k != "modify_payload"
                ]
                if ctx.operation == "cancel" and ctx.cancel_order_ids:
                    # 退单场景将依赖提取到的订单号预填到表单中，便于用户确认与补充原因。
                    prefill["order_id"] = prefill.get("order_id") or str(ctx.cancel_order_ids[0])
                    prefill["cancel_order_ids"] = ",".join(str(x) for x in ctx.cancel_order_ids)
                if ctx.operation in {"cancel", "modify"} and not ctx.items:
                    ids_for_items = [str(x) for x in (ctx.cancel_order_ids or []) if str(x).strip()]
                    if not ids_for_items and str(ctx.fields.get("order_id") or "").strip():
                        ids_for_items = [str(ctx.fields.get("order_id"))]
                    if ids_for_items:
                        try:
                            fetched_items = self._fetch_order_items_by_order_ids(ids_for_items, ctx.user_id)
                        except Exception:
                            fetched_items = []
                        if fetched_items:
                            ctx.items = fetched_items
                if ctx.operation in {"cancel", "modify"} and ctx.items:
                    normalized_items = [
                        {
                            "item_name": str(it.get("item_name") or "").strip(),
                            "quantity": str(it.get("quantity") or "").strip() or "1",
                        }
                        for it in ctx.items
                        if str(it.get("item_name") or "").strip()
                    ]
                    if normalized_items:
                        prefill["item_name"] = prefill.get("item_name") or normalized_items[0]["item_name"]
                        prefill["quantity"] = prefill.get("quantity") or normalized_items[0]["quantity"]
                        prefill["items"] = normalized_items
                if ctx.operation == "create" and ctx.items:
                    normalized_items = [
                        {
                            "item_name": str(it.get("item_name") or "").strip(),
                            "quantity": str(it.get("quantity") or "").strip() or "1",
                        }
                        for it in ctx.items
                        if str(it.get("item_name") or "").strip()
                    ]
                    if normalized_items:
                        prefill["item_name"] = prefill.get("item_name") or normalized_items[0]["item_name"]
                        prefill["quantity"] = prefill.get("quantity") or normalized_items[0]["quantity"]
                        prefill["items"] = normalized_items
                readonly_keys = readonly_fields_for(ctx.operation)
                fmt_codes = format_validation_codes(ctx)
                hint = (
                    "请修正格式不正确的字段后继续。"
                    if fmt_codes and not missing
                    else "请通过表单补全订单必填信息后继续。"
                )
                actions.append(
                    {
                        "type": "order_fill_fields",
                        "task_id": task_id,
                        "operation": ctx.operation,
                        "required_fields": [
                            {"key": f, "label": ORDER_FIELD_LABEL_ZH.get(f, f)} for f in correction_keys
                        ],
                        "display_fields": [
                            {"key": f, "label": ORDER_FIELD_LABEL_ZH.get(f, f)} for f in display_keys
                        ],
                        "readonly_fields": [
                            {"key": f, "label": ORDER_FIELD_LABEL_ZH.get(f, f)} for f in readonly_keys
                        ],
                        "prefill": prefill,
                        "hint": hint,
                    }
                )
        if ctx.status == "awaiting_pre_confirm":
            actions.append(
                {"type": "order_confirm", "task_id": task_id, "hint": "订单任务待确认，需用户二次确认后执行"}
            )
        return actions

    def _build_task_result(self, task: Task, raw: AgentResult) -> dict[str, Any]:
        return {
            "task_id": task.id,
            "intent": task.intent,
            "status": raw.status,
            "message": raw.message,
            "error": raw.error,
            "action_required": raw.action_required,
            "order_link": raw.order_link,
            "handoff_status": raw.handoff_status,
        }

    def _upsert_task_context(self, state: GraphState, task: Task, raw: AgentResult) -> None:
        runtime = state["runtime"]
        trace = state["trace"]
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

    def _append_order_pending_actions(self, state: GraphState, task_id: str, raw: AgentResult) -> None:
        runtime = state["runtime"]
        if raw.route != "order":
            return
        if raw.status not in {"collecting_info", "awaiting_pre_confirm"}:
            return
        conv = state["session"]["conversation"]
        ctx = self.session_store.get_order(conv.session_id)
        if not ctx:
            return
        if raw.status == "collecting_info" and ctx.operation not in {"create", "cancel", "modify"}:
            return
        runtime["pending_actions"].extend(self._build_order_pending_actions(ctx, task_id=task_id))


#收集结果节点 收集子任务结果
    def _collect_result_node(self, state: GraphState) -> GraphState:
        runtime = state["runtime"]
        idx = runtime["current_task_index"]
        task = runtime["sub_tasks"][idx]
        raw = runtime["raw"]
        # 更新子任务状态
        task.status = "done" if raw.status not in {"error", "failed"} else "failed"
        task_result = self._build_task_result(task, raw)
        # 记录子任务结果
        runtime["task_results"].append(task_result)
        # 记录任务上下文并补齐 trace 回填信息
        self._upsert_task_context(state, task, raw)
        # 根据订单状态生成下一步动作
        self._append_order_pending_actions(state, task_id=task.id, raw=raw)
        return {"runtime": {**runtime, "current_task_index": idx + 1}}


#分发节点 根据人工-订单-普通任务的执行顺序执行 
    def _dispatch_node(self, state: GraphState) -> GraphState:
        #计时并获取子任务
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
        #如果此时处于人工中
        if handoff and handoff.enabled and self.settings.handoff_enabled:
            return {"runtime": {**runtime, "route": "handoff"}}
        # 订单未结束时仅允许 order 主线
        if active_order and active_order.status not in {"closed", "failed"}:
            return {"runtime": {**runtime, "route": "order", "current_task_index": idx}}
        #否则继续执行当前任务
        return {"runtime": {**runtime, "route": intent, "current_task_index": idx}}

#路由选择器 根据子任务执行结果选择下一个路由
    def _route_selector(self, state: GraphState) -> str:
        if state["trace"]["observability"].errors:
            return "safe_response"
        route = state["runtime"].get("route", "unknown")
        if route in {"query", "rule", "order", "handoff", "all_done"}:
            return route
            #异常兜底
        return "unknown"
#查询节点 执行查询任务
    def _query_node(self, state: GraphState) -> GraphState:
        start = perf_counter()
        runtime = state["runtime"]
        idx = runtime["current_task_index"]
        task = runtime["sub_tasks"][idx]
        out = self.search_agent.handle_with_state(state, task.text)
        state["trace"]["observability"].node_timings["query_agent_ms"] = (perf_counter() - start) * 1000
        return out
#规则节点 执行规则任务
    def _rule_node(self, state: GraphState) -> GraphState:
        start = perf_counter()
        runtime = state["runtime"]
        idx = runtime["current_task_index"]
        task = runtime["sub_tasks"][idx]
        out = self.rag_tool.handle_with_state(state, task.text)
        state["trace"]["observability"].node_timings["rule_agent_ms"] = (perf_counter() - start) * 1000
        return out

    def _normalize_dep_order_item(self, item: Any) -> OrderCollectedItem | None:
        if not isinstance(item, dict):
            return None
        name = str(item.get("item_name") or item.get("name") or item.get("product_name") or "").strip()
        qty = str(item.get("quantity") or item.get("qty") or item.get("count") or "").strip() or "1"
        if not name:
            return None
        normalized: OrderCollectedItem = {"item_name": name, "quantity": qty}
        pid = item.get("product_id") or item.get("id")
        if pid is not None and str(pid).strip():
            normalized["product_id"] = pid
        return normalized

    def _collect_items_from_dep_context(self, dep_ctx: dict[str, Any]) -> list[OrderCollectedItem]:
        merged: list[OrderCollectedItem] = []
        seen: set[tuple[str, str]] = set()

        outputs = dep_ctx.get("outputs")
        outputs_dict = outputs if isinstance(outputs, dict) else {}
        # 兼容多种输出字段，避免只依赖单一 key。
        candidates = [
            outputs_dict.get("proposed_order_items"),
            outputs_dict.get("items"),
            outputs_dict.get("order_items"),
            outputs_dict.get("line_items"),
            dep_ctx.get("order_line_items"),
        ]
        oid_map = outputs_dict.get("order_items_by_order_id")
        if isinstance(oid_map, dict):
            for vals in oid_map.values():
                candidates.append(vals)
        # SQL 轨迹注入的结构：order_line_items_by_order_id: dict[str, list[dict]]
        by_order_id = dep_ctx.get("order_line_items_by_order_id")
        if isinstance(by_order_id, dict):
            for vals in by_order_id.values():
                candidates.append(vals)

        for bucket in candidates:
            if not isinstance(bucket, list):
                continue
            for raw_item in bucket:
                normalized = self._normalize_dep_order_item(raw_item)
                if not normalized:
                    continue
                key = (normalized["item_name"], normalized["quantity"])
                if key in seen:
                    continue
                seen.add(key)
                merged.append(normalized)
        return merged

    def _collect_dep_order_items(
        self, runtime: dict[str, Any], dep_ids: list[str]
    ) -> list[OrderCollectedItem]:
        task_ctx = runtime.get("task_context", {})
        if not isinstance(task_ctx, dict):
            return []
        merged: list[OrderCollectedItem] = []
        seen: set[tuple[str, str]] = set()
        for dep_id in dep_ids:
            dep_ctx = task_ctx.get(dep_id)
            if not isinstance(dep_ctx, dict):
                continue
            items = self._collect_items_from_dep_context(dep_ctx)
            for item in items:
                key = (item["item_name"], item["quantity"])
                if key in seen:
                    continue
                seen.add(key)
                merged.append(item)
        return merged

    def _collect_dep_cancel_order_ids(
        self, runtime: dict[str, Any], dep_ids: list[str]
    ) -> list[str]:
        task_ctx = runtime.get("task_context", {})
        if not isinstance(task_ctx, dict):
            return []
        out: list[str] = []
        seen: set[str] = set()
        for dep_id in dep_ids:
            dep_ctx = task_ctx.get(dep_id)
            if not isinstance(dep_ctx, dict):
                continue
            outputs = dep_ctx.get("outputs")
            if not isinstance(outputs, dict):
                continue
            for key in ("unpaid_order_ids", "order_ids", "cancel_order_ids"):
                vals = outputs.get(key)
                if not isinstance(vals, list):
                    continue
                for raw in vals:
                    oid = str(raw or "").strip()
                    if not oid or oid in seen:
                        continue
                    seen.add(oid)
                    out.append(oid)
        return out

    def _fetch_order_items_by_order_ids(
        self, order_ids: list[str], user_id: str
    ) -> list[OrderCollectedItem]:
        numeric_ids = [str(x).strip() for x in order_ids if str(x).strip().isdigit()]
        if not numeric_ids:
            return []
        ids_sql = ", ".join(numeric_ids)
        sql = (
            "SELECT oi.order_id, oi.product_id, oi.quantity, oi.unit_price, p.name AS item_name "
            "FROM order_items oi "
            "LEFT JOIN products p ON p.id = oi.product_id "
            f"WHERE oi.order_id IN ({ids_sql})"
        )
        rows = execute_user_scoped_sql(sql, user_id)
        merged: list[OrderCollectedItem] = []
        seen: set[tuple[str, str]] = set()
        for r in rows:
            d = dict(r)
            name = str(d.get("item_name") or "").strip()
            qty = str(d.get("quantity") or "").strip() or "1"
            if not name:
                continue
            key = (name, qty)
            if key in seen:
                continue
            seen.add(key)
            item: OrderCollectedItem = {"item_name": name, "quantity": qty}
            pid = d.get("product_id")
            if pid is not None and str(pid).strip():
                item["product_id"] = pid
            merged.append(item)
        return merged
#订单节点 执行订单任务
    def _order_node(self, state: GraphState) -> GraphState:
        start = perf_counter()
        runtime = state["runtime"]
        conv = state["session"]["conversation"]
        idx = runtime["current_task_index"]
        task = runtime["sub_tasks"][idx]
        #获取订单上下文
        ctx = self.session_store.get_or_create_order(conv.session_id, conv.user_id) 
        dep_ids = list(task.depends_on or [])
        user_text = runtime.get("text") or ""
        combined = f"{user_text}\n{task.text}".strip()
        hint = task.order_operation_hint if isinstance(task, OrderTask) else None
        resolved = OrderChain.resolve_order_operation(combined, hint, ctx.operation)
        chain_op = resolved if resolved in ("create", "cancel", "modify") else hint
        loaded_items_count = 0 #加载到的订单条目

        # 若订单任务存在依赖：按订单操作类型提取依赖信息。
        if dep_ids:
            # 按“下单同款”统一读取依赖，再按操作类型消费字段。
            dep_items = self._collect_dep_order_items(runtime, dep_ids)
            dep_order_ids = self._collect_dep_cancel_order_ids(runtime, dep_ids)

            if dep_items:
                loaded_items_count = len(dep_items)
                ctx.items = dep_items
                if chain_op in {"cancel", "modify"} and not str(ctx.fields.get("item_name") or "").strip():
                    ctx.fields["item_name"] = str(dep_items[0].get("item_name") or "").strip()

            if chain_op == "cancel" and dep_order_ids:
                ctx.cancel_order_ids = dep_order_ids
            elif chain_op == "modify":
                # modify 仅消费依赖中的订单号，不污染 cancel 专用上下文。
                if dep_order_ids and not str(ctx.fields.get("order_id") or "").strip():
                    ctx.fields["order_id"] = str(dep_order_ids[0])

            # cancel/modify 场景：依赖中只有订单号没有商品明细时，按订单号补查一次条目。
            if chain_op in {"cancel", "modify"} and (not ctx.items) and dep_order_ids:
                fetched_items = self._fetch_order_items_by_order_ids(dep_order_ids, conv.user_id)
                if fetched_items:
                    loaded_items_count = len(fetched_items)
                    ctx.items = fetched_items
                    if not str(ctx.fields.get("item_name") or "").strip():
                        ctx.fields["item_name"] = str(fetched_items[0].get("item_name") or "").strip()

            # 依赖了查询但没有提取到所需信息时，给明确提示
            missing_dep = False
            dep_message = "未从前序查询提取到可用依赖信息，请先确认查询结果后再继续。"
            dep_error = MISSING_DEPENDENCY_DATA
            if chain_op == "cancel":
                if not ctx.cancel_order_ids:
                    missing_dep = True
                    dep_message = "未从前序查询提取到可退单订单号，请先确认未支付订单列表后再退单。"
                    dep_error = MISSING_DEPENDENCY_ORDER_IDS
            elif chain_op == "modify":
                if not str(ctx.fields.get("order_id") or "").strip() and not ctx.cancel_order_ids:
                    missing_dep = True
                    dep_message = "未从前序查询提取到可修改订单号，请先确认未支付订单列表后再修改。"
                    dep_error = MISSING_DEPENDENCY_ORDER_IDS
            elif chain_op == "create":
                if loaded_items_count == 0:
                    missing_dep = True
                    dep_message = "未从前序查询提取到可下单商品，请先确认商品列表后再下单。"
                    dep_error = MISSING_DEPENDENCY_ITEMS
            elif loaded_items_count == 0 and not ctx.cancel_order_ids:
                missing_dep = True

            if missing_dep:
                raw = AgentResult(
                    route="order",
                    status="collecting_info",
                    message=dep_message,
                    error=dep_error,
                    debug_trace=order_validation_debug_trace(
                        phase="dependency_injection",
                        codes=[dep_error],
                        operation=chain_op,
                        extra={"depends_on": dep_ids},
                    ),
                )
                #记录订单轨迹
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
#执行订单任务
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
#人工节点 执行人工任务
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
#未知节点 兜底
    def _unknown_node(self, state: GraphState) -> GraphState:
        runtime = state["runtime"]
        sid = state["session"]["conversation"].session_id
        active_order = self.session_store.get_order(sid)
        if active_order and active_order.status not in {"closed", "failed"}:
            return {
                "runtime": {
                    **runtime,
                    "raw": AgentResult(
                        route="unknown",
                        status="clarify",
                        message="当前订单任务尚未完成，请先补全并完成当前订单流程。",
                    ),
                }
            }
        return {
            "runtime": {
                **runtime,
                "raw": AgentResult(
                    route="unknown",
                    status="clarify",
                    message="未识别意图，请说明您是要查询信息、咨询规则，还是处理订单（下单/退单/修改）。",
                ),
            }
        }
#安全响应节点 兜底
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
#总结节点 总结结果
    def _summarize_node(self, state: GraphState) -> GraphState:
        start = perf_counter()
        out = self.summarizer.summarize_with_state(state)
        state["trace"]["observability"].node_timings["summarize_ms"] = (perf_counter() - start) * 1000
        return out


#保存状态节点 保存状态到数据库  还没完全实现 现在就保存在内存了 后面其实可以改成Session可以只保存k轮信息 7天内保存到reis 然后重要信息异步持久化  
    def _save_state_node(self, state: GraphState) -> GraphState:
        runtime = state["runtime"]
        conv = state["session"]["conversation"]
        # 保存用户消息
        self.session_store.append_history(conv.session_id, "user", runtime["text"], conv.active_intent)
        #保存AI回复 
        self.session_store.append_history(
            conv.session_id,
            "assistant",
            runtime["result"].message,
            runtime["result"].route,
        )
        #持久化状态
        self.session_store.save_graph_state(conv.session_id, state)
        # 写盘后再清理终态订单上下文，避免清理成功但历史落盘失败
        ctx = self.session_store.get_order(conv.session_id)
        if ctx and ctx.status in {"closed", "failed"}:
            self.session_store.clear_order(conv.session_id)
        return state
