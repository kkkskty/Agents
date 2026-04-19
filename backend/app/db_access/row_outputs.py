"""从查询结果行推断 Search 子任务 task_context.outputs（逻辑由 SqlRowOutputPolicy 驱动）。"""

from __future__ import annotations

from typing import Any

from app.core.settings import load_settings
from app.db_access.policies import SqlRowOutputPolicy, policy_from_app_settings


def unpaid_order_ids_from_rows(rows: list[Any], policy: SqlRowOutputPolicy) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for r in rows:
        d = dict(r)
        if "status" not in d:
            continue
        st = str(d.get("status") or "").strip().lower()
        if st not in policy.unpaid_statuses:
            continue
        oid = None
        for k in policy.order_id_keys:
            if k in d and d.get(k) is not None:
                oid = d.get(k)
                break
        if oid is None:
            continue
        s = str(oid)
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def build_search_task_outputs(
    rows: list[dict[str, Any]],
    policy: SqlRowOutputPolicy | None = None,
) -> dict[str, Any]:
    """协议字段：unpaid_order_ids、proposed_order_items、order_items_by_order_id。"""
    if policy is None:
        policy = policy_from_app_settings(load_settings())
    unpaid = unpaid_order_ids_from_rows(rows, policy)
    proposed: list[dict[str, Any]] = []
    order_items_by_order_id: dict[str, list[dict[str, Any]]] = {}
    seen_pid: set[int] = set()
    name_keys = policy.product_name_keys

    for r in rows:
        d = dict(r)
        pid_raw = d.get("product_id")
        try:
            ipid = int(pid_raw) if pid_raw is not None else None
        except (TypeError, ValueError):
            ipid = None
        label = ""
        for nk in name_keys:
            v = d.get(nk)
            if v is not None and str(v).strip():
                label = str(v).strip()
                break
        oid_raw = d.get("order_id")
        oid_s = str(oid_raw).strip() if oid_raw is not None else ""
        if not label:
            continue
        if ipid is not None and ipid in seen_pid:
            continue
        if ipid is not None:
            seen_pid.add(ipid)
        qty_raw = d.get("quantity", 1)
        try:
            qty = max(1, int(qty_raw))
        except (TypeError, ValueError):
            qty = 1
        item: dict[str, Any] = {"item_name": label, "quantity": qty}
        if ipid is not None:
            item["product_id"] = ipid
        if oid_s:
            item["order_id"] = oid_s
            line = {"item_name": label, "quantity": str(qty)}
            if ipid is not None:
                line["product_id"] = ipid
            bucket = order_items_by_order_id.setdefault(oid_s, [])
            sig = (line.get("product_id"), line["item_name"], line["quantity"])
            if not any(
                (x.get("product_id"), x.get("item_name"), str(x.get("quantity"))) == sig for x in bucket
            ):
                bucket.append(line)
        proposed.append(item)

    cap = max(1, policy.max_proposed_items)
    return {
        "ordered_items": [],
        "current_prices": [],
        "drop_items": [],
        "proposed_order_items": proposed[:cap],
        "unpaid_order_ids": unpaid,
        "order_items_by_order_id": order_items_by_order_id,
    }
