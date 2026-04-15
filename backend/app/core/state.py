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


@dataclass
class ConversationState:
    session_id: str
    user_id: str
    turn_index: int = 0
    history: list[ConversationTurn] = field(default_factory=list)
    last_intent: str | None = None
    active_intent: str | None = None


@dataclass
class OrderExecutionResult:
    ok: bool | None = None
    message: str | None = None
    order_link: str | None = None
    reason: str | None = None


@dataclass
class OrderWorkflowState:
    operation: OrderOperation | None = None
    step: OrderStatus = "collecting_info"
    required_fields: list[str] = field(default_factory=list)
    collected_fields: dict[str, str] = field(default_factory=dict)
    pre_confirmed: bool = False
    execution_result: OrderExecutionResult = field(default_factory=OrderExecutionResult)


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
    """按子任务追加 records；retrieval_* / selected_* 保留为最近一次写入（兼容调试）。"""
    records: list[RagTaskRecord] = field(default_factory=list)
    retrieval_query: str | None = None
    top_k: int = 0
    retrieved_chunks: list[dict[str, Any]] = field(default_factory=list)
    filtered_chunks: list[dict[str, Any]] = field(default_factory=list)
    selected_citations: list[dict[str, Any]] = field(default_factory=list)


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
class SubTask:
    id: str
    text: str
    intent: str
    status: str = "pending"
    depends_on: list[str] = field(default_factory=list)
    order_operation_hint: OrderOperation | None = None

class GraphState(TypedDict, total=False):
    """LangGraph 单次 invoke 内的共享状态（每轮用户消息会构造新实例并注入会话级对象）。"""

    conversation: ConversationState  # 会话：session_id、user_id、多轮 history、当前/上一轮意图
    order_workflow: OrderWorkflowState  # 订单流程镜像：与 OrderContext 同步的 step、已收集字段等（便于展示）
    rag_trace: RagTraceState  # 规则/RAG 子任务检索轨迹（按 task 追加 records）
    sql_query_trace: SqlQueryTraceState  # SQL 查询子任务轨迹（按 task 追加 records）
    observability: ObservabilityState  # 可观测：request_id、节点耗时、节点日志、错误列表
    handoff: HandoffState  # 是否转人工及原因
    text: str  # 本轮用户原始输入全文
    session_meta_recall: bool  # 是否命中「会话元问题」短路（仅回顾历史，不跑检索）
    continuing_order_session: bool  # 是否处于订单续轮（decompose 未走意图拆分、整句当 order）
    route: str  # dispatch 当前步路由：query|rule|order|handoff|unknown|safe_response|all_done 等
    sub_tasks: list[SubTask]  # 意图拆分后的子任务列表（含 id、intent、depends_on）
    task_results: list[dict[str, Any]]  # 各子任务执行结果摘要（与 collect_result 对齐）
    task_context: dict[str, dict[str, Any]]  # 按 task_id 存放 citations、outputs、SQL 明细等
    current_task_index: int  # 当前正在执行（或刚完成待汇总）的子任务下标
    pending_actions: list[dict[str, Any]]  # 待用户侧动作（如订单二次确认）
    raw: "AgentResult"  # 当前子节点产出的原始 AgentResult（如 query/order 单步结果）
    result: "AgentResult"  # summarize 节点写入的最终对外结果（整轮回复）


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
