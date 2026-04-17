from langchain_core.runnables import RunnableLambda

from app.chains.order_field_config import (
    FIELD_LABEL_ZH as ORDER_FIELD_LABEL_ZH,
    MUTABLE_FIELDS_MODIFY,
    display_fields_for,
    required_fields_for as cfg_required_fields,
)
from app.chains.order_validation import (
    MISSING_OPERATION,
    MISSING_ORDER_OPERATION,
    ORDER_API_FAILED,
    ORDER_CANCELLED_BY_USER,
    order_validation_debug_trace,
)
from app.chains import order_validators as ord_val
from app.core.state import AgentResult, OrderContext
from app.tools.order_tools import cancel_order, create_order, modify_order


class OrderChain:
    """订单子流程（无 LLM）：路由动作 → 确认/拦截 → 收集校验 → 待确认摘要 → 执行 API。"""

    MODIFY_MUTABLE_FIELDS = MUTABLE_FIELDS_MODIFY
    FIELD_LABEL_ZH = ORDER_FIELD_LABEL_ZH

    # -------------------------------------------------------------------------
    # 对外入口
    # -------------------------------------------------------------------------

    @classmethod
    def required_fields_for(cls, operation: str | None) -> list[str]:
        return cfg_required_fields(operation)

    @classmethod
    def resolve_order_operation(
        cls,
        text: str,
        hint: str | None,
        persisted: str | None,
    ) -> str | None:
        return ord_val.resolve_order_operation(text, hint, persisted)

    def process_user_text(
        self, ctx: OrderContext, text: str, operation_hint: str | None = None
    ) -> AgentResult:
        chain = (
            RunnableLambda(
                lambda p: self._step_route_or_operation(
                    p["ctx"], p["text"], p.get("operation_hint")
                )
            )
            | RunnableLambda(self._step_handle_confirm_stage)
            | RunnableLambda(self._step_block_after_closed)
            | RunnableLambda(self._step_collect_and_validate)
            | RunnableLambda(self._step_prepare_pre_confirm)
        )
        return chain.invoke({"ctx": ctx, "text": text.strip(), "operation_hint": operation_hint})

    # -------------------------------------------------------------------------
    # Runnable 链路（顺序固定：确认须先于收集，避免「确认」被当填表）
    # -------------------------------------------------------------------------

    def _step_route_or_operation(
        self, ctx: OrderContext, text: str, operation_hint: str | None = None
    ) -> dict:
        self._reset_ctx_if_closed_new_intent(ctx, operation_hint)
        if operation_hint in ("create", "cancel", "modify"):
            if ctx.operation != operation_hint:
                self._reset_ctx_for_operation_change(ctx, operation_hint)
            ctx.operation = operation_hint
        elif ctx.operation is None:
            ctx.operation = self.resolve_order_operation(text, operation_hint, None)
            if ctx.operation is None:
                return {
                    "done": True,
                    "result": AgentResult(
                        route="order",
                        status=ctx.status,
                        message="请说明是下单、退单还是修改订单信息。",
                        action_required="provide_order_operation",
                        error=MISSING_ORDER_OPERATION,
                        debug_trace=order_validation_debug_trace(
                            phase="route_operation",
                            codes=[MISSING_ORDER_OPERATION],
                            operation=None,
                        ),
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

        kind = ord_val.classify_pre_confirm_reply(text)
        if kind == "yes":
            return {"done": True, "result": self.apply_pre_confirm(ctx, True), "ctx": ctx, "text": text}
        if kind == "no":
            return {"done": True, "result": self.apply_pre_confirm(ctx, False), "ctx": ctx, "text": text}
        return {
            "done": True,
            "result": AgentResult(
                route="order",
                status=ctx.status,
                message="请直接回复“确认”或“取消”，以决定是否执行订单操作。",
                action_required="confirm_before_execute",
                error=ord_val.CONFIRM_INPUT_INVALID,
                debug_trace=order_validation_debug_trace(
                    phase="pre_confirm",
                    codes=[ord_val.CONFIRM_INPUT_INVALID],
                    operation=ctx.operation,
                ),
            ),
            "ctx": ctx,
            "text": text,
        }

    def _step_block_after_closed(self, payload: dict) -> dict:
        """流程已 closed 时短路，避免继续收集。"""
        if payload["done"]:
            return payload
        ctx: OrderContext = payload["ctx"]
        if ctx.status != "closed":
            return payload
        return {
            "done": True,
            "result": AgentResult(
                route="order",
                status=ctx.status,
                message="订单流程已完成。若需继续，请发起新的订单请求。",
                order_link=ctx.order_link,
            ),
            "ctx": ctx,
            "text": payload["text"],
        }

    def _step_collect_and_validate(self, payload: dict) -> dict:
        if payload["done"]:
            return payload
        ctx: OrderContext = payload["ctx"]
        ord_val.parse_collect_order_fields(ctx, payload["text"])
        codes = ord_val.collect_order_validation_codes(ctx)
        if not codes:
            return payload
        ctx.status = "collecting_info"
        missing = ord_val.missing_order_field_keys(ctx)
        return {
            "done": True,
            "result": AgentResult(
                route="order",
                status="collecting_info",
                message=ord_val.collect_validate_user_message(ctx),
                action_required="provide_order_fields",
                error=ord_val.primary_order_validation_code(codes),
                debug_trace=order_validation_debug_trace(
                    phase="collect_validate",
                    codes=codes,
                    missing_field_keys=missing,
                    operation=ctx.operation,
                ),
            ),
            "ctx": ctx,
            "text": payload["text"],
        }

    def _step_prepare_pre_confirm(self, payload: dict) -> AgentResult:
        if payload["done"]:
            return payload["result"]
        ctx: OrderContext = payload["ctx"]
        ctx.status = "awaiting_pre_confirm"
        summary = self._pre_confirm_summary(ctx)
        summary_block = f"{summary}\n\n" if summary.strip() else ""
        return AgentResult(
            route="order",
            status=ctx.status,
            message=(
                "已收集必要信息。\n\n"
                "【待确认的订单信息】\n"
                f"{summary_block}"
                f"请确认是否执行{self._op_zh(ctx.operation)}？回复「确认」继续，回复「取消」终止。"
            ),
            action_required="confirm_before_execute",
        )

    # -------------------------------------------------------------------------
    # 链路内调用的业务动作（确认 / 调 API / 收尾）
    # -------------------------------------------------------------------------

    def apply_pre_confirm(self, ctx: OrderContext, confirm: bool) -> AgentResult:
        if ctx.status != "awaiting_pre_confirm":
            return AgentResult(
                route="order",
                status=ctx.status,
                message="当前不在执行前确认阶段，请先提供完整订单信息。",
                action_required="provide_order_fields",
                error=ord_val.NOT_AWAITING_PRE_CONFIRM,
                debug_trace=order_validation_debug_trace(
                    phase="pre_confirm",
                    codes=[ord_val.NOT_AWAITING_PRE_CONFIRM],
                    operation=ctx.operation,
                ),
            )
        if not confirm:
            ctx.status = "closed"
            return AgentResult(
                route="order",
                status="closed",
                message="已取消本次订单操作。",
                debug_trace=order_validation_debug_trace(
                    phase="pre_confirm",
                    codes=[ORDER_CANCELLED_BY_USER],
                    operation=ctx.operation,
                ),
            )
        return self.execute(ctx)

    def execute(self, ctx: OrderContext) -> AgentResult:
        if ctx.operation is None:
            return AgentResult(
                route="order",
                status="failed",
                message="未识别订单操作类型。",
                error=MISSING_OPERATION,
                debug_trace=order_validation_debug_trace(
                    phase="execute",
                    codes=[MISSING_OPERATION],
                    operation=None,
                ),
            )

        if ctx.operation == "create":
            payload = dict(ctx.fields)
            if ctx.items:
                payload["items"] = list(ctx.items)
            result = create_order(payload)
        elif ctx.operation == "cancel":
            cancel_reason = (
                str(ctx.fields.get("remark") or "").strip()
                or str(ctx.fields.get("reason") or "").strip()
                or "用户申请取消"
            )
            cancel_extra = {
                "item_name": str(ctx.fields.get("item_name") or "").strip(),
                "quantity": str(ctx.fields.get("quantity") or "").strip(),
                "address": str(ctx.fields.get("address") or "").strip(),
                "contact_phone": str(ctx.fields.get("contact_phone") or "").strip(),
                "remark": str(ctx.fields.get("remark") or "").strip(),
            }
            if ctx.cancel_order_ids:
                messages: list[str] = []
                last_link: str | None = None
                all_ok = True
                for oid in ctx.cancel_order_ids:
                    payload = {"order_id": str(oid), "reason": cancel_reason}
                    payload.update({k: v for k, v in cancel_extra.items() if v})
                    r = cancel_order(payload)
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
                        error=ORDER_API_FAILED,
                        debug_trace=order_validation_debug_trace(
                            phase="execute",
                            codes=[ORDER_API_FAILED],
                            operation=ctx.operation,
                            extra={"upstream_reason": ctx.failure_reason},
                        ),
                    )
                ctx.status = "closed"
                ctx.order_link = last_link
                return AgentResult(
                    route="order",
                    status=ctx.status,
                    message=f"已处理 {len(ctx.cancel_order_ids)} 笔订单取消申请。"
                    f" {' '.join(messages[:3])}"
                    f"{' …' if len(messages) > 3 else ''} 请点击链接查看订单状态。",
                    order_link=ctx.order_link,
                )
            cancel_payload = dict(ctx.fields)
            if not str(cancel_payload.get("reason") or "").strip():
                cancel_payload["reason"] = cancel_reason
            for k, v in cancel_extra.items():
                if v and not str(cancel_payload.get(k) or "").strip():
                    cancel_payload[k] = v
            result = cancel_order(cancel_payload)
        else:
            result = modify_order(ctx.fields)

        if not result.get("ok"):
            ctx.status = "failed"
            ctx.failure_reason = result.get("reason", "未知错误")
            return AgentResult(
                route="order",
                status=ctx.status,
                message=f"{self._op_zh(ctx.operation)}失败：{ctx.failure_reason}",
                error=ORDER_API_FAILED,
                debug_trace=order_validation_debug_trace(
                    phase="execute",
                    codes=[ORDER_API_FAILED],
                    operation=ctx.operation,
                    extra={"upstream_reason": ctx.failure_reason},
                ),
            )

        ctx.status = "closed"
        ctx.order_link = result.get("order_link")
        return AgentResult(
            route="order",
            status=ctx.status,
            message=f"{result.get('message')} 请点击链接查看订单状态。",
            order_link=ctx.order_link,
        )

    def finalize(self, ctx: OrderContext, _clicked: bool) -> AgentResult:
        if ctx.status == "closed":
            return AgentResult(
                route="order", status=ctx.status, message="订单流程已完成。"
            )
        return AgentResult(
            route="order",
            status="closed",
            message="操作已完成，对应订单链接如下，请点击查看。",
            order_link=ctx.order_link,
        )

    # -------------------------------------------------------------------------
    # 上下文切换（仅供路由步骤）
    # -------------------------------------------------------------------------

    def _reset_ctx_for_operation_change(self, ctx: OrderContext, new_op: str) -> None:
        if new_op == "cancel":
            ctx.items = []
            for k in ("item_name", "quantity", "address", "contact_phone"):
                ctx.fields.pop(k, None)
        elif new_op == "create":
            ctx.cancel_order_ids = []

    def _reset_ctx_if_closed_new_intent(
        self, ctx: OrderContext, operation_hint: str | None
    ) -> None:
        """closed 后会话复用同一 OrderContext 时清空旧数据，避免连续下单不清理。"""
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

    # -------------------------------------------------------------------------
    # 文案
    # -------------------------------------------------------------------------

    def _op_zh(self, op: str | None) -> str:
        return {"create": "下单", "cancel": "退单", "modify": "修改订单"}.get(op or "", "订单操作")

    def _pre_confirm_summary(self, ctx: OrderContext) -> str:
        """待确认摘要：表单式排版；多订单时每订单一组。"""
        op = ctx.operation
        if op not in ("create", "cancel", "modify"):
            return ""

        _CREATE = "_create_"
        _BARE = "_bare_"
        sections: list[str] = []
        items = list(ctx.items or [])
        skip_single_item_fields = bool(items and len(items) >= 1)

        group_map: dict[str, list[dict]] = {}
        if op == "create":
            if items:
                group_map[_CREATE] = list(items)
        elif items:
            bare: list[dict] = []
            for it in items:
                oid = it.get("order_id")
                if oid is not None and str(oid).strip():
                    k = str(oid).strip()
                    group_map.setdefault(k, []).append(it)
                else:
                    bare.append(it)
            if bare:
                anchor: str | None = None
                if ctx.cancel_order_ids and len(ctx.cancel_order_ids) == 1:
                    anchor = str(ctx.cancel_order_ids[0])
                elif ord_val.normalize_field_value(ctx.fields.get("order_id")):
                    anchor = ord_val.normalize_field_value(ctx.fields.get("order_id"))
                if anchor:
                    group_map.setdefault(anchor, []).extend(bare)
                else:
                    group_map[_BARE] = bare

        def _product_lines(chunk: list[dict]) -> list[str]:
            lines: list[str] = []
            for it in chunk[:40]:
                nm = str(it.get("item_name") or "").strip() or "（未命名商品）"
                qty = str(it.get("quantity") or "").strip() or "1"
                pid = it.get("product_id")
                row = f"  · {nm}  ×{qty}"
                if pid is not None and str(pid).strip():
                    row += f"  （商品编号 {pid}）"
                lines.append(row)
            if len(chunk) > 40:
                lines.append(f"  · …（另有 {len(chunk) - 40} 个商品未展示）")
            return lines

        def _group_sort_keys() -> list[str]:
            if not group_map:
                return []
            if op == "create":
                return [_CREATE] if _CREATE in group_map else []
            keys = list(group_map.keys())
            if op == "cancel" and ctx.cancel_order_ids:
                out: list[str] = []
                seen: set[str] = set()
                for x in ctx.cancel_order_ids:
                    s = str(x)
                    if s in group_map and s not in seen:
                        out.append(s)
                        seen.add(s)
                for k in sorted(keys):
                    if k not in seen and k not in (_BARE,):
                        out.append(k)
                        seen.add(k)
                if _BARE in group_map:
                    out.append(_BARE)
                return out
            fo = ord_val.normalize_field_value(ctx.fields.get("order_id"))
            if fo and fo in group_map:
                rest = sorted(k for k in keys if k != fo)
                return [fo] + rest
            return sorted(keys)

        for gk in _group_sort_keys():
            chunk = group_map.get(gk) or []
            if not chunk:
                continue
            if gk == _CREATE:
                title = "【拟下单 · 商品】"
            elif gk == _BARE:
                title = "【商品明细 · 未关联订单号】"
            else:
                title = f"【订单 {gk} · 商品】"
            sections.append(title + "\n" + "\n".join(_product_lines(chunk)))

        if op == "cancel" and ctx.cancel_order_ids and not items:
            ids_txt = "、".join(str(x) for x in ctx.cancel_order_ids[:40])
            suf = f"\n（共 {len(ctx.cancel_order_ids)} 笔）" if len(ctx.cancel_order_ids) > 1 else ""
            sections.append(f"【待取消订单】\n订单号：{ids_txt}{suf}")

        if op == "cancel" and ctx.cancel_order_ids and items:
            ids_from_items: set[str] = set()
            for it in items:
                o = it.get("order_id")
                if o is not None and str(o).strip():
                    ids_from_items.add(str(o).strip())
            orphans = [str(x) for x in ctx.cancel_order_ids if str(x) not in ids_from_items]
            if orphans and ids_from_items:
                sections.append(
                    "【待取消 · 暂无商品明细行的订单】\n订单号：" + "、".join(orphans)
                )

        skip_dup_order_id = bool(op == "cancel" and ctx.cancel_order_ids)
        field_lines: list[str] = []
        for key in display_fields_for(op):
            if skip_dup_order_id and key == "order_id":
                continue
            if skip_single_item_fields and key in {"item_name", "quantity"}:
                continue
            val = ord_val.normalize_field_value(ctx.fields.get(key))
            if not val:
                continue
            label = self.FIELD_LABEL_ZH.get(key, key)
            field_lines.append(f"{label}：{val}")

        if field_lines:
            block = "\n".join(field_lines)
            sections.append(("【共用 / 表单信息】\n" + block) if sections else block)

        return "\n\n".join(sections).strip()
