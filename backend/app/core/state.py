from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict


OrderOperation = Literal["create", "cancel", "modify"]
OrderStatus = Literal[
    "collecting_info",
    "awaiting_pre_confirm",
    "closed",
    "failed",
]


class DbOrderRow(TypedDict, total=False):
    """orders 表对应结构。"""

    id: str | int
    user_id: str | int
    status: str
    total_amount: float | int | str
    created_at: str
    updated_at: str


class DbOrderItemRow(TypedDict, total=False):
    """order_items 表对应结构。"""

    id: str | int
    order_id: str | int
    user_id: str | int
    product_id: str | int
    quantity: str | int
    unit_price: float | int | str


class OrderCollectedItem(TypedDict, total=False):
    """下单/修改收集用条目（前后端表单交互结构）。"""

    order_id: str | int
    product_id: str | int
    item_name: str
    quantity: str


class OrderCollectedFields(TypedDict, total=False):
    """订单收集字段（按前台表单字段定义）。"""

    order_id: str
    item_name: str
    quantity: str
    address: str
    contact_phone: str
    remark: str
    reason: str


OrderFormFields = OrderCollectedFields


class OrderHeader(TypedDict, total=False):
    """orders 主表语义（查询/依赖协议用）。"""

    order_id: str | int
    user_id: str | int
    status: str
    total_amount: float | int | str
    created_at: str
    updated_at: str


class OrderLineItem(TypedDict, total=False):
    """order_items + products 联查语义（依赖协议 proposed_order_items）。"""

    order_id: str | int
    product_id: str | int
    item_name: str
    quantity: str | int
    unit_price: float | int | str


@dataclass
class OrderContext:
    session_id: str
    user_id: str
    operation: OrderOperation | None = None
    status: OrderStatus = "collecting_info"
    fields: OrderCollectedFields = field(default_factory=dict)
    items: list[OrderCollectedItem] = field(default_factory=list)
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
    """会话结构版本；升级 StepRef 等模型时递增。"""
    session_schema_version: int = 2
    history: list[ConversationTurn] = field(default_factory=list)
    """被裁剪出热窗口后的折叠文本，供 IntentRouter 等使用。"""
    memory_summary: str = ""
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


@dataclass(frozen=True)
class StepRef:
    """依赖边：turn_id + 会话内全局唯一的 step_id（形如 {turn_uuid}:task_0）。"""

    turn_id: str
    step_id: str


@dataclass
class StepArtifact:
    """单步执行产物，供跨轮依赖与 Injector 消费。

    turn_id — 本子任务所属用户轮次 id（一轮用户输入对应一次 process_message、一个 turn_id）。
    step_id — 会话内唯一步骤 id，与编排里 BaseTask.id 一致；SessionStore 按 session_id + step_id 索引。
    intent — 子任务意图：query / rule / order / handoff / session_meta / unknown 等。
    status — 本步执行状态，与 AgentResult.status 对齐（如 ok、error、collecting_info、no_result）。
    message — 可读摘要文本，通常来自 AgentResult.message（入库时可截断）。
    error — 失败时的错误码或简要标识；成功一般为 None。
    payload — 结构化上下文副本（如 runtime「task_context」对应 task 的字典），跨轮依赖主要读此字段而非仅读 message。
    """

    turn_id: str
    step_id: str
    intent: str
    status: str
    message: str = ""
    error: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class BaseTask:
    id: str
    text: str
    intent: Literal["query", "rule", "order", "handoff", "unknown", "session_meta"]
    status: str = "pending"
    depends_on: list[StepRef] = field(default_factory=list)


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
    turn_id: str
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
    turn_id: str | None = None
