"""按子任务 intent 将依赖与当前文案整理为执行器入参（可调用 LLM 输出结构化 JSON）。"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from app.core.llm_provider import LLMRouter
from app.core.session_store import SessionStore
from app.core.settings import load_settings
from app.core.state import StepRef, Task


def _ref_bundle_for_prompt(refs: list[StepRef], session_id: str, store: SessionStore, current_turn_id: str) -> str:
    """将依赖解析为可放入提示的短文本。"""
    parts: list[str] = []
    for ref in refs:
        if ref.turn_id == current_turn_id:
            parts.append(f"[同轮] turn_id={ref.turn_id} step_id={ref.step_id}（由本 run 的 task_context 提供，见下条）")
        else:
            art = store.get_step_artifact(session_id, ref.step_id)
            if art is None:
                parts.append(f"[历史] {ref.turn_id}/{ref.step_id} 缺失")
            else:
                brief = (art.message or "")[:400]
                p = art.payload if isinstance(art.payload, dict) else {}
                parts.append(
                    f"[历史] intent={art.intent} status={art.status} message={brief!r} "
                    f"payload_keys={list(p.keys())[:12]}"
                )
    return "\n".join(parts) if parts else "（无依赖）"


class QueryInjectResult(BaseModel):
    enriched_question: str = Field(..., min_length=1, description="供 Text-to-SQL 的完整问句")


class RuleInjectResult(BaseModel):
    retrieval_query: str = Field(..., min_length=1, description="供 RAG 的检索问句")


class OrderInjectResult(BaseModel):
    """补充给订单链的上下文字段（可空，由链上再解析）。"""

    operation_hint: str | None = None
    user_utterance: str = ""


class SessionMetaInjectResult(BaseModel):
    user_focus: str = Field(default="", description="用户想回顾的要点，可空则由系统用原话")


class TaskInjectors:
    def __init__(self) -> None:
        s = load_settings()
        self._llm = LLMRouter(s.summarizer_agent_llm)

    def _llm_json(self, prompt: str) -> dict[str, Any]:
        return self._llm.invoke_json(prompt)

    def build_query_text(
        self,
        task: Task,
        user_utterance: str,
        *,
        turn_id: str,
        session_id: str,
        store: SessionStore,
        dep_snippets: dict[str, str] | None = None,
    ) -> str:
        refs = list(task.depends_on or [])
        if not refs and (task.text or "").strip():
            return (task.text or "").strip()

        schema = json.dumps(QueryInjectResult.model_json_schema(), ensure_ascii=False)
        dep_txt = _ref_bundle_for_prompt(refs, session_id, store, turn_id)
        same_turn_ctx = ""
        if dep_snippets:
            same_turn_ctx = "\n【同轮依赖 task_context 节选】\n" + "\n---\n".join(
                f"{k}: {v[:1200]}" for k, v in dep_snippets.items()
            )
        prompt = (
            "你是查询子任务的问句重写器。输出仅 JSON。\n"
            "Schema:\n"
            f"{schema}\n"
            f"【当前子任务】\n{task.text}\n"
            f"【用户本轮原话】\n{user_utterance}\n"
            "【依赖摘要】\n"
            f"{dep_txt}\n"
            f"{same_turn_ctx}\n"
            "生成 enriched_question：可直接用于数据库查询的自然语言。"
        )
        try:
            data = self._llm_json(prompt)
            r = QueryInjectResult.model_validate(data)
            return r.enriched_question.strip()
        except Exception:
            return f"{task.text}\n（用户原话：{user_utterance}）".strip()

    def build_rule_query(
        self,
        task: Task,
        user_utterance: str,
        *,
        turn_id: str,
        session_id: str,
        store: SessionStore,
        dep_snippets: dict[str, str] | None = None,
    ) -> str:
        refs = list(task.depends_on or [])
        if not refs:
            return (task.text or "").strip()
        schema = json.dumps(RuleInjectResult.model_json_schema(), ensure_ascii=False)
        dep_txt = _ref_bundle_for_prompt(refs, session_id, store, turn_id)
        same_turn_ctx = ""
        if dep_snippets:
            same_turn_ctx = "\n【同轮依赖 task_context】\n" + "\n---\n".join(
                f"{k}: {v[:1200]}" for k, v in dep_snippets.items()
            )
        prompt = (
            "你是规则/RAG 检索查询重写器。输出仅 JSON。\n"
            f"Schema:\n{schema}\n"
            f"【子任务】{task.text}\n【用户原话】{user_utterance}\n【依赖摘要】\n{dep_txt}\n"
            f"{same_turn_ctx}\n"
        )
        try:
            data = self._llm_json(prompt)
            return RuleInjectResult.model_validate(data).retrieval_query.strip()
        except Exception:
            return (task.text or "").strip()

    def build_session_meta_focus(self, task: Task, user_utterance: str) -> str:
        if not task.depends_on:
            return (user_utterance or "").strip()
        schema = json.dumps(SessionMetaInjectResult.model_json_schema(), ensure_ascii=False)
        prompt = (
            "输出仅 JSON。\n"
            f"Schema:\n{schema}\n"
            f"【子任务】{task.text}\n【用户原话】{user_utterance}\n"
        )
        try:
            data = self._llm_json(prompt)
            return SessionMetaInjectResult.model_validate(data).user_focus.strip() or user_utterance
        except Exception:
            return user_utterance
