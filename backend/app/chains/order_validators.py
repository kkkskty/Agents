"""订单字段规范化、解析与校验（单一入口）。"""

from __future__ import annotations

import re
from typing import Literal

from app.chains.order_field_config import FIELD_LABEL_ZH, mutable_fields_modify, required_fields_for
from app.core.state import OrderContext

# ----- 稳定错误码（API / 可观测约定）-----

MISSING_ORDER_OPERATION = "missing_order_operation"
MISSING_OPERATION = "missing_operation"

MISSING_REQUIRED_FIELDS = "missing_required_fields"
MISSING_ITEM_NAME = "missing_item_name"
MISSING_QUANTITY = "missing_quantity"
MISSING_ADDRESS = "missing_address"
MISSING_CONTACT_PHONE = "missing_contact_phone"
MISSING_REASON = "missing_reason"
MISSING_ORDER_ID = "missing_order_id"
MODIFY_MISSING_MUTABLE_FIELDS = "modify_missing_mutable_fields"

INVALID_PHONE = "invalid_phone"
INVALID_ORDER_ID_FORMAT = "invalid_order_id_format"

MISSING_DEPENDENCY_DATA = "missing_dependency_data"
MISSING_DEPENDENCY_ORDER_IDS = "missing_dependency_order_ids"
MISSING_DEPENDENCY_ITEMS = "missing_dependency_items"

NOT_AWAITING_PRE_CONFIRM = "not_awaiting_pre_confirm"
CONFIRM_INPUT_INVALID = "confirm_input_invalid"

ORDER_API_FAILED = "order_api_failed"
ORDER_CANCELLED_BY_USER = "order_cancelled_by_user"

FIELD_KEY_TO_ERROR_CODE: dict[str, str] = {
    "item_name": MISSING_ITEM_NAME,
    "quantity": MISSING_QUANTITY,
    "address": MISSING_ADDRESS,
    "contact_phone": MISSING_CONTACT_PHONE,
    "reason": MISSING_REASON,
    "order_id": MISSING_ORDER_ID,
    "modify_payload": MODIFY_MISSING_MUTABLE_FIELDS,
}


def normalize_input_text(text: str) -> str:
    return (text or "").replace("\n", " ").replace("\t", " ").strip()


def normalize_digits(raw: str) -> str:
    return re.sub(r"\D", "", raw or "")


def normalize_quantity(raw: str | int | None, default: str = "1") -> str:
    try:
        q = int(str(raw or "").strip())
        return str(q if q > 0 else int(default))
    except (TypeError, ValueError):
        return default


def is_valid_phone(raw: str, min_digits: int = 3) -> bool:
    d = normalize_digits(raw)
    return len(d) >= min_digits


def is_valid_order_id(raw: str) -> bool:
    s = str(raw or "").strip()
    return bool(re.fullmatch(r"[A-Za-z0-9\-]+", s))


def normalize_field_value(raw: str | None) -> str:
    return str(raw or "").strip()


