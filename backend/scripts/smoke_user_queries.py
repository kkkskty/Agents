"""
端到端冒烟：用真实中文用户句调用 orchestrator.process_message。
需要：意图 LLM、（query 时）Search LLM + MySQL、（rule 时）RAG/Chroma + Embedding 等按环境而定。

用法（在仓库内）:
  conda activate aicode-py311
  cd backend
  set PYTHONPATH=%CD%
  python scripts/smoke_user_queries.py
  python scripts/smoke_user_queries.py --no-rag   # 跳过走 RAG/Embedding 的规则问句（默认 u2）
"""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

_backend_root = Path(__file__).resolve().parents[1]
if str(_backend_root) not in sys.path:
    sys.path.insert(0, str(_backend_root))


def main() -> None:
    parser = argparse.ArgumentParser(description="端到端用户句冒烟")
    parser.add_argument(
        "--no-rag",
        action="store_true",
        help="跳过依赖 RAG/向量 Embedding 的场景（默认跳过标签 u2：退单规则）",
    )
    args = parser.parse_args()

    from app.core.orchestrator import MultiAgentOrchestrator
    from app.core.session_store import SessionStore

    store = SessionStore()
    orch = MultiAgentOrchestrator(store)

    # 多种真实用户问法（单轮独立 session，避免相互污染）
    cases: list[tuple[str, str]] = [
        ("u1", "我的订单有哪些"),
        ("u2", "退单要怎么操作，有什么规则"),
        ("u3", "我刚才说了什么"),
        ("u4", "小米充电器多少钱，库存还有多少"),
        ("u5", "帮我下一单"),
        ("u6", "查询我的订单，看下哪些降价了，帮我重新下单一份"),
        ("u7", "转人工"),
        ("u8", "你好，在吗"),
    ]
    skip_labels: set[str] = {"u2"} if args.no_rag else set()
    if skip_labels:
        cases = [(a, b) for a, b in cases if a not in skip_labels]
        print(f"（已跳过 RAG 场景标签: {sorted(skip_labels)}）\n")

    # 与 shop 库 orders.user_id 常见整型一致；若你库为字符串用户，请改此变量
    demo_uid = "1"

    print("=== 独立会话（每句一个新 session）===\n")
    for label, text in cases:
        sid = None
        try:
            sid, result = orch.process_message(user_id=demo_uid, text=text, session_id=sid)
            tid = getattr(result, "turn_id", None)
            print(f"[{label}] session={sid}")
            print(f"  route={result.route} status={result.status} turn_id={tid}")
            msg = (result.message or "").replace("\n", " ")[:320]
            print(f"  reply_preview: {msg}{'...' if len(result.message or '') > 320 else ''}")
            if result.error:
                print(f"  error: {result.error}")
        except Exception as exc:
            print(f"[{label}] FAILED: {exc!r}")
            traceback.print_exc()

    # 同一 session 两轮：第二轮带指代「刚才」（用小米充电器与库内商品对齐）
    print("\n=== 同一 session 连续两轮 ===\n")
    sid = None
    for step, text in enumerate(
        [
            "帮我查一下小米充电器多少钱",
            "刚才那个单价多少，帮我买一个",
        ],
        start=1,
    ):
        try:
            sid, result = orch.process_message(user_id=demo_uid, text=text, session_id=sid)
            print(f"[step{step}] session={sid} route={result.route} status={result.status}")
            print(f"  turn_id={getattr(result, 'turn_id', None)}")
            print(f"  reply_preview: {(result.message or '')[:260]}...")
        except Exception as exc:
            print(f"[step{step}] FAILED: {exc!r}")
            traceback.print_exc()


if __name__ == "__main__":
    main()
