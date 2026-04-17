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


class OrderFillFieldsRequest(BaseModel):
    session_id: str
    user_id: str
    fields: dict[str, str] = Field(default_factory=dict, description="Structured order fields from form")
    items: list[dict[str, str]] = Field(
        default_factory=list,
        description="Editable item list from form, e.g. [{item_name, quantity}]",
    )


class OrderCancelFlowRequest(BaseModel):
    session_id: str
    user_id: str


class SimpleOrderResponse(BaseModel):
    session_id: str
    status: str
    message: str
    order_link: str | None = None
    error: str | None = None
    action_required: str | None = None
    pending_actions: list[dict] | None = None
