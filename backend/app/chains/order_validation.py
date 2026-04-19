"""订单校验：稳定错误码、观测块（逻辑实现见 order_validators）。"""

from __future__ import annotations

from typing import Any, Literal

from app.chains.order_validators import (
    CONFIRM_INPUT_INVALID,
    FIELD_KEY_TO_ERROR_CODE,
    INVALID_ORDER_ID_FORMAT,
    INVALID_PHONE,
    MISSING_ADDRESS,
    MISSING_CONTACT_PHONE,
    MISSING_DEPENDENCY_DATA,
    MISSING_DEPENDENCY_ITEMS,
    MISSING_DEPENDENCY_ORDER_IDS,
    MISSING_ITEM_NAME,
    MISSING_OPERATION,
    MISSING_ORDER_ID,
    MISSING_ORDER_OPERATION,
    MISSING_QUANTITY,
    MISSING_REASON,
    MISSING_REQUIRED_FIELDS,
    MODIFY_MISSING_MUTABLE_FIELDS,
    NOT_AWAITING_PRE_CONFIRM,
    ORDER_API_FAILED,
    ORDER_CANCELLED_BY_USER,
    collect_order_validation_codes,
    format_validation_codes,
    missing_keys_to_codes,
    missing_order_field_keys,
    order_form_correction_field_keys,
    primary_order_validation_code,
)

OrderValidationPhase = Literal[
    "route_operation",
    "collect_validate",
    "pre_confirm",
    "execute",
    "dependency_injection",
]


def order_validation_debug_trace(
    *,
    phase: OrderValidationPhase | str,
    codes: list[str],
    missing_field_keys: list[str] | None = None,
    operation: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """写入 AgentResult.debug_trace 的结构化块。"""
    block: dict[str, Any] = {
        "phase": phase,
        "codes": list(codes),
        "primary_code": primary_order_validation_code(codes),
        "missing_field_keys": list(missing_field_keys or []),
        "operation": operation,
    }
    if extra:
        block["extra"] = extra
    return {"order_validation": block}


def merge_debug_trace(
    base: dict[str, Any] | None,
    addition: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """浅合并 debug_trace（订单块与其他观测并存）。"""
    if not base and not addition:
        return None
    out: dict[str, Any] = {}
    if isinstance(base, dict):
        out.update(base)
    if isinstance(addition, dict):
        out.update(addition)
    return out or None