def missing_keys_to_codes(missing_keys: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for k in missing_keys:
        code = FIELD_KEY_TO_ERROR_CODE.get(k) or MISSING_REQUIRED_FIELDS
        if code not in seen:
            seen.add(code)
            out.append(code)
    return out


def missing_order_field_keys(ctx: OrderContext) -> list[str]:
    op = ctx.operation
    if op not in ("create", "cancel", "modify"):
        return []

    required = required_fields_for(op)
    if op == "create" and ctx.items:
        required = [f for f in required if f not in {"item_name", "quantity"}]

    missing = [f for f in required if not normalize_field_value(ctx.fields.get(f))]

    if op == "modify":
        has_mutation = any(
            normalize_field_value(ctx.fields.get(field)) for field in mutable_fields_modify()
        )
        if not has_mutation:
            missing.append("modify_payload")

    return missing


def format_validation_codes(ctx: OrderContext) -> list[str]:
    codes: list[str] = []
    op = ctx.operation
    if op not in ("create", "cancel", "modify"):
        return []

    phone = normalize_field_value(ctx.fields.get("contact_phone"))
    if phone and op in ("create", "modify") and not is_valid_phone(phone):
        codes.append(INVALID_PHONE)

    oid = normalize_field_value(ctx.fields.get("order_id"))
    if oid and op in ("cancel", "modify") and not is_valid_order_id(oid):
        codes.append(INVALID_ORDER_ID_FORMAT)

    return codes


def order_form_correction_field_keys(ctx: OrderContext) -> list[str]:
    missing = missing_order_field_keys(ctx)
    keys: list[str] = list(dict.fromkeys(missing))
    fmt = format_validation_codes(ctx)
    if INVALID_PHONE in fmt and "contact_phone" not in keys:
        keys.append("contact_phone")
    if INVALID_ORDER_ID_FORMAT in fmt and "order_id" not in keys:
        keys.append("order_id")
    return keys


def collect_order_validation_codes(ctx: OrderContext) -> list[str]:
    missing_codes = missing_keys_to_codes(missing_order_field_keys(ctx))
    fmt_codes = format_validation_codes(ctx)
    out: list[str] = []
    seen: set[str] = set()
    for c in fmt_codes + missing_codes:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def primary_order_validation_code(codes: list[str]) -> str:
    if not codes:
        return MISSING_REQUIRED_FIELDS
    for strict in (INVALID_PHONE, INVALID_ORDER_ID_FORMAT):
        if strict in codes:
            return strict
    if len(codes) == 1:
        return codes[0]
    return MISSING_REQUIRED_FIELDS


def _operation_label_zh(op: str | None) -> str:
    mapping = {"create": "下单", "cancel": "退单", "modify": "修改订单"}
    return mapping.get(op or "", "订单操作")


def missing_field_labels_zh(missing: list[str]) -> str:
    labels: list[str] = []
    for f in missing:
        if f == "modify_payload":
            labels.append("至少提供一项可修改信息（数量、备注、收货地址、联系电话）")
        else:
            labels.append(FIELD_LABEL_ZH.get(f, f))
    return "、".join(labels)


def collect_validate_user_message(ctx: OrderContext) -> str:
    missing = missing_order_field_keys(ctx)
    fmt_codes = format_validation_codes(ctx)
    parts: list[str] = []
    if INVALID_PHONE in fmt_codes:
        parts.append("联系电话格式不正确")
    if INVALID_ORDER_ID_FORMAT in fmt_codes:
        parts.append("订单号格式不正确")
    if missing:
        parts.append(f"请补充以下信息：{missing_field_labels_zh(missing)}")
    body = "；".join(parts)
    return f"为继续{_operation_label_zh(ctx.operation)}，{body}。"


def parse_collect_order_fields(ctx: OrderContext, text: str) -> None:
    """从用户自然语言解析并写入 ctx.fields（写入前做可接受性格式判断）。"""
    norm = normalize_input_text(text)

    if ctx.operation == "create":
        if not normalize_field_value(ctx.fields.get("address")):
            am = re.search(
                r"(?:收货地址|收获地址|地址|address)\s*[：:]\s*(.+?)(?=(?:\s|^)(?:联系电话|联系方式|手机|手机号|电话|phone)\s*[：:]|$)",
                norm,
                re.IGNORECASE | re.DOTALL,
            )
            if am:
                ctx.fields["address"] = am.group(1).strip().rstrip(" ，,;；")
        if not normalize_field_value(ctx.fields.get("contact_phone")):
            pm = re.search(
                r"(?:联系电话|联系方式|手机|手机号|电话|phone)\s*[：:]\s*([+\d\s\-]{1,40})",
                norm,
                re.IGNORECASE,
            )
            if pm:
                d = normalize_digits(pm.group(1))
                if is_valid_phone(d):
                    ctx.fields["contact_phone"] = d
            if not normalize_field_value(ctx.fields.get("contact_phone")):
                pm2 = re.search(
                    r"(?:联系电话|联系方式|手机|手机号|电话|phone)\s*(?:[是为]?\s*)?([1][\d\s\-]{10,18})",
                    norm,
                    re.IGNORECASE,
                )
                if pm2:
                    d2 = normalize_digits(pm2.group(1))
                    if len(d2) >= 11 and d2.startswith("1"):
                        ctx.fields["contact_phone"] = d2[:11]
                    elif is_valid_phone(d2):
                        ctx.fields["contact_phone"] = d2
            if not normalize_field_value(ctx.fields.get("contact_phone")):
                norm_m = re.sub(r"(?<=\d)\s+(?=\d)", "", norm)
                m3 = re.search(r"(?<!\d)(1[3-9]\d{9})(?!\d)", norm_m)
                if m3:
                    ctx.fields["contact_phone"] = m3.group(1)
        im = re.search(
            r"(?:商品名称|商品|item_name|item)\s*[：:]\s*(.+?)(?=(?:\s|^)(?:数量|qty)\s*[：:]|$)",
            norm,
            re.IGNORECASE,
        )
        if im:
            ctx.fields["item_name"] = im.group(1).strip()
        qm = re.search(r"(?:数量|qty)\s*[：:]\s*(\d+)", norm, re.IGNORECASE)
        if qm:
            ctx.fields["quantity"] = normalize_quantity(qm.group(1))

    if ctx.operation in {"cancel", "modify"}:
        om = re.search(r"(?:订单号|order_id)\s*[：:]\s*([A-Za-z0-9\-]+)", norm, re.IGNORECASE)
        if om:
            oid = om.group(1).strip()
            if is_valid_order_id(oid):
                ctx.fields["order_id"] = oid
    if ctx.operation in {"cancel", "modify"}:
        qm = re.search(r"(?:数量|qty)\s*[：:]\s*(\d+)", norm, re.IGNORECASE)
        if qm:
            ctx.fields["quantity"] = normalize_quantity(qm.group(1))
        im = re.search(
            r"(?:商品名称|商品|item_name|item)\s*[：:]\s*(.+?)(?=(?:\s|^)(?:数量|qty|收货地址|收获地址|地址|address|备注|remark|联系电话|联系方式|手机|手机号|电话|phone)\s*[：:]|$)",
            norm,
            re.IGNORECASE,
        )
        if im:
            ctx.fields["item_name"] = im.group(1).strip()
        am = re.search(
            r"(?:收货地址|收获地址|地址|address)\s*[：:]\s*(.+?)(?=(?:\s|^)(?:联系电话|联系方式|手机|手机号|电话|phone|备注|remark)\s*[：:]|$)",
            norm,
            re.IGNORECASE | re.DOTALL,
        )
        if am:
            ctx.fields["address"] = am.group(1).strip().rstrip(" ，,;；")
        pm = re.search(
            r"(?:联系电话|联系方式|手机|手机号|电话|phone)\s*[：:]\s*([+\d\s\-]{1,40})",
            norm,
            re.IGNORECASE,
        )
        if pm:
            d = normalize_digits(pm.group(1))
            if is_valid_phone(d):
                ctx.fields["contact_phone"] = d
        rm = re.search(r"(?:备注|remark)\s*[：:]\s*([^。\n]+)", norm, re.IGNORECASE)
        if rm:
            ctx.fields["remark"] = rm.group(1).strip()


def resolve_order_operation(text: str, hint: str | None, persisted: str | None) -> str | None:
    if hint in ("create", "cancel", "modify"):
        return hint
    if persisted in ("create", "cancel", "modify"):
        return persisted
    norm = normalize_input_text(text).lower()
    if not norm:
        return None
    if re.search(r"(退单|取消订单|撤单|取消)", norm):
        return "cancel"
    if re.search(r"(修改订单|改订单|修改)", norm):
        return "modify"
    if re.search(r"(下单|购买|买)", norm):
        return "create"
    return None


ConfirmReplyKind = Literal["yes", "no", "invalid"]


def classify_pre_confirm_reply(text: str) -> ConfirmReplyKind:
    text_norm = text.strip().lower()
    yes = text_norm in {"确认", "同意", "yes", "y"} or ("确认" in text and "不确认" not in text)
    no = text_norm in {"取消", "不同意", "no", "n"} or ("取消" in text or "不同意" in text)
    if yes:
        return "yes"
    if no:
        return "no"
    return "invalid"
