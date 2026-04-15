from uuid import uuid4

from app.core.settings import load_settings

SETTINGS = load_settings()


def create_order(payload: dict) -> dict:
    if payload.get("item_name") == "失败样例":
        return {"ok": False, "reason": "库存不足，无法创建订单"}
    items = payload.get("items")
    if isinstance(items, list):
        sanitized_items = []
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("item_name", "")).strip()
            qty = item.get("quantity", 1)
            try:
                qty_val = int(qty)
            except Exception:
                qty_val = 1
            if not name:
                continue
            sanitized_items.append({"item_name": name, "quantity": max(1, qty_val)})
    else:
        name = str(payload.get("item_name", "")).strip()
        qty = payload.get("quantity", 1)
        try:
            qty_val = int(qty)
        except Exception:
            qty_val = 1
        sanitized_items = [{"item_name": name, "quantity": max(1, qty_val)}] if name else []
    order_id = f"ORD-{uuid4().hex[:8].upper()}"
    item_count = len(sanitized_items)
    item_summary = "、".join(f"{i['item_name']} x{i['quantity']}" for i in sanitized_items[:5])
    if item_count > 5:
        item_summary += " 等"
    detail = f"，包含 {item_count} 个商品：{item_summary}" if sanitized_items else ""
    return {
        "ok": True,
        "order_id": order_id,
        "order_link": f"{SETTINGS.mock_order_base_url}/orders/{order_id}",
        "message": f"下单成功（Mock）{detail}。默认未调用支付 API，未扣余额。",
        "items": sanitized_items,
    }


def _valid_cancel_order_id(order_id: str) -> bool:
    s = str(order_id).strip()
    if not s:
        return False
    if s.startswith("ORD-"):
        return True
    return s.isdigit()


def cancel_order(payload: dict) -> dict:
    order_id = str(payload.get("order_id", "") or "").strip()
    if not _valid_cancel_order_id(order_id):
        return {"ok": False, "reason": "订单号不存在或格式错误"}
    ticket = f"RF-{uuid4().hex[:8].upper()}"
    return {
        "ok": True,
        "order_id": order_id,
        "order_link": f"{SETTINGS.mock_order_base_url}/refunds/{ticket}",
        "message": "退单/退款申请已创建（Mock），默认未调用支付 API。",
    }


def modify_order(payload: dict) -> dict:
    order_id = str(payload.get("order_id", "") or "").strip()
    if not _valid_cancel_order_id(order_id):
        return {"ok": False, "reason": "订单号不存在或格式错误"}
    change_id = f"CHG-{uuid4().hex[:8].upper()}"
    return {
        "ok": True,
        "order_id": order_id,
        "order_link": f"{SETTINGS.mock_order_base_url}/changes/{change_id}",
        "message": "订单信息修改成功（Mock）。",
    }
