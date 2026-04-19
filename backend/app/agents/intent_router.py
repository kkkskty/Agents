from __future__ import annotations

import json
import re
from textwrap import dedent
from typing import Any

from app.schemas.chat import IntentType
from app.core.llm_provider import LLMRouter
from app.core.settings import load_settings
from app.core.session_store import SessionStore
from app.core.step_dag import detect_cycle_same_turn, global_step_id, validate_same_turn_refs
from app.core.state import (
    HandoffTask,
    OrderOperation,
    OrderTask,
    QueryTask,
    RuleTask,
    SessionMetaTask,
    StepRef,
    Task,
    UnknownTask,
)

_TASK_LOCAL_RE = re.compile(r"^task_(\d+)$")

# 仅含「固定系统说明 + 结尾引导语」；运行时在末尾拼接 build_context_for_router 产出的块。
_INTENT_ROUTER_PROMPT_PREFIX = dedent(
    """
    你是客服系统任务分析器（Task Planner）。请结合用户输入与会话历史做意图重写、路由判定与子任务拆解；只输出 JSON，不要输出任何其它内容。

    ## 输出 JSON 结构
    {"tasks":[{
      "text":"子任务描述",
      "intent":"query|rule|order|handoff|unknown|session_meta",
      "depends_on":[依赖项],
      "order_operation":"create|cancel|modify"
    }]}

    ## depends_on（统一 StepRef）
    - 数组元素支持两种形态：
      1）**本轮内依赖**：字符串 `"task_k"`（仅允许引用**已出现在此前 tasks 的下标**，即当前任务之前的步骤）。不要写完整 `uuid:task_k` 串；若误写也会被服务端归一化。
      2）**跨轮依赖**：对象 `{"turn_id":"<会话轮次uuid>","step_id":"<全局步骤id>"}`；其中 `step_id` 必须为全局唯一形式（见下文「历史步骤清单」）。也可用字符串 `"<历史turn_uuid>:task_k"` 表示依赖该轮的第 k 步。
    - 禁止依赖尚未声明的步骤；禁止环依赖。
    - 若无需依赖则 `depends_on`: []。

    ## intent 定义
    - query：业务数据查询（订单、商品、库存、价格等），走 Text-to-SQL；不得用于「会话本身」类问题。
    - rule：规则说明、政策解释或操作指引（不查业务数据）。
    - order：订单相关操作（下单、取消、修改等）。
    - handoff：转人工。
    - session_meta：询问会话本身——例如「我刚才说了什么」「重复上一句」「总结我们聊了什么」；答案来自对话历史与摘要，禁止用 query 查库。
    - unknown：仅在无法理解用户语义时使用。

    ## 会话类禁止查库
    凡是回顾上文、复述用户原话、要对话摘要的请求，必须使用 session_meta；禁止输出 query（不得编造「查询会话记录 / 操作历史」等子任务）。

    ## 关键规则
    1）信息不足不要随意推断下单：用户提到「下单/购买」但未给出明确商品、且上下文无法唯一确定商品时，不得直接生成 order；优先 query（商品/意图）或 unknown。
       示例 — "帮我下一单" → {"tasks":[{"text":"用户未提供下单商品信息，需确认具体商品","intent":"unknown","depends_on":[]}]}
    2）order 必须满足：已有明确商品，或有明确来源（如「刚才那个」「订单里的某商品」）；否则禁止 order。
    3）查询 + 操作同时出现时：拆成 query → order，且 order 的 depends_on 必须包含指向该 query 的本轮字符串依赖 `"task_0"` 等形式。
    4）禁止虚构「确认信息」类子任务。
    5）上下文可指代历史步骤时：在 depends_on 中加入对象引用历史 step（来自下方清单）。
    6）每个 task 原子化，禁止一个 task 内多个动作。
    7）tasks 必须为非空数组。
    8）禁止输出任何非 JSON 内容（不要 complex、route、confidence 等扩展字段）。

    ## 示例（用户话 → tasks 片段）
    "我的订单有哪些" →
    {"tasks":[{"text":"查询我的订单列表","intent":"query","depends_on":[]}]}

    "查询我的订单，看下哪些降价了，帮我重新下单一份" →
    {"tasks":[
      {"text":"查询我的订单中哪些商品已降价","intent":"query","depends_on":[]},
      {"text":"基于降价商品重新下单","intent":"order","depends_on":["task_0"],"order_operation":"create"}
    ]}

    "帮我买一个无线耳机" →
    {"tasks":[
      {"text":"查询无线耳机价格","intent":"query","depends_on":[]},
      {"text":"基于查询结果下单无线耳机","intent":"order","depends_on":["task_0"],"order_operation":"create"}
    ]}

    "帮我下一单" →
    {"tasks":[{"text":"用户未提供下单商品信息，需确认具体商品","intent":"unknown","depends_on":[]}]}

    "我刚才说了什么" →
    {"tasks":[{"text":"回顾本会话中用户已说的话","intent":"session_meta","depends_on":[]}]}

    以下为完整上下文（含更早摘要、最近对话节选与「当前用户输入」）。请结合全部上下文判断意图并拆解任务。
    """
).strip()


