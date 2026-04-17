from typing import Any

from app.schemas.chat import IntentType
from app.core.llm_provider import LLMRouter
from app.core.settings import load_settings
from app.core.state import (
    HandoffTask,
    OrderOperation,
    OrderTask,
    QueryTask,
    RuleTask,
    Task,
    UnknownTask,
)


class IntentRouterAgent:
    def __init__(self) -> None:
        self.settings = load_settings()
        self.llm = LLMRouter(self.settings.intent_agent_llm)

    def analyze(self, text: str) -> tuple[IntentType, list[Task]]:
        prompt = (
            "你是客服系统任务分析器（Task Planner），你的任务是对用户输入进行路由判定与任务拆解，并严格输出 JSON，不要输出任何额外内容。\n"
            "输出 JSON 格式必须为 \n"
            '{"tasks":[{"text":"子任务描述","intent":"query|rule|order|handoff|unknown","depends_on":["task_0"],"order_operation":"create|cancel|modify"}]}\n'
            "其中intent用于意图分类，分类规则如下：\n"
            "query表示信息查询、状态查询或列表查询，rule表示规则说明、政策解释或处理指引，order表示订单相关操作包括下单、退单、退款或修改订单，handoff表示转人工，unknown仅在无法理解用户语义时使用；\n"
            "要求：\n"
            "1.一次同时完成\"子任务拆分 + 意图判定\"，"
            "2.tasks必须为非空数组，用于表示拆解后的子任务，每个task必须包含text、intent字段；可选task_id（从task_0起递增且唯一），"
            "task_id必须从task_0开始递增且唯一，不允许出现复杂类型或未定义类型；\n"
            "3.当intent为order时,必填order_operation字段，其中create表示下单或再下单，cancel表示取消或退单，modify表示修改订单；\n"
            "4.如果任务存在“先查询再操作”的情况必须拆分为多个task，查询任务在前，操作任务在后，后续任务的depends_on必须引用前序task_id形成依赖关系；所有任务必须是原子级执行单元，不允许一个task包含多个动作；\n"
            "6.禁止输出任何非JSON内容，禁止输出complex、route、confidence字段或任何扩展字段，禁止随意编造不存在的task_id或依赖关系，所有depends_on必须严格引用已生成的task_id；\n"
            "示例：\n"
            "\"我的订单有哪些\" → {\"tasks\":[{\"text\":\"查询我的订单列表\",\"intent\":\"query\",\"depends_on\":[]}]}\n"
            "\"查询我的订单，看下那些降价了，帮我重新下单一份？\" → {\"tasks\":[{\"text\":\"查询我的订单中哪些商品已降价\",\"intent\":\"query\",\"depends_on\":[]},{\"text\":\"基于查询到的降价商品重新下单\",\"intent\":\"order\",\"depends_on\":[\"task_0\"],\"order_operation\":\"create\"}]}\n"
            # "\"查询我的订单里买过的东西多少钱\" → {\"tasks\":[{\"text\":\"查询用户订单及订单明细中的商品名称与成交单价\",\"intent\":\"query\",\"depends_on\":[]}]}\n"
            # "\"充值失败怎么办\" → {\"tasks\":[{\"text\":\"充值失败的处理规则与指引\",\"intent\":\"rule\",\"depends_on\":[]}]}\n"
            # "\"如何退款/怎么联系客服\" → 偏规则说明、操作指引用 rule，不要用 query 查库。\n"
            f"用户输入：{text}"
        )
        # try:
        #     data = self.llm.invoke_json(prompt)
        # except ValueError:
        #     fb = self._fallback_tasks_if_json_failed(text)
        #     if fb is not None:
        #         return self._derive_session_route(fb), fb  # type: ignore[return-value]
        #     raise
        data = self.llm.invoke_json(prompt)
        if not isinstance(data, dict):
            raise ValueError("intent_router analyze failed: root must be a JSON object")
        raw_tasks = data.get("tasks", [])
        tasks = self._validate_and_build_tasks(raw_tasks)

        if not tasks:
            raise ValueError("intent_router analyze failed: empty tasks from llm")

        derived_route = self._derive_session_route(tasks)
        route: IntentType = derived_route  # type: ignore[assignment] 意图路由
        return route, tasks

    # def _fallback_tasks_if_json_failed(self, text: str) -> list[Task] | None:
    #     """意图模型返回非合法 JSON 时，对常见句式做固定拆分，避免整请求失败。"""
    #     t = text.strip()
    #     wants_unpaid = any(
    #         k in t for k in ("未支付", "没支付", "未付款", "没付款", "待支付", "待付款")
    #     )
    #     wants_cancel = any(k in t for k in ("取消", "退单", "退掉", "作废", "全部取消", "批量取消"))
    #     if wants_unpaid and wants_cancel:
    #         return [
    #             QueryTask(
    #                 id="task_0",
    #                 text="查询当前用户未支付订单",
    #                 intent="query",
    #                 depends_on=[],
    #             ),
    #             OrderTask(
    #                 id="task_1",
    #                 text="取消查询到的未支付订单",
    #                 intent="order",
    #                 depends_on=["task_0"],
    #                 order_operation_hint="cancel",
    #             ),
    #         ]
    #     # 历史订单 / 买过什么 / 单价与金额（意图 JSON 损坏时的单查询兜底）
    #     order_and_price = any(
    #         k in t for k in ("订单", "买过", "购买", "下单", "买过的")
    #     ) and any(k in t for k in ("钱", "价格", "多少", "单价", "元", "金额"))
    #     if order_and_price:
    #         return [
    #             QueryTask(
    #                 id="task_0",
    #                 text="查询当前用户订单及订单明细中的商品与成交单价金额",
    #                 intent="query",
    #                 depends_on=[],
    #             ),
    #         ]
    #     return None

    def _derive_session_route(self, tasks: list[Task]) -> str:
        """多任务时取第一个非 unknown 的 intent 作为会话级 route。"""
        if not tasks:
            return "unknown"
        if len(tasks) == 1:
            return tasks[0].intent
        for t in tasks:
            if t.intent != "unknown":
                return t.intent
        return "unknown"

    def _normalize_intent(self, value: Any) -> str:
        if not isinstance(value, str):
            return "unknown"
        intent = value.strip().lower()
        if intent == "complex":
            return "unknown"
        if intent in {"query", "rule", "order", "handoff", "unknown"}:
            return intent
        return "unknown"

    def _normalize_task_text(self, value: Any) -> str:
        if value is None:
            return ""
        text = str(value).strip()
        return text[:500]

    def _normalize_order_operation(self, value: Any) -> OrderOperation | None:
        if not isinstance(value, str):
            return None
        v = value.strip().lower()
        if v in ("create", "cancel", "modify"):
            return v  # type: ignore[return-value]
        return None

    def _normalize_depends_on(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        out: list[str] = []
        for item in value:
            v = str(item).strip()
            if not v.startswith("task_"):
                continue
            if v not in out:
                out.append(v)
        return out

    def _canonical_task_id(self, index: int) -> str:
        return f"task_{index}"

    def _validate_and_build_tasks(self, raw_tasks: Any) -> list[Task]:
        if not isinstance(raw_tasks, list):
            return []
        result: list[Task] = []
        for item in raw_tasks:
            if not isinstance(item, dict):
                continue
            raw_text = item.get("text", item.get("task", item.get("question", item.get("query", ""))))
            text = self._normalize_task_text(raw_text)
            if not text:
                continue
            i = len(result)
            tid = self._canonical_task_id(i)

            intent = self._normalize_intent(item.get("intent", "unknown"))
            depends_raw = item.get("depends_on", item.get("dependsOn", []))
            depends_on = self._normalize_depends_on(depends_raw)
            allowed = {self._canonical_task_id(j) for j in range(i)}
            depends_on = [d for d in depends_on if d in allowed]

            op_hint = self._normalize_order_operation(
                item.get("order_operation") or item.get("orderOperation")
            )
            # if intent == "order":
            #     if op_hint is None:
            #         op_hint = "create"
            #     # 模型常漏传 order_operation；若子任务明显是退单而非下单，勿保持默认 create
            #     if op_hint == "create" and any(
            #         k in text for k in ("取消", "退单", "退掉", "作废", "全部取消", "批量取消")
            #     ) and not any(k in text for k in ("重新下单", "再下一单", "重新订购", "再买", "下单")):
            #         op_hint = "cancel"

            if intent == "query":
                result.append(QueryTask(id=tid, text=text, depends_on=depends_on))
            elif intent == "rule":
                result.append(RuleTask(id=tid, text=text, depends_on=depends_on))
            elif intent == "order":
                result.append(
                    OrderTask(
                        id=tid,
                        text=text,
                        depends_on=depends_on,
                        order_operation_hint=op_hint,
                    )
                )
            elif intent == "handoff":
                result.append(HandoffTask(id=tid, text=text, depends_on=depends_on))
            elif intent == "session_meta":
                result.append(UnknownTask(id=tid, text=text, depends_on=depends_on))
            else:
                result.append(UnknownTask(id=tid, text=text, depends_on=depends_on))
        return result

# ##测试代码
#     def decompose_sub_tasks(self, text: str) -> list[Task]:
#         _, tasks = self.analyze(text)
#         return tasks
