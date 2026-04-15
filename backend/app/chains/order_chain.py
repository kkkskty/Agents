import re

from langchain_core.runnables import RunnableLambda

from app.core.state import AgentResult, OrderContext
from app.tools.order_tools import cancel_order, create_order, modify_order


class OrderChain:
    """订单子流程（无 LLM）：1 判动作 2 校验字段 3 缺则追问 4 齐则待确认 5 确认后调 API。"""

    REQUIRED_FIELDS = {
        "create": ["item_name", "quantity", "address", "contact_phone"],
        "cancel": ["order_id", "reason"],
        "modify": ["order_id", "field", "new_value"],
    }

    FIELD_LABEL_ZH = {
        "item_name": "商品名称",
        "quantity": "数量",
        "address": "收货地址",
        "contact_phone": "联系电话",
        "order_id": "订单号",
        "reason": "退单原因",
        "field": "要修改的信息项",
        "new_value": "修改后的内容",
    }

    def process_user_text(
        self, ctx: OrderContext, text: str, operation_hint: str | None = None
    ) -> AgentResult:
        chain = (
            RunnableLambda(
                lambda payload: self._step_route_or_operation(
                    payload["ctx"], payload["text"], payload.get("operation_hint")
                )
            )
            | RunnableLambda(self._step_handle_confirm_stage)
            | RunnableLambda(self._step_block_if_executed)
            | RunnableLambda(self._step_collect_and_validate)
            | RunnableLambda(self._step_prepare_pre_confirm)
        )
        return chain.invoke({"ctx": ctx, "text": text.strip(), "operation_hint": operation_hint})

    def apply_pre_confirm(self, ctx: OrderContext, confirm: bool) -> AgentResult:
        if ctx.status != "awaiting_pre_confirm":
            return AgentResult(
                route="order",
                status=ctx.status,
                message="当前不在执行前确认阶段，请先提供完整订单信息。",
                action_required="provide_order_fields",
            )
        if not confirm:
            ctx.status = "closed"
            return AgentResult(
                route="order", status="closed", message="已取消本次订单操作。"
            )
        return self.execute(ctx)

    def execute(self, ctx: OrderContext) -> AgentResult:
        if ctx.operation is None:
            return AgentResult(
                route="order", status="failed", message="未识别订单操作类型。", error="missing_operation"
            )

        if ctx.operation == "create":
            payload = dict(ctx.fields)
            if ctx.items:
                payload["items"] = list(ctx.items)
            result = create_order(payload)
        elif ctx.operation == "cancel":
            if ctx.cancel_order_ids:
                messages: list[str] = []
                last_link: str | None = None
                all_ok = True
                for oid in ctx.cancel_order_ids:
                    r = cancel_order(
                        {"order_id": str(oid), "reason": ctx.fields.get("reason", "用户申请取消")}
                    )
                    if r.get("ok"):
                        messages.append(str(r.get("message", "")))
                        last_link = r.get("order_link") or last_link
                    else:
                        all_ok = False
                        ctx.failure_reason = r.get("reason", "未知错误")
                        break
                if not all_ok:
                    ctx.status = "failed"
                    return AgentResult(
                        route="order",
                        status=ctx.status,
                        message=f"{self._op_zh(ctx.operation)}失败：{ctx.failure_reason}",
                        error=ctx.failure_reason,
                    )
                ctx.status = "executed_waiting_click"
                ctx.order_link = last_link
                return AgentResult(
                    route="order",
                    status=ctx.status,
                    message=f"已处理 {len(ctx.cancel_order_ids)} 笔订单取消申请。"
                    f" {' '.join(messages[:3])}"
                    f"{' …' if len(messages) > 3 else ''} 请点击链接确认后结束流程。",
                    action_required="click_order_link_confirm",
                    order_link=ctx.order_link,
                )
            result = cancel_order(ctx.fields)
        else:
            result = modify_order(ctx.fields)

        if not result.get("ok"):
            ctx.status = "failed"
            ctx.failure_reason = result.get("reason", "未知错误")
            return AgentResult(
                route="order",
                status=ctx.status,
                message=f"{self._op_zh(ctx.operation)}失败：{ctx.failure_reason}",
                error=ctx.failure_reason,
            )

        ctx.status = "executed_waiting_click"
        ctx.order_link = result.get("order_link")
        return AgentResult(
            route="order",
            status=ctx.status,
            message=f"{result.get('message')} 请点击链接确认后结束流程。",
            action_required="click_order_link_confirm",
            order_link=ctx.order_link,
        )

    def finalize(self, ctx: OrderContext, _clicked: bool) -> AgentResult:
        """执行结果已就绪后收尾：到此结束，不再要求额外交互确认（保留参数以兼容 API）。"""
        if ctx.status != "executed_waiting_click":
            return AgentResult(
                route="order", status=ctx.status, message="当前无可确认的订单流程。"
            )
        ctx.status = "closed"
        return AgentResult(
            route="order",
            status="closed",
            message="操作已完成，对应订单链接如下，请点击查看。",
            order_link=ctx.order_link,
        )

    def _missing_fields(self, ctx: OrderContext) -> list[str]:
        required = list(self.REQUIRED_FIELDS.get(ctx.operation or "", []))
        if ctx.operation == "create" and ctx.items:
            required = [field for field in required if field not in {"item_name", "quantity"}]
        if ctx.operation == "cancel" and ctx.cancel_order_ids:
            # 订单号来自依赖任务，仅需用户补充退单原因
            required = ["reason"]
        return [field for field in required if not str(ctx.fields.get(field) or "").strip()]

    def _parse_supplement_fields(self, ctx: OrderContext, text: str) -> None:
        """解析用户本轮补充的「标签:值」文本写入 ctx.fields（收集阶段）；兼容半角/全角冒号。"""
        norm = text.replace("\n", " ").replace("\t", " ").strip()

        def _digits(s: str) -> str:
            return re.sub(r"\D", "", s)

        if ctx.operation == "create":
            # 先直配地址/电话（不依赖复杂 lookahead，避免「同一行两键值」漏匹配）
            if not str(ctx.fields.get("address") or "").strip():
                am = re.search(
                    r"(?:收货地址|收获地址|地址|address)\s*[：:]\s*(.+?)(?=(?:\s|^)(?:联系电话|联系方式|手机|手机号|电话|phone)\s*[：:]|$)",
                    norm,
                    re.IGNORECASE | re.DOTALL,
                )
                if am:
                    ctx.fields["address"] = am.group(1).strip().rstrip(" ，,;；")
            if not str(ctx.fields.get("contact_phone") or "").strip():
                # 捕获段至少 1 个字符；此前 {4,40} 会导致「124」等 3 位无法匹配
                pm = re.search(
                    r"(?:联系电话|联系方式|手机|手机号|电话|phone)\s*[：:]\s*([+\d\s\-]{1,40})",
                    norm,
                    re.IGNORECASE,
                )
                if pm:
                    d = _digits(pm.group(1))
                    # 至少 3 位数字（兼容短号测试；真实场景可在 API 再校验 11 位手机号）
                    if len(d) >= 3:
                        ctx.fields["contact_phone"] = d
                # 无冒号：「电话138…」「手机 138…」「联系电话是138…」
                if not str(ctx.fields.get("contact_phone") or "").strip():
                    pm2 = re.search(
                        r"(?:联系电话|联系方式|手机|手机号|电话|phone)\s*(?:[是为]?\s*)?([1][\d\s\-]{10,18})",
                        norm,
                        re.IGNORECASE,
                    )
                    if pm2:
                        d2 = _digits(pm2.group(1))
                        if len(d2) >= 11 and d2.startswith("1"):
                            ctx.fields["contact_phone"] = d2[:11]
                        elif len(d2) >= 3:
                            ctx.fields["contact_phone"] = d2
                # 用户只发 11 位号码或带空格分隔的手机号（去掉数字间空格后再匹配）
                if not str(ctx.fields.get("contact_phone") or "").strip():
                    norm_m = re.sub(r"(?<=\d)\s+(?=\d)", "", norm)
                    m3 = re.search(r"(?<!\d)(1[3-9]\d{9})(?!\d)", norm_m)
                    if m3:
                        ctx.fields["contact_phone"] = m3.group(1)
            # 商品名、数量（可选）
            im = re.search(
                r"(?:商品名称|商品|item_name|item)\s*[：:]\s*(.+?)(?=(?:\s|^)(?:数量|qty)\s*[：:]|$)",
                norm,
                re.IGNORECASE,
            )
            if im:
                ctx.fields["item_name"] = im.group(1).strip()
            qm = re.search(r"(?:数量|qty)\s*[：:]\s*(\d+)", norm, re.IGNORECASE)
            if qm:
                ctx.fields["quantity"] = qm.group(1)

        if ctx.operation in {"cancel", "modify"}:
            om = re.search(r"(?:订单号|order_id)\s*[：:]\s*([A-Za-z0-9\-]+)", norm, re.IGNORECASE)
            if om:
                ctx.fields["order_id"] = om.group(1).strip()
        if ctx.operation == "cancel":
            rm = re.search(r"(?:退单原因|原因|reason)\s*[：:]\s*([^。\n]+)", norm, re.IGNORECASE)
            if rm:
                ctx.fields["reason"] = rm.group(1).strip()
        if ctx.operation == "modify":
            fm = re.search(r"(?:字段|field)\s*[：:]\s*([^\s,，。]+)", norm, re.IGNORECASE)
            vm = re.search(r"(?:新值|new_value|值)\s*[：:]\s*([^。]+)", norm, re.IGNORECASE)
            if fm:
                ctx.fields["field"] = fm.group(1).strip()
            if vm:
                ctx.fields["new_value"] = vm.group(1).strip()

    @classmethod
    def resolve_order_operation(
        cls,
        _text: str,
        hint: str | None,
        persisted: str | None,
    ) -> str | None:
        if hint in ("create", "cancel", "modify"):
            return hint
        if persisted in ("create", "cancel", "modify"):
            return persisted
        return None

    def _op_zh(self, op: str | None) -> str:
        mapping = {"create": "下单", "cancel": "退单", "modify": "修改订单"}
        return mapping.get(op or "", "订单操作")

    def _missing_fields_zh(self, missing: list[str]) -> str:
        labels = [self.FIELD_LABEL_ZH.get(f, f) for f in missing]
        return "、".join(labels)

    def _pre_confirm_summary(self, ctx: OrderContext) -> str:
        """基于已收集字段与清单生成只读摘要（步骤 4 展示用，非 LLM）。"""
        parts: list[str] = []
        if ctx.operation == "create" and ctx.items:
            seg = "；".join(
                f"{str(p.get('item_name', '')).strip()} x{p.get('quantity', 1)}"
                for p in ctx.items[:5]
            )
            if len(ctx.items) > 5:
                seg += " …"
            parts.append(f"拟下单清单：{seg}")
        for key in self.REQUIRED_FIELDS.get(ctx.operation or "", []):
            v = ctx.fields.get(key)
            if v:
                label = self.FIELD_LABEL_ZH.get(key, key)
                parts.append(f"{label}：{v}")
        if ctx.operation == "cancel" and ctx.cancel_order_ids:
            parts.append(f"待取消笔数：{len(ctx.cancel_order_ids)}")
        return " ".join(parts) if parts else ""

    # ------- LangChain chain steps（顺序配合 ctx.status；确认步须先于收集以免「确认」被当填表）-------
    def _reset_ctx_for_operation_change(self, ctx: OrderContext, new_op: str) -> None:
        if new_op == "cancel":
            ctx.items = []
            for k in ("item_name", "quantity", "address", "contact_phone"):
                ctx.fields.pop(k, None)
        elif new_op == "create":
            ctx.cancel_order_ids = []
        elif new_op == "modify":
            ctx.items = []

    def _reset_ctx_if_closed_new_intent(
        self, ctx: OrderContext, operation_hint: str | None
    ) -> None:
        """上一轮已 closed 但同会话复用同一 OrderContext 时，清掉旧品项/字段，避免 create→create 不触发换 op 清理。"""
        if ctx.status != "closed":
            return
        if operation_hint not in ("create", "cancel", "modify"):
            return
        ctx.items = []
        ctx.cancel_order_ids = []
        ctx.fields.clear()
        ctx.order_link = None
        ctx.failure_reason = None
        ctx.operation = None
        ctx.status = "collecting_info"

    def _step_route_or_operation(
        self, ctx: OrderContext, text: str, operation_hint: str | None = None
    ) -> dict:
        self._reset_ctx_if_closed_new_intent(ctx, operation_hint)
        # 步骤 1：订单动作仅来自路由注入的 operation_hint 或 ctx 已有 operation
        if operation_hint in ("create", "cancel", "modify"):
            if ctx.operation != operation_hint:
                self._reset_ctx_for_operation_change(ctx, operation_hint)
            ctx.operation = operation_hint
        elif ctx.operation is None:
            ctx.operation = self.resolve_order_operation("", operation_hint, None)
            if ctx.operation is None:
                return {
                    "done": True,
                    "result": AgentResult(
                        route="order",
                        status=ctx.status,
                        message="请说明是下单、退单还是修改订单信息。",
                        action_required="provide_order_operation",
                    ),
                    "ctx": ctx,
                    "text": text,
                }
        return {"done": False, "ctx": ctx, "text": text}

    def _step_handle_confirm_stage(self, payload: dict) -> dict:
        if payload["done"]:
            return payload
        ctx: OrderContext = payload["ctx"]
        text: str = payload["text"]
        if ctx.status != "awaiting_pre_confirm":
            return payload

        text_norm = text.strip().lower()
        yes = text_norm in {"确认", "同意", "yes", "y"} or ("确认" in text and "不确认" not in text)
        no = text_norm in {"取消", "不同意", "no", "n"} or ("取消" in text or "不同意" in text)
        if yes or no:
            return {"done": True, "result": self.apply_pre_confirm(ctx, yes), "ctx": ctx, "text": text}
        return {
            "done": True,
            "result": AgentResult(
                route="order",
                status=ctx.status,
                message="请直接回复“确认”或“取消”，以决定是否执行订单操作。",
                action_required="confirm_before_execute",
            ),
            "ctx": ctx,
            "text": text,
        }

    def _step_block_if_executed(self, payload: dict) -> dict:
        if payload["done"]:
            return payload
        ctx: OrderContext = payload["ctx"]
        if ctx.status != "executed_waiting_click":
            return payload
        return {
            "done": True,
            "result": AgentResult(
                route="order",
                status=ctx.status,
                message="订单处理已完成，请点击订单链接确认后结束流程。",
                action_required="click_order_link_confirm",
                order_link=ctx.order_link,
            ),
            "ctx": ctx,
            "text": payload["text"],
        }

    def _step_collect_and_validate(self, payload: dict) -> dict:
        if payload["done"]:
            return payload
        ctx: OrderContext = payload["ctx"]
        # 步骤 2–3：合并用户本轮补充的字段，再校验是否齐全
        self._parse_supplement_fields(ctx, payload["text"])
        missing = self._missing_fields(ctx)
        if not missing:
            return payload
        ctx.status = "collecting_info"
        return {
            "done": True,
            "result": AgentResult(
                route="order",
                status="collecting_info",
                message=f"为继续{self._op_zh(ctx.operation)}，请补充以下信息：{self._missing_fields_zh(missing)}。",
                action_required="provide_order_fields",
            ),
            "ctx": ctx,
            "text": payload["text"],
        }

    def _step_prepare_pre_confirm(self, payload: dict) -> AgentResult:
        if payload["done"]:
            return payload["result"]
        ctx: OrderContext = payload["ctx"]
        # 步骤 4：信息已齐，展示摘要并进入待确认（步骤 5 在 _step_handle_confirm_stage）
        ctx.status = "awaiting_pre_confirm"
        item_hint = f"（共{len(ctx.items)}个商品）" if ctx.operation == "create" and ctx.items else ""
        summary = self._pre_confirm_summary(ctx)
        summary_block = f"\n{summary}" if summary else ""
        return AgentResult(
            route="order",
            status=ctx.status,
            message=(
                f"已收集必要信息{item_hint}。{summary_block}\n"
                f"请确认是否执行{self._op_zh(ctx.operation)}？回复“确认”继续，回复“取消”终止。"
            ),
            action_required="confirm_before_execute",
        )
