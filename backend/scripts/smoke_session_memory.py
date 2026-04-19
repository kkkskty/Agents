"""一次性冒烟：会话裁剪与路由上下文块。

用法（在仓库任意目录）：PYTHONPATH 指向 backend 根目录，或使用 conda 环境的 python：

  conda activate aicode-py311
  cd backend
  python scripts/smoke_session_memory.py
"""

from pathlib import Path
import sys

_backend_root = Path(__file__).resolve().parents[1]
if str(_backend_root) not in sys.path:
    sys.path.insert(0, str(_backend_root))

from fastapi.testclient import TestClient

from app.core.conversation_context import build_context_for_router
from app.core.session_memory import trim_history_if_needed
from app.core.settings import load_settings
from app.core.state import ConversationState, ConversationTurn
from app.deps import session_store
from app.main import app


def main() -> None:
    settings = load_settings()
    k = settings.session_memory_rounds_k
    max_keep = 2 * k
    print("SESSION_MEMORY_ROUNDS_K", k, "max_keep_turns", max_keep)

    s = ConversationState(session_id="t1", user_id="u1")
    for i in range(50):
        role = "user" if i % 2 == 0 else "assistant"
        s.history.append(ConversationTurn(role=role, content=f"m{i}", intent=None))

    trim_history_if_needed(s, settings)
    print("after trim history_len", len(s.history), "expected", max_keep)
    print("memory_summary_chars", len(s.memory_summary or ""))
    assert len(s.history) == max_keep

    block = build_context_for_router(s, "还是刚才那个订单", settings)
    assert "还是刚才那个订单" in block
    print("router_block chars", len(block))

    c = TestClient(app)
    state = session_store.get_or_create_graph_state("t2", "u1")
    conv = state["session"]["conversation"]
    conv.memory_summary = "摘要一句"
    conv.history.append(ConversationTurn(role="user", content="你好", intent="query"))
    conv.history.append(ConversationTurn(role="assistant", content="在的", intent="query"))
    session_store.save_graph_state("t2", state)

    # 会话相关 API 仍可访问（overflow-preview 已移除）
    r = c.get("/api/v1/health")
    print("GET health", r.status_code)
    assert r.status_code == 200

    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