class IntentRouterAgent:
    def __init__(self) -> None:
        self.settings = load_settings()
        self.llm = LLMRouter(self.settings.intent_agent_llm)

    def analyze(
        self,
        router_prompt_block: str,
        *,
        turn_id: str,
        session_id: str,
        session_store: SessionStore | None = None,
    ) -> tuple[IntentType, list[Task]]:
        hist_block = self._history_dep_block(session_id, session_store)
        prompt = _INTENT_ROUTER_PROMPT_PREFIX + hist_block + "\n\n" + router_prompt_block

        data = self.llm.invoke_json(prompt)
        if not isinstance(data, dict):
            raise ValueError("intent_router analyze failed: root must be a JSON object")
        raw_tasks = data.get("tasks", [])
        tasks = self._validate_and_build_tasks(raw_tasks, turn_id=turn_id, session_id=session_id, session_store=session_store)

        if not tasks:
            raise ValueError("intent_router analyze failed: empty tasks from llm")

        derived_route = self._derive_session_route(tasks)
        route: IntentType = derived_route  # type: ignore[assignment]
        return route, tasks

    @staticmethod
    def _history_dep_block(session_id: str, session_store: SessionStore | None) -> str:
        if not session_store:
            return ""
        arts = session_store.iter_recent_step_artifacts(session_id, limit=12)
        if not arts:
            return ""
        lines = []
        for a in arts:
            lines.append(
                json.dumps(
                    {"turn_id": a.turn_id, "step_id": a.step_id, "intent": a.intent, "status": a.status},
                    ensure_ascii=False,
                )
            )
        return (
            "\n## 历史步骤清单（跨轮 depends_on 须引用其中 turn_id + step_id）\n"
            + "\n".join(lines)
        )

    def _derive_session_route(self, tasks: list[Task]) -> str:
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
        if intent in {"query", "rule", "order", "handoff", "unknown", "session_meta"}:
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

    def _parse_depends_raw(self, raw: Any) -> list[Any]:
        if raw is None:
            return []
        if not isinstance(raw, list):
            return []
        return list(raw)

    def _item_to_step_ref(
        self,
        item: Any,
        *,
        turn_id: str,
        max_local_index_exclusive: int,
    ) -> StepRef | None:
        """max_local_index_exclusive：当前任务下标 i，仅允许引用 task_0 .. task_{i-1}。"""
        if isinstance(item, str):
            s = item.strip()
            # 模型误输出全局 id 串「{turn}:{task_k}」：拆成 StepRef
            if ":" in s:
                prefix, suffix = s.rsplit(":", 1)
                prefix = prefix.strip()
                mloc = _TASK_LOCAL_RE.match(suffix)
                if not mloc:
                    return None
                idx = int(mloc.group(1))
                local = suffix
                full_sid = f"{prefix}:{local}"
                if prefix == turn_id:
                    if idx >= max_local_index_exclusive:
                        return None
                    return StepRef(turn_id=turn_id, step_id=full_sid)
                # 跨轮：prefix 为历史 turn_id
                return StepRef(turn_id=prefix, step_id=full_sid)
            m = _TASK_LOCAL_RE.match(s)
            if not m:
                return None
            idx = int(m.group(1))
            if idx >= max_local_index_exclusive:
                return None
            local = f"task_{idx}"
            return StepRef(turn_id=turn_id, step_id=global_step_id(turn_id, local))
        if isinstance(item, dict):
            tid = str(item.get("turn_id") or "").strip()
            sid = str(item.get("step_id") or "").strip()
            if not tid or not sid:
                return None
            return StepRef(turn_id=tid, step_id=sid)
        return None

    def _validate_history_refs(
        self,
        tasks: list[Task],
        *,
        current_turn_id: str,
        session_id: str,
        session_store: SessionStore | None,
    ) -> None:
        if not session_store:
            return
        for t in tasks:
            for ref in t.depends_on:
                if ref.turn_id == current_turn_id:
                    continue
                art = session_store.get_step_artifact(session_id, ref.step_id)
                if art is None:
                    raise ValueError(
                        f"意图拆解引用了不存在的历史步骤 step_id={ref.step_id!r} turn_id={ref.turn_id!r}"
                    )
                if art.turn_id != ref.turn_id:
                    raise ValueError(
                        f"历史步骤 turn_id 不一致：引用 {ref.turn_id!r} 实际 {art.turn_id!r}"
                    )

    def _validate_and_build_tasks(
        self,
        raw_tasks: Any,
        *,
        turn_id: str,
        session_id: str,
        session_store: SessionStore | None,
    ) -> list[Task]:
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
            local_tid = f"task_{i}"
            global_tid = global_step_id(turn_id, local_tid)

            intent = self._normalize_intent(item.get("intent", "unknown"))
            raw_dep = self._parse_depends_raw(item.get("depends_on", item.get("dependsOn", [])))
            depends_on: list[StepRef] = []
            seen: set[tuple[str, str]] = set()
            for d in raw_dep:
                ref = self._item_to_step_ref(d, turn_id=turn_id, max_local_index_exclusive=i)
                if ref is None:
                    continue
                key = (ref.turn_id, ref.step_id)
                if key in seen:
                    continue
                seen.add(key)
                depends_on.append(ref)

            op_hint = self._normalize_order_operation(item.get("order_operation") or item.get("orderOperation"))

            if intent == "query":
                result.append(QueryTask(id=global_tid, text=text, depends_on=depends_on))
            elif intent == "rule":
                result.append(RuleTask(id=global_tid, text=text, depends_on=depends_on))
            elif intent == "order":
                result.append(
                    OrderTask(
                        id=global_tid,
                        text=text,
                        depends_on=depends_on,
                        order_operation_hint=op_hint,
                    )
                )
            elif intent == "handoff":
                result.append(HandoffTask(id=global_tid, text=text, depends_on=depends_on))
            elif intent == "session_meta":
                result.append(SessionMetaTask(id=global_tid, text=text, depends_on=depends_on))
            else:
                result.append(UnknownTask(id=global_tid, text=text, depends_on=depends_on))

        validate_same_turn_refs(result, turn_id)
        detect_cycle_same_turn(result, turn_id)
        self._validate_history_refs(result, current_turn_id=turn_id, session_id=session_id, session_store=session_store)
        return result
