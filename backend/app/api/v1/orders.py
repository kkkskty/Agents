from fastapi import APIRouter

from app.deps import orchestrator
from app.schemas.orders import (
    OrderConfirmRequest,
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
    )
