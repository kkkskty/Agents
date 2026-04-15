import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv


def _load_env_file() -> None:
    # backend/app/core/settings.py -> backend/.env
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)


_load_env_file()


@dataclass
class AgentLLMSettings:
    use_local: bool
    local_llm_model: str
    # Ollama HTTP 服务根地址（默认本机 11434）；与 Ollama 安装目录无关
    ollama_base_url: str
    """传给 ollama-python/httpx 的请求超时（秒），避免长推理被默认短超时切断。"""
    ollama_http_timeout_s: float
    """单次 LLM 调用在线程池中的最长等待（秒），须 >= 单次 Ollama 生成耗时。"""
    llm_invoke_timeout_s: float
    ollama_reasoning: bool  # ChatOllama(reasoning=...)；False 显式关闭深度思考
    cloud_llm_model: str
    cloud_llm_api_key: str | None
    cloud_llm_base_url: str | None
    llm_temperature: float


@dataclass
class AppSettings:
    service_name: str
    app_version: str
    api_v1_prefix: str
    cors_allow_origins: list[str]
    sessions_persistence: bool
    graph_debug_trace_enabled: bool
    llm_response_log_enabled: bool
    handoff_enabled: bool
    max_history_turns: int
    node_timeout_ms: int
    max_sub_tasks: int
    intent_confidence_threshold: float
    intent_agent_llm: AgentLLMSettings
    search_agent_llm: AgentLLMSettings
    rag_agent_llm: AgentLLMSettings
    summarizer_agent_llm: AgentLLMSettings
    chroma_path: str
    rag_top_k: int
    rag_collection_name: str
    rag_embedding_model: str
    rag_embedding_base_url: str
    rag_embedding_api_key: str | None
    mock_order_base_url: str
    mysql_host: str
    mysql_port: int
    mysql_user: str
    mysql_password: str
    mysql_database: str
    sql_max_rows: int


def _as_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _as_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _as_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _as_list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if raw is None:
        return default
    values = [x.strip() for x in raw.split(",")]
    return [x for x in values if x]


def _resolve_rag_embedding_api_key() -> str | None:
    """与 AICODE 对齐：多源 Key，占位符视为未配置。"""
    for name in (
        "RAG_EMBEDDING_API_KEY",
        "OPENAI_API_KEY",
        "LLM_API_KEY",
        "CHROMA_OPENAI_API_KEY",
        "RAG_AGENT_CLOUD_LLM_API_KEY",
    ):
        raw = os.getenv(name)
        if not raw:
            continue
        s = raw.strip()
        if s.upper() in ("YOUR_OPENAI_API_KEY", "YOUR_LLM_API_KEY", ""):
            continue
        return s
    return None


