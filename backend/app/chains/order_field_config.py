"""订单字段规则单一来源：必填 / 展示 / 只读 / 可修改 / 表单白名单。"""

from __future__ import annotations

from typing import Literal

OrderOperation = Literal["create", "cancel", "modify"]

# ---------- 字段规则（与《订单结构与校验标准化改造计划》一致）----------

REQUIRED_FIELDS_BY_OPERATION: dict[str, list[str]] = {
    "create": ["item_name", "quantity", "address", "contact_phone"],
    "cancel": ["order_id", "reason"],
    "modify": ["order_id"],
}

DISPLAY_FIELDS_BY_OPERATION: dict[str, list[str]] = {
    "create": ["item_name", "quantity", "address", "contact_phone"],
    "cancel": [
        "order_id",
        "item_name",
        "quantity",
        "remark",
        "address",
        "contact_phone",
        "reason",
    ],
    "modify": [
        "order_id",
        "item_name",
        "quantity",
        "remark",
        "address",
        "contact_phone",
    ],
}

READONLY_FIELDS_BY_OPERATION: dict[str, list[str]] = {
    "create": [],
    "cancel": ["order_id"],
    "modify": ["order_id"],
}

# modify：至少一项视为“已发起修改”；不含 item_name（展示用）
MUTABLE_FIELDS_MODIFY: list[str] = ["quantity", "remark", "address", "contact_phone"]

# POST /orders/fill-fields 允许写入的字段（含展示用 item_name）
ALLOWED_FORM_FIELDS_BY_OPERATION: dict[str, list[str]] = {
    "create": ["item_name", "quantity", "address", "contact_phone"],
    "cancel": [
        "order_id",
        "item_name",
        "quantity",
        "remark",
        "reason",
        "address",
        "contact_phone",
    ],
    "modify": [
        "order_id",
        "item_name",
        "quantity",
        "remark",
        "address",
        "contact_phone",
    ],
}

FIELD_LABEL_ZH: dict[str, str] = {
    "item_name": "商品名称",
    "quantity": "数量",
    "address": "收货地址",
    "contact_phone": "联系电话",
    "order_id": "订单号",
    "reason": "退单原因",
    "remark": "备注",
    "modify_payload": "至少提供一项可修改信息",
}


def required_fields_for(operation: str | None) -> list[str]:
    return list(REQUIRED_FIELDS_BY_OPERATION.get(operation or "", []))


def display_fields_for(operation: str | None) -> list[str]:
    return list(DISPLAY_FIELDS_BY_OPERATION.get(operation or "", []))


def readonly_fields_for(operation: str | None) -> list[str]:
    return list(READONLY_FIELDS_BY_OPERATION.get(operation or "", []))


def allowed_form_field_keys(operation: str | None) -> set[str]:
    return set(ALLOWED_FORM_FIELDS_BY_OPERATION.get(operation or "", []))


def mutable_fields_modify() -> list[str]:
    return list(MUTABLE_FIELDS_MODIFY)
