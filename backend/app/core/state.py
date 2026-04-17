from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict


OrderOperation = Literal["create", "cancel", "modify"]
OrderStatus = Literal[
    "collecting_info",
    "awaiting_pre_confirm",
    "executed_waiting_click",
    "closed",
    "failed",
]


@dataclass
class OrderContext:
    session_id: str
    user_id: str
    operation: OrderOperation | None = None
    status: OrderStatus = "collecting_info"
    fields: dict[str, str] = field(default_factory=dict)
    items: list[dict[str, Any]] = field(default_factory=list)
    """依赖查询任务解析出的待取消订单号（库内 orders.id），与单笔 order_id 二选一填充。"""
    cancel_order_ids: list[str] = field(default_factory=list)
    order_link: str | None = None
    failure_reason: str | None = None


@dataclass
class ConversationTurn:
    role: str
    content: str
    intent: str | None = None



###records 会话信息
@dataclass
class ConversationState:
    session_id: str
    user_id: str
    turn_index: int = 0
    history: list[ConversationTurn] = field(default_factory=list)
    last_intent: str | None = None
    active_intent: str | None = None



###records 记录轨迹
@dataclass
class RagTaskRecord:
    """单次规则/RAG 子任务的检索轨迹，按 task_id 与 task_results 对齐。"""
    task_id: str
    retrieval_query: str | None = None
    top_k: int = 0
    retrieved_chunks: list[dict[str, Any]] = field(default_factory=list)
    filtered_chunks: list[dict[str, Any]] = field(default_factory=list)
    selected_citations: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class RagTraceState:
    """按子任务追加 records。"""
    records: list[RagTaskRecord] = field(default_factory=list)


@dataclass
class SqlQueryTaskRecord:
    """单次 SQL 查询子任务轨迹，按 task_id 与 task_results 对齐。"""
    task_id: str
    last_sql: str | None = None
    citations: list[dict[str, Any]] = field(default_factory=list)
    order_line_items_by_order_id: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    status: str | None = None
    error: str | None = None


@dataclass
class SqlQueryTraceState:
    """Shop SQL 轨迹：与 rag_trace 分离；按子任务追加，避免多任务互相覆盖。"""
    records: list[SqlQueryTaskRecord] = field(default_factory=list)


@dataclass
class OrderTaskRecord:
    """单次订单子任务轨迹。"""
    task_id: str
    operation: OrderOperation | None = None
    source_dep_task_ids: list[str] = field(default_factory=list)
    loaded_items_count: int = 0
    status: str | None = None
    message: str | None = None
    order_link: str | None = None
    error: str | None = None


@dataclass
class OrderTraceState:
    """订单轨迹：按子任务追加。"""
    records: list[OrderTaskRecord] = field(default_factory=list)




@dataclass
class ObservabilityState:
    request_id: str = ""
    node_timings: dict[str, float] = field(default_factory=dict)
    node_logs: list[str] = field(default_factory=list)
    retries: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


@dataclass
class HandoffState:
    enabled: bool = False
    reason: str | None = None
    assigned_to: str | None = None
    status: str = "inactive"


@dataclass
class BaseTask:
    id: str
    text: str
    intent: Literal["query", "rule", "order", "handoff", "unknown", "session_meta"]
    status: str = "pending"
    depends_on: list[str] = field(default_factory=list)


@dataclass
class QueryTask(BaseTask):
    intent: Literal["query"] = "query"


@dataclass
class RuleTask(BaseTask):
    intent: Literal["rule"] = "rule"


@dataclass
class OrderTask(BaseTask):
    intent: Literal["order"] = "order"
    order_operation_hint: OrderOperation | None = None


@dataclass
class HandoffTask(BaseTask):
    intent: Literal["handoff"] = "handoff"


@dataclass
class UnknownTask(BaseTask):
    intent: Literal["unknown"] = "unknown"


@dataclass
class SessionMetaTask(BaseTask):
    intent: Literal["session_meta"] = "session_meta"


Task = QueryTask | RuleTask | OrderTask | HandoffTask | UnknownTask | SessionMetaTask #子任务类型


class SessionState(TypedDict):
    """会话级历史真源。"""

    conversation: ConversationState


class RuntimeState(TypedDict, total=False):
    """单轮编排执行态。"""

    text: str
    route: str
    sub_tasks: list[Task]
    task_results: list[dict[str, Any]]
    task_context: dict[str, dict[str, Any]]
    current_task_index: int
    pending_actions: list[dict[str, Any]]
    raw: "AgentResult"
    result: "AgentResult"


class TraceState(TypedDict):
    """调试与可观测态。"""

    rag_trace: RagTraceState
    sql_query_trace: SqlQueryTraceState
    order_trace: OrderTraceState
    observability: ObservabilityState


class GraphState(TypedDict):
    """LangGraph 单次 invoke 内的共享状态（标准化分层结构）。"""

    session: SessionState
    runtime: RuntimeState
    trace: TraceState


@dataclass
class AgentResult:
    route: str
    status: str
    message: str
    action_required: str | None = None
    order_link: str | None = None
    citations: list[dict] | None = None
    error: str | None = None
    request_id: str | None = None
    workflow_step: str | None = None
    handoff_status: str | None = None
    debug_trace: dict[str, Any] | None = None
    sub_task_count: int | None = None
    sub_task_progress: str | None = None
    pending_actions: list[dict[str, Any]] | None = None
    sql_query: str | None = None
