from app.chains.order_chain import OrderChain
from app.core.state import AgentResult, OrderContext


class OrderAgent:
    def __init__(self) -> None:
        self._chain = OrderChain()

    def handle_message(
        self, ctx: OrderContext, text: str, operation_hint: str | None = None
    ) -> AgentResult:
        return self._chain.process_user_text(ctx, text, operation_hint=operation_hint)

    def handle_confirm(self, ctx: OrderContext, confirm: bool) -> AgentResult:
        # 必须直接走确认逻辑：process_user_text("取消") 会把「取消」误解析为退单意图（resolve_order_operation）
        return self._chain.apply_pre_confirm(ctx, confirm)

    def finalize(self, ctx: OrderContext, click_confirmed: bool) -> AgentResult:
        return self._chain.finalize(ctx, click_confirmed)

    def handle_with_state(
        self, state, ctx: OrderContext, text: str, operation_hint: str | None = None
    ) -> dict:
        result = self.handle_message(ctx, text, operation_hint=operation_hint)
        runtime = state["runtime"]
        return {"runtime": {**runtime, "raw": result}}
