from app.core.state import AgentResult, RagTaskRecord
from app.core.settings import load_settings
from app.tools.rag_tool import (
    build_context_from_hits,
    generate_answer,
    get_vector_retriever,
    merge_retrieval_hits,
    postprocess_answer,
    rewrite_queries,
)


class RAGTool:
    def __init__(self) -> None:
        self.settings = load_settings()
        self.retriever = get_vector_retriever()

    def handle(self, text: str) -> AgentResult:
        query_variants = rewrite_queries(text)
        if not query_variants:
            return AgentResult(
                route="rule",
                status="no_result",
                message="输入为空，无法执行 RAG 检索。",
                error="rag_empty_query",
            )

        try:
            all_hits = []
            for q in query_variants:
                all_hits.extend(self.retriever.retrieve(q, top_k=self.settings.rag_top_k))
            hits = merge_retrieval_hits(
                all_hits,
                score_threshold=self.settings.rag_score_threshold,
                enable_rerank=self.settings.rag_enable_rerank,
                query=text,
            )
        except Exception as exc:
            detail = str(exc).strip()
            low = detail.lower()
            if len(detail) > 400:
                detail = detail[:400] + "…"
            hint = ""
            if "403" in detail or "insufficient" in low or "quota" in low or "balance" in low:
                hint = "（Embedding API 额度/余额不足或为 403：请充值或更换 RAG_EMBEDDING_API_KEY / 本地模型。）"
            return AgentResult(
                route="rule",
                status="error",
                message=(
                    "RAG 检索失败。"
                    + (f" 原因：{detail}" if detail else " 请检查网络、API Key、CHROMA_PATH 与集合配置。")
                    + hint
                ),
                error=str(exc),
            )

        if not hits:
            return AgentResult(
                route="rule",
                status="no_result",
                message="RAG Tool 未检索到规则相关内容，请补充规则名称或场景。",
                error="rag_no_result",
            )
        context = build_context_from_hits(
            hits,
            budget_chars=self.settings.rag_context_char_budget,
            max_snippets=self.settings.rag_generation_max_snippets,
        )
        draft = generate_answer(text, context)
        summary, _, _ = postprocess_answer(draft, hits)
        return AgentResult(
            route="rule",
            status="ok",
            message=summary,
            citations=hits,
        )

    def handle_with_state(self, state, text: str) -> dict:
        runtime = state["runtime"]
        trace = state["trace"]["rag_trace"]
        idx = runtime["current_task_index"]
        tasks = runtime["sub_tasks"]
        task_id = tasks[idx].id if 0 <= idx < len(tasks) else "unknown"
        result = self.handle(text)
        hits = result.citations or []
        rewrites = rewrite_queries(text)
        context = build_context_from_hits(
            hits,
            budget_chars=self.settings.rag_context_char_budget,
            max_snippets=self.settings.rag_generation_max_snippets,
        )
        post_text, confidence, flags = postprocess_answer(result.message, hits)
        result.message = post_text
        top_k = len(hits)
        rec = RagTaskRecord(
            task_id=task_id,
            retrieval_query=text,
            top_k=top_k,
            retrieved_chunks=list(hits),
            filtered_chunks=list(hits),
            selected_citations=list(hits),
            rewrite_queries=rewrites,
            context_preview=context[:600],
            generated_answer=result.message,
            confidence=confidence,
            postprocess_flags=flags,
        )
        trace.records.append(rec)
        return {"runtime": {**runtime, "raw": result}}
