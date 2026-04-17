from pydantic import BaseModel, Field


class OrderConfirmRequest(BaseModel):
    session_id: str
    user_id: str
    confirm: bool = Field(..., description="User pre-confirm before execution")


class OrderFinalizeRequest(BaseModel):
    session_id: str
    user_id: str
    click_confirmed: bool = Field(
        ..., description="Whether user clicked order/after-sales confirmation link"
    )


class SimpleOrderResponse(BaseModel):
    session_id: str
    status: str
    message: str
    order_link: str | None = None
    error: str | None = None
