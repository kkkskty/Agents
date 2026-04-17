from fastapi import APIRouter

from app.deps import orchestrator
from app.schemas.orders import (
    OrderCancelFlowRequest,
    OrderConfirmRequest,
    OrderFillFieldsRequest,
    OrderFinalizeRequest,
    SimpleOrderResponse,
)

router = APIRouter(prefix="/orders", tags=["orders"])


@router.post("/confirm", response_model=SimpleOrderResponse)
def confirm_order(req: OrderConfirmRequest) -> SimpleOrderResponse:
    result = orchestrator.order_confirm(
        session_id=req.session_id, user_id=req.user_id, confirm=req.confirm
    )
    return SimpleOrderResponse(
        session_id=req.session_id,
        status=result.status,
        message=result.message,
        order_link=result.order_link,
        error=result.error,
        action_required=result.action_required,
        pending_actions=result.pending_actions,
    )

@router.post("/finalize", response_model=SimpleOrderResponse)
def finalize_order(req: OrderFinalizeRequest) -> SimpleOrderResponse:
    result = orchestrator.order_finalize(
        session_id=req.session_id, user_id=req.user_id, clicked=req.click_confirmed
    )
    return SimpleOrderResponse(
        session_id=req.session_id,
        status=result.status,
        message=result.message,
        order_link=result.order_link,
        error=result.error,
        action_required=result.action_required,
        pending_actions=result.pending_actions,
    )


@router.post("/fill-fields", response_model=SimpleOrderResponse)
def fill_order_fields(req: OrderFillFieldsRequest) -> SimpleOrderResponse:
    result = orchestrator.order_fill_fields(
        session_id=req.session_id,
        user_id=req.user_id,
        fields=req.fields,
        items=req.items,
    )
    return SimpleOrderResponse(
        session_id=req.session_id,
        status=result.status,
        message=result.message,
        order_link=result.order_link,
        error=result.error,
        action_required=result.action_required,
        pending_actions=result.pending_actions,
    )


@router.post("/cancel-flow", response_model=SimpleOrderResponse)
def cancel_order_flow(req: OrderCancelFlowRequest) -> SimpleOrderResponse:
    result = orchestrator.order_cancel_flow(
        session_id=req.session_id,
        user_id=req.user_id,
    )
    return SimpleOrderResponse(
        session_id=req.session_id,
        status=result.status,
        message=result.message,
        order_link=result.order_link,
        error=result.error,
        action_required=result.action_required,
        pending_actions=result.pending_actions,
    )
