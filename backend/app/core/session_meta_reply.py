"""会话元问题：根据 memory_summary 与 history 生成回复，不走数据库查询。"""

from __future__ import annotations

from app.core.state import ConversationState


def format_session_meta_reply(conv: ConversationState, current_user_text: str, *, max_turn_lines: int = 24) -> str:
    """列出本轮写入历史之前的摘要与对话节选。"""
    summary = (conv.memory_summary or "").strip()
    hist = list(conv.history)
    chunks: list[str] = []

    if summary:
        chunks.append("【更早会话摘要】")
        chunks.append(summary)
        chunks.append("")

    ut = (current_user_text or "").strip()
    user_only = any(
        k in ut
        for k in (
            "我说了什么",
            "我说过什么",
            "我刚才说",
            "我的话",
            "我上次说",
            "重复我说的",
            "列出我说",
        )
    )

    if not hist:
        if summary:
            chunks.append("【本轮之前的用户发言】")
            chunks.append("（尚无已保存的用户句；摘要区已覆盖更早折叠内容。）")
        else:
            chunks.append("本轮之前尚无已保存的对话记录。")
            chunks.append("若这是本会话的第一句，可从您的本轮输入继续。")
        chunks.append("")
        chunks.append("（说明：您当前这句话尚未写入会话历史。）")
        return "\n".join(chunks)

    tail = hist[-max_turn_lines:]
    if user_only:
        chunks.append("【本轮之前您的发言】")
        n = 0
        for t in tail:
            if str(getattr(t, "role", "") or "").strip() != "user":
                continue
            n += 1
            content = str(getattr(t, "content", "") or "").strip().replace("\n", " ")
            if len(content) > 600:
                content = content[:600] + "…"
            chunks.append(f"{n}. {content}")
        if n == 0:
            chunks.append("（暂无更早的用户句。）")
    else:
        chunks.append("【本轮之前最近对话】")
        for i, t in enumerate(tail, start=1):
            role = str(getattr(t, "role", "") or "").strip()
            zh = "用户" if role == "user" else ("助手" if role == "assistant" else role or "?")
            content = str(getattr(t, "content", "") or "").strip().replace("\n", " ")
            if len(content) > 600:
                content = content[:600] + "…"
            chunks.append(f"{i}. [{zh}] {content}")

    chunks.append("")
    chunks.append("（说明：以上为会话历史中已保存的内容；您当前这句话尚未写入历史。）")
    return "\n".join(chunks)
