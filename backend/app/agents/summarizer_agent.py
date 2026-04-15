from typing import Any

from app.core.llm_provider import LLMRouter
from app.core.settings import load_settings
from app.core.state import AgentResult


def _collect_task_citations(state: dict[str, Any], task_id: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str]] = set()

    def _push(c: dict[str, Any]) -> None:
        source = str(c.get("source") or "unknown")
        chunk_id = int(c.get("chunk_id") or 0)
        snippet = str(c.get("snippet") or "").strip()
        if not snippet:
            return
        k = (source, chunk_id, snippet)
        if k in seen:
            return
        seen.add(k)
        out.append({"source": source, "chunk_id": chunk_id, "snippet": snippet})

    ctx = (state.get("task_context") or {}).get(task_id)
    if isinstance(ctx, dict):
        for key in ("citations", "sql_citations", "rag_selected_citations"):
            for c in (ctx.get(key) or []):
                if isinstance(c, dict):
                    _push(c)

    for rec in (getattr(state.get("sql_query_trace"), "records", None) or []):
        if rec.task_id == task_id:
            for c in (rec.citations or []):
                if isinstance(c, dict):
                    _push(c)

    for rec in (getattr(state.get("rag_trace"), "records", None) or []):
        if rec.task_id == task_id:
            for c in (rec.selected_citations or []):
                if isinstance(c, dict):
                    _push(c)
    return out


def _citation_snippets(citations: list[dict[str, Any]], limit: int = 8) -> str:
    rows: list[str] = []
    for c in citations[:limit]:
        s = str(c.get("snippet") or "").strip()
        if not s:
            continue
        s = s.replace("\r", " ").replace("\n", " ")
        rows.append(f"- {s[:180]}{'...' if len(s) > 180 else ''}")
    return "\n".join(rows) if rows else "（无）"


def _fallback_answer_from_citations(question: str, citations: list[dict[str, Any]]) -> str:
    if not citations:
        return "当前没有检索到可用证据，建议补充更具体的问题关键词。"

    q_tokens = [t for t in question.replace("？", " ").replace("，", " ").split() if t]
    candidates: list[str] = []
    for c in citations[:6]:
        s = str(c.get("snippet") or "").strip()
        if not s:
            continue
        s = s.replace("\r", " ").replace("\n", " ")
        parts = [p.strip() for p in s.replace("。", "；").split("；") if p.strip()]
        candidates.extend(parts[:4])

    if not candidates:
        return "已检索到相关证据，但未提取到有效要点。请查看下方引用。"

    scored: list[tuple[int, str]] = []
    for p in candidates:
        score = 0
        for tk in q_tokens:
            if tk and tk in p:
                score += 2
        for kw in ("步骤", "处理", "联系", "提交", "核查", "转人工", "失败", "规则"):
            if kw in p:
                score += 1
        scored.append((score, p))
    scored.sort(key=lambda x: x[0], reverse=True)

    picked: list[str] = []
    seen: set[str] = set()
    for _, p in scored:
        t = p[:60] + ("..." if len(p) > 60 else "")
        if t in seen:
            continue
        seen.add(t)
        picked.append(t)
        if len(picked) >= 3:
            break

    if not picked:
        return "已检索到相关证据，请查看下方引用。"
    return f"结合检索证据，与你的问题最相关的要点是：{'；'.join(picked)}。"


_MAX_QUERY_SYNTH_BODY = 4500


def _should_skip_sql_llm_rewrite(task_status: str, query_message: str) -> bool:
    """失败、无数据或明显错误文案时不走总结模型，避免编造条数与商品名。"""
    if task_status != "ok":
        return True
    qm = (query_message or "").strip()
    if not qm:
        return True
    if "未查询到相关数据" in qm:
        return True
    if "查询执行失败" in qm or "大模型未配置" in qm or "无法生成查询" in qm:
        return True
    if "大模型生成的 SQL 不可用" in qm:
        return True
    return False


