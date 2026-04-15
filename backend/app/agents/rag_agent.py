from app.core.state import AgentResult, RagTaskRecord
from app.tools.rag_tool import query_chroma


class RAGTool:
    def __init__(self) -> None:
        # RAG 作为检索工具使用：仅返回证据，不在本节点调用 LLM 生成最终回答。
        pass

    def handle(self, text: str) -> AgentResult:
        try:
            hits = query_chroma(text)  #RAG查询
        except Exception as exc:
            detail = str(exc).strip()
            if len(detail) > 400:
                detail = detail[:400] + "…"
            return AgentResult(
                route="rule",
                status="error",
                message=(
                    "RAG 检索失败。"
                    + (f" 原因：{detail}" if detail else " 请检查网络、API Key、CHROMA_PATH 与集合配置。")
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

        snippets = []
        for item in hits[:3]:
            s = item.get("snippet")
            if s:
                snippets.append(f"- {s}")
        summary = "规则检索结果：\n" + ("\n".join(snippets) if snippets else "（无可展示片段）")
        return AgentResult(
            route="rule",
            status="ok",
            message=summary,
            citations=hits,
        )

    def handle_with_state(self, state, text: str) -> dict:
        idx = state.get("current_task_index", 0)
        tasks = state.get("sub_tasks", [])
        task_id = tasks[idx].id if 0 <= idx < len(tasks) else "unknown"
        state["rag_trace"].retrieval_query = text
        result = self.handle(text)
        hits = result.citations or []
        top_k = len(hits)
        rec = RagTaskRecord(
            task_id=task_id,
            retrieval_query=text,
            top_k=top_k,
            retrieved_chunks=list(hits),
            filtered_chunks=list(hits),
            selected_citations=list(hits),
        )
        state["rag_trace"].records.append(rec)
        state["rag_trace"].top_k = top_k
        state["rag_trace"].retrieved_chunks = hits
        state["rag_trace"].filtered_chunks = hits
        state["rag_trace"].selected_citations = hits
        return {"raw": result}
