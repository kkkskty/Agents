"""两轮同 session 自测：打印每轮 route、reply 摘要、订单上下文状态。"""
from __future__ import annotations

import os
import sys

# 保证从 backend 根目录可 import app
_BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

# 新进程独立 SessionStore，避免与已跑着的 uvicorn 混用
from app.core.session_store import SessionStore
from app.core.orchestrator import MultiAgentOrchestrator


def _print_block(title: str, r, store, sid: str, m2: str) -> None:
    print(title)
    print("route:", r.route, "status:", r.status)
    print("reply (<=1200 chars):\n", (r.message or "")[:1200])
    o = store.get_order(sid)
    if o:
        print("order:", "status=", o.status, "operation=", o.operation, "items_len=", len(o.items or []))
    msg = r.message or ""
    if "降价" in msg and "降价" not in m2:
        print("[启发式] 本轮回复含「降价」但用户句未含——留意串话/SQL/总结。")


def main() -> None:
    m1 = "查询我的订单 降价的再下一单"
    m2 = "查询我的订单 涨价的再下一单"

    # --- A：连续两轮，不手动清订单（复现「订单未收尾则第二轮不拆意图」）---
    store_a = SessionStore()
    orch_a = MultiAgentOrchestrator(session_store=store_a)
    sid_a = "selftest_session_A"
    uid = "selftest_user"

    _, r1a = orch_a.process_message(uid, m1, sid_a)
    print("========== 场景 A：两轮连续（订单可能未 closed）==========")
    _print_block("--- ROUND 1 ---", r1a, store_a, sid_a, m1)
    _, r2a = orch_a.process_message(uid, m2, sid_a)
    print()
    _print_block("--- ROUND 2 ---", r2a, store_a, sid_a, m2)

    # --- B：第一轮后 clear_order，再发第二轮（模拟「订单流程已结束」）---
    print()
    print("========== 场景 B：第一轮后 clear_order，再 ROUND 2 ==========")
    store_b = SessionStore()
    orch_b = MultiAgentOrchestrator(session_store=store_b)
    sid_b = "selftest_session_B"
    _, r1b = orch_b.process_message(uid, m1, sid_b)
    _print_block("--- ROUND 1 ---", r1b, store_b, sid_b, m1)
    store_b.clear_order(sid_b)
    print("[中间] clear_order(%s)" % sid_b)
    _, r2b = orch_b.process_message(uid, m2, sid_b)
    print()
    _print_block("--- ROUND 2 ---", r2b, store_b, sid_b, m2)


if __name__ == "__main__":
    main()