@lru_cache(maxsize=1)
def load_settings() -> AppSettings:
    rag_top_k = _as_int("RAG_TOP_K", 3)
    if rag_top_k <= 0:
        rag_top_k = 3

    def _agent_llm(prefix: str, local_model_default: str, cloud_model_default: str) -> AgentLLMSettings:
        ollama_url = os.getenv(f"{prefix}_OLLAMA_BASE_URL") or os.getenv(
            "OLLAMA_BASE_URL", "http://127.0.0.1:11434"
        )
        ollama_timeout = _as_float("OLLAMA_HTTP_TIMEOUT", 600.0)
        if ollama_timeout <= 0:
            ollama_timeout = 600.0
        invoke_timeout = _as_float("LLM_INVOKE_TIMEOUT_S", 300.0)
        if invoke_timeout <= 0:
            invoke_timeout = 300.0
        reasoning_raw = os.getenv(f"{prefix}_OLLAMA_REASONING")
        if reasoning_raw is not None and str(reasoning_raw).strip() != "":
            ollama_reasoning = reasoning_raw.strip().lower() in {"1", "true", "yes", "on"}
        else:
            ollama_reasoning = _as_bool("OLLAMA_REASONING", False)
        return AgentLLMSettings(
            use_local=_as_bool(f"{prefix}_USE_LOCAL", True),
            local_llm_model=os.getenv(f"{prefix}_LOCAL_LLM_MODEL", local_model_default),
            ollama_base_url=ollama_url.strip() or "http://127.0.0.1:11434",
            ollama_http_timeout_s=ollama_timeout,
            llm_invoke_timeout_s=invoke_timeout,
            ollama_reasoning=ollama_reasoning,
            cloud_llm_model=os.getenv(f"{prefix}_CLOUD_LLM_MODEL", cloud_model_default),
            cloud_llm_api_key=os.getenv(f"{prefix}_CLOUD_LLM_API_KEY"),
            cloud_llm_base_url=os.getenv(f"{prefix}_CLOUD_LLM_BASE_URL"),
            llm_temperature=_as_float(f"{prefix}_LLM_TEMPERATURE", 0.1),
        )

    return AppSettings(
        service_name=os.getenv("SERVICE_NAME", "multi-agent-customer-service"),
        app_version=os.getenv("APP_VERSION", "0.1.0"),
        api_v1_prefix=os.getenv("API_V1_PREFIX", "/api/v1"),
        cors_allow_origins=_as_list("CORS_ALLOW_ORIGINS", ["http://localhost:5173"]),
        sessions_persistence=_as_bool("SESSIONS_PERSISTENCE", False),
        graph_debug_trace_enabled=_as_bool("GRAPH_DEBUG_TRACE_ENABLED", False),
        llm_response_log_enabled=_as_bool("LLM_RESPONSE_LOG_ENABLED", False),
        handoff_enabled=_as_bool("HANDOFF_ENABLED", False),
        max_history_turns=_as_int("MAX_HISTORY_TURNS", 20),
        node_timeout_ms=_as_int("NODE_TIMEOUT_MS", 8000),
        max_sub_tasks=_as_int("MAX_SUB_TASKS", 5),
        intent_confidence_threshold=_as_float("INTENT_CONFIDENCE_THRESHOLD", 0.65),
        intent_agent_llm=_agent_llm("INTENT_AGENT", "qwen2.5:7b", "gpt-4o-mini"),
        search_agent_llm=_agent_llm("SEARCH_AGENT", "qwen2.5:7b", "gpt-4o-mini"),
        rag_agent_llm=_agent_llm("RAG_AGENT", "qwen2.5:7b", "gpt-4o-mini"),
        summarizer_agent_llm=_agent_llm("SUMMARIZER_AGENT", "qwen2.5:7b", "gpt-4o-mini"),
        chroma_path=os.getenv("CHROMA_PATH", r"D:\AICODE\data\chroma"),
        rag_top_k=rag_top_k,
        rag_collection_name=(os.getenv("RAG_COLLECTION_NAME") or "aicode_kb").strip() or "aicode_kb",
        rag_embedding_model=(os.getenv("RAG_EMBEDDING_MODEL") or "text-embedding-3-small").strip(),
        rag_embedding_base_url=(
            (
                os.getenv("RAG_EMBEDDING_BASE_URL")
                or os.getenv("OPENAI_BASE_URL")
                or os.getenv("LLM_BASE_URL")
                or "https://api.openai.com/v1"
            )
            .strip()
            .rstrip("/")
        ),
        rag_embedding_api_key=_resolve_rag_embedding_api_key(),
        mock_order_base_url=os.getenv("MOCK_ORDER_BASE_URL", "https://mock-order.local"),
        mysql_host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        mysql_port=_as_int("MYSQL_PORT", 3306),
        mysql_user=os.getenv("MYSQL_USER", "root"),
        mysql_password=os.getenv("MYSQL_PASSWORD", "123456"),
        mysql_database=os.getenv("MYSQL_DATABASE", "shop"),
        sql_max_rows=_as_int("SQL_MAX_ROWS", 50),
    )
