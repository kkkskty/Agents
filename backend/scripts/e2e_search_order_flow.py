"""端到端：长句查询+下单，直至 Mock 订单执行成功（需 Ollama + MySQL + Conda 环境）。"""

import sys

from app.deps import orchestrator


def main() -> int:
    uid = "u_e2e_script"
    q1 = "查询我的订单，看那些产品降价了，帮我重下单一份"
    print("=== round1 ===", q1, flush=True)
    sid, r = orchestrator.process_message(uid, q1, None)
    print("session", sid, "route", r.route, "status", r.status, flush=True)
    if r.error:
        print("error", r.error, flush=True)
    print("reply_preview", (r.message or "")[:800], flush=True)

    q2 = (
        "商品名称：测试商品A 数量：2 "
        "收货地址：北京市朝阳区测试路1号 "
        "联系电话：13800138000"
    )
    print("=== round2 ===", q2, flush=True)
    sid2, r2 = orchestrator.process_message(uid, q2, sid)
    print("route", r2.route, "status", r2.status, flush=True)
    if r2.error:
        print("error", r2.error, flush=True)
    print("reply_preview", (r2.message or "")[:800], flush=True)

    q3 = "确认下单"
    print("=== round3 ===", q3, flush=True)
    sid3, r3 = orchestrator.process_message(uid, q3, sid2)
    print("route", r3.route, "status", r3.status, flush=True)
    if r3.error:
        print("error", r3.error, flush=True)
    print("reply_preview", (r3.message or "")[:800], flush=True)
    print("order_link", getattr(r3, "order_link", None), flush=True)

    r_last = r3
    sid_last = sid3
    for i, extra in enumerate(
        (
            "确认",
            "是",
        ),
        start=4,
    ):
        if r_last.status in ("executed_waiting_click", "closed"):
            break
        print(f"=== round{i} ===", extra, flush=True)
        sid_last, r_last = orchestrator.process_message(uid, extra, sid_last)
        print("route", r_last.route, "status", r_last.status, flush=True)
        print("order_link", getattr(r_last, "order_link", None), flush=True)

    ok = r_last.status in ("executed_waiting_click", "closed") and (
        r_last.order_link or "成功" in (r_last.message or "") or "Mock" in (r_last.message or "")
    )
    if ok:
        print("E2E_OK", flush=True)
        return 0
    print("E2E_INCOMPLETE (may need more rounds or env)", flush=True)
    print("last_status", r_last.status, "last_msg", (r_last.message or "")[:400], flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