class SummarizerAgent:
    def __init__(self) -> None:
        settings = load_settings()
        self.llm = LLMRouter(settings.summarizer_agent_llm)

    def _synthesize_sql_answer(
        self,
        *,
        user_text: str,
        subtask_text: str,
        query_message: str,
        task_status: str,
    ) -> str:
        """把「查询结果罗列」改写成先答用户所问、再附精简明细；失败则退回原文。"""
        qm = (query_message or "").strip()
        if _should_skip_sql_llm_rewrite(task_status, qm):
            return qm or "未获取到查询结果。"
        body = qm if len(qm) <= _MAX_QUERY_SYNTH_BODY else qm[:_MAX_QUERY_SYNTH_BODY] + "\n…（以下略）"
        prompt = (
            "你是电商数据客服。下面「查询结果」来自数据库 SELECT，是客观事实。\n"
            "请按用户真正关心的问题组织回答，禁止只罗列编号条目而不作答。\n"
            "结构要求：\n"
            "1）先用 1～3 句「结论」直接回答用户（例如问「多少钱」则概括各商品当前单价/成交价要点；问「有哪些订单」则概括笔数、状态与金额范围等）；\n"
            "2）若结果中有可对比信息（如下单时单价与商品现价），在结论中点明；\n"
            "3）另起一行写「明细：」，后面用极短分点或一行话摘录关键字段，不要重复粘贴整表。\n"
            "硬性约束（违反则视为错误回答）：\n"
            "- 条数、金额、商品名必须与【查询结果】中出现的文字一致；若原文写「共 n 条」或列出若干行，结论中的条数须一致。\n"
            "- 禁止使用「商品A」「商品B」「商品一」等虚构代号；商品名只能来自【查询结果】原文中的名称字段。\n"
            "- 若【查询结果】仅有一句话表示无数据，则只回答未查到，不得推断有记录或涨跌。\n"
            "- 不得编造【查询结果】中未出现的数字或状态。\n"
            f"【用户原话】\n{user_text}\n\n"
            f"【子任务】\n{subtask_text}\n\n"
            f"【查询结果】\n{body}\n"
        )
        try:
            txt = self.llm.invoke_text(prompt)
            if txt and txt.strip():
                return txt.strip()
        except Exception:
            pass
        return qm

    def _answer_from_outputs_and_citations(
        self,
        question: str,
        intent: str,
        outputs: dict[str, Any],
        citations: list[dict[str, Any]],
        *,
        current_user_text: str,
    ) -> str:
        snippets = _citation_snippets(citations)
        drop_items = outputs.get("drop_items") or []
        proposed = outputs.get("proposed_order_items") or []
        outputs_hint = (
            f"结构化输出：drop_items={drop_items[:5]}, proposed_order_items={proposed[:5]}"
            if outputs
            else "结构化输出：无"
        )
        prompt = (
            "你是客服总结助手。请基于引用证据进行总结，回答要精炼、可执行。"
            "不要逐字抄写引用原文，不要编造事实。\n"
            f"【本轮用户完整输入】\n{current_user_text}\n\n"
            f"问题类型：{intent}\n"
            f"当前子任务原文：{question}\n"
            f"{outputs_hint}\n"
            f"引用证据：\n{snippets}"
        )
        fallback = _fallback_answer_from_citations(question, citations)
        try:
            txt = self.llm.invoke_text(prompt)
            if txt:
                return txt
        except Exception:
            pass
        return fallback

    def summarize_with_state(self, state) -> dict:
        current_user_text = (state.get("text") or "").strip()

        tasks = state.get("sub_tasks", [])
        task_results = state.get("task_results", [])
        result_map = {str(t.get("task_id", "")): t for t in task_results}
        merged_citations: list[dict[str, Any]] = []
        lines: list[str] = []

        if not tasks and state.get("raw") is not None:
            raw = state["raw"]
            tasks = [type("TmpTask", (), {"id": "task_0", "text": state.get("text", ""), "intent": raw.route})()]
            result_map["task_0"] = {
                "task_id": "task_0",
                "intent": raw.route,
                "status": raw.status,
                "message": raw.message,
                "error": raw.error,
            }

        for idx, task in enumerate(tasks, start=1):
            tid = task.id
            q = str(task.text or "").strip() or "（未提供）"
            intent = str(task.intent or "unknown")
            tr = result_map.get(tid, {})
            status = str(tr.get("status", "unknown"))
            msg = str(tr.get("message", "") or "")
            tctx = (state.get("task_context") or {}).get(tid) or {}
            outputs = tctx.get("outputs") if isinstance(tctx, dict) else {}
            citations = _collect_task_citations(state, tid)
            merged_citations.extend(citations)

            if intent == "session_meta":
                ans = msg
            elif intent == "query":
                # SQL 结果先经 SearchAgent 写入 message；再改写为「结论 + 明细」以贴合用户问法
                if msg.strip():
                    ans = self._synthesize_sql_answer(
                        user_text=current_user_text,
                        subtask_text=q,
                        query_message=msg,
                        task_status=status,
                    )
                else:
                    ans = self._answer_from_outputs_and_citations(
                        q,
                        intent,
                        outputs if isinstance(outputs, dict) else {},
                        citations,
                        current_user_text=current_user_text,
                    )
            elif intent == "rule":
                if status in ("error", "failed"):
                    ans = (msg or "").strip() or "规则检索失败，请稍后重试。"
                elif status == "no_result" and not citations:
                    ans = (msg or "").strip() or "未检索到相关规则。"
                else:
                    ans = self._answer_from_outputs_and_citations(
                        q,
                        intent,
                        outputs if isinstance(outputs, dict) else {},
                        citations,
                        current_user_text=current_user_text,
                    )
            elif intent == "order":
                order_link = tr.get("order_link") or ""
                link_txt = f"，订单链接：{order_link}" if order_link else ""
                ans = f"当前订单处理状态：{status}。{msg}{link_txt}"
            elif intent == "handoff":
                hs = state.get("handoff").status if state.get("handoff") else "inactive"
                ans = f"当前人工处理状态：{hs}。{msg}"
            else:
                ans = (msg or "").strip() or "未识别意图，请说明您是要查询信息、咨询规则，还是处理订单（下单/退单/修改）。"

            cont = bool(state.get("continuing_order_session"))
            use_plain = (
                cont
                or intent == "session_meta"
                or (len(tasks) == 1 and intent == "query")
                or (len(tasks) == 1 and intent == "rule")
                or (
                    len(tasks) == 1
                    and intent == "order"
                    and status in ("collecting_info", "awaiting_pre_confirm")
                )
            )
            if use_plain:
                lines.append(ans)
            else:
                lines.append(f"针对问题{idx}：{q}：{ans}")

        if len(tasks) == 1 and tasks[0].intent in {
            "query",
            "rule",
            "order",
            "handoff",
            "unknown",
            "session_meta",
        }:
            final_route = tasks[0].intent
        elif len(tasks) > 1:
            final_route = "unknown"
            for t in tasks:
                if t.intent != "unknown":
                    final_route = t.intent
                    break
        else:
            final_route = "unknown"
        if len(tasks) == 1:
            only_tid = tasks[0].id
            final_status = str(result_map.get(only_tid, {}).get("status", "ok"))
        else:
            final_status = "ok"

        final_result = AgentResult(
            route=final_route,
            status=final_status,
            message="\n".join(lines),
            citations=merged_citations or None,
        )
        obs = state["observability"]
        final_result.request_id = obs.request_id
        final_result.workflow_step = state["order_workflow"].step
        final_result.handoff_status = state["handoff"].status
        final_result.debug_trace = {
            "node_timings": obs.node_timings,
            "node_logs": obs.node_logs,
            "errors": obs.errors,
        }
        done = len(task_results)
        total = len(tasks)
        final_result.sub_task_count = total
        final_result.sub_task_progress = f"{done}/{total}" if total else None
        final_result.pending_actions = state.get("pending_actions", [])
        return {"result": final_result}
