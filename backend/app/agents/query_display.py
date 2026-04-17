"""查询结果展示：中英文列名映射、单元格格式化、模板拼接与 task outputs 轻量推断（与 SearchAgent 解耦）。"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

COLUMN_LABEL_ZH: dict[str, str] = {
    "id": "编号",
    "order_id": "订单号",
    "user_id": "用户",
    "status": "状态",
    "total_amount": "金额",
    "quantity": "数量",
    "unit_price": "单价",
    "reason": "原因",
    "item_name": "商品",
    "address": "地址",
    "contact_phone": "电话",
    "username": "用户名",
    "phone": "手机",
    "email": "邮箱",
    "name": "名称",
    "category": "分类",
    "price": "价格",
    "stock": "库存",
    "created_at": "创建时间",
    "updated_at": "更新时间",
    "field": "字段",
    "new_value": "新值",
    "product_id": "商品编号",
}

UNPAID_ORDER_STATUSES: frozenset[str] = frozenset({"pending", "unpaid", "awaiting_payment"})

ORDER_STATUS_ZH: dict[str, str] = {
    "pending": "待处理",
    "completed": "已完成",
    "cancelled": "已取消",
    "paid": "已支付",
    "shipped": "已发货",
    "refunded": "已退款",
    "failed": "失败",
}


def format_cell(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, Decimal):
        s = format(value, "f")
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return s
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    return str(value)


def format_row_for_display(row: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, raw in row.items():
        label = COLUMN_LABEL_ZH.get(key, key)
        if key == "status" and isinstance(raw, str):
            v_zh = ORDER_STATUS_ZH.get(raw.strip().lower(), format_cell(raw))
            parts.append(f"{label}：{v_zh}")
        elif key == "total_amount":
            parts.append(f"{label}：{format_cell(raw)} 元")
        else:
            parts.append(f"{label}：{format_cell(raw)}")
    return "，".join(parts)


def format_line_items_brief(items: list[dict[str, Any]]) -> str:
    if not items:
        return "（无明细）"
    segs: list[str] = []
    for it in items:
        name = str(it.get("product_name") or "商品")
        qty = it.get("quantity", 0)
        price = it.get("unit_price")
        price_s = ""
        if price is not None:
            price_s = f"，单价 {format_cell(price)} 元"
        segs.append(f"{name} ×{qty}{price_s}")
    return "；".join(segs)


def unpaid_order_ids_from_rows(rows: list[Any]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for r in rows:
        d = dict(r)
        if "status" not in d:
            continue
        st = str(d.get("status") or "").strip().lower()
        if st not in UNPAID_ORDER_STATUSES:
            continue
        # 兼容 SQL 别名：订单主键可能是 id，也可能被命名为 order_id。
        oid = d.get("id")
        if oid is None:
            oid = d.get("order_id")
        if oid is None:
            continue
        s = str(oid)
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def build_query_result_template(rows: list[dict[str, Any]], max_display: int = 3) -> str:
    display_lines: list[str] = []
    for r in rows[:5]:
        display_lines.append(format_row_for_display(dict(r)))
    snippets = "\n".join(f"{i}. {line}" for i, line in enumerate(display_lines[:max_display], start=1))
    template = f"查询结果：\n{snippets}"
    if len(display_lines) > max_display:
        template += f"\n（共 {len(rows)} 条，上文展示前 {max_display} 条）"
    elif len(rows) > len(display_lines):
        template += f"\n（共 {len(rows)} 条）"
    return template


def build_citation_snippets(rows: list[dict[str, Any]], limit: int = 5) -> list[str]:
    return [format_row_for_display(dict(r)) for r in rows[:limit]]


def build_search_task_outputs(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """从主查询结果行推断 task_context.outputs（无数据库比价）。

    协议字段：unpaid_order_ids、proposed_order_items、order_items_by_order_id。
    """
    unpaid = unpaid_order_ids_from_rows(rows)
    proposed: list[dict[str, Any]] = []
    order_items_by_order_id: dict[str, list[dict[str, Any]]] = {}
    seen_pid: set[int] = set()
    for r in rows:
        d = dict(r)
        pid_raw = d.get("product_id")
        try:
            ipid = int(pid_raw) if pid_raw is not None else None
        except (TypeError, ValueError):
            ipid = None
        name = d.get("product_name") or d.get("name") or d.get("item_name")
        label = str(name).strip() if name is not None else ""
        oid_raw = d.get("order_id")
        oid_s = str(oid_raw).strip() if oid_raw is not None else ""
        # 仅接受真实商品名称（如 products.name / item_name）。
        # 若只有 product_id 无名称，则交给后续补查流程（order_items + products）填充，避免前端展示“商品#ID”占位名。
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
    return {
        "ordered_items": [],
        "current_prices": [],
        "drop_items": [],
        "proposed_order_items": proposed[:20],
        "unpaid_order_ids": unpaid,
        "order_items_by_order_id": order_items_by_order_id,
    }
