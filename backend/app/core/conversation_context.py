"""拼装 IntentRouter / Summarizer 可用的对话上下文（摘要 + 最近若干轮）。"""

from __future__ import annotations

from app.core.settings import AppSettings
from app.core.state import ConversationState


def _turn_lines(history_tail: list, max_line_chars: int = 400) -> list[str]:
    lines: list[str] = []
    for t in history_tail:
        role = str(getattr(t, "role", "") or "")
        intent = getattr(t, "intent", None)
        content = str(getattr(t, "content", "") or "").strip().replace("\n", " ")
        if len(content) > max_line_chars:
            content = content[:max_line_chars] + "…"
        suf = f" intent={intent}" if intent else ""
        lines.append(f"[{role}]{suf} {content}")
    return lines


def build_context_for_router(
    conv: ConversationState,
    current_user_text: str,
    settings: AppSettings,
) -> str:
    """拼接路由用上下文字符串；当前轮用户句不在 history 内时需单独传入。"""
    rounds = max(1, settings.router_context_rounds)
    max_turns = 2 * rounds
    max_chars = settings.router_context_max_chars

    summary = str(conv.memory_summary or "").strip()

    hist = list(conv.history)
    recent = hist[-max_turns:] if len(hist) > max_turns else hist
    recent_lines = _turn_lines(recent)

    sections: list[str] = []

    if summary:
        sections.append("【更早对话摘要】\n" + summary)
    else:
        sections.append("【更早对话摘要】\n（无）")

    sections.append("【最近若干轮对话】\n" + ("\n".join(recent_lines) if recent_lines else "（尚无历史轮次）"))

    cur = (current_user_text or "").strip()
    sections.append("【当前用户输入】\n" + cur)

    block = "\n\n".join(sections)

    if len(block) <= max_chars:
        return block

    # 超长：先缩短摘要区，再删最早的历史行（保留当前用户输入整块）
    tail_anchor = "\n\n【当前用户输入】\n" + cur
    budget = max_chars - len(tail_anchor)
    if budget < 80:
        return ("【上下文已截断】\n" + cur)[-max_chars:]

    head_candidate = "\n\n".join(sections[:-1])
    if len(head_candidate) > budget:
        head_candidate = head_candidate[:budget] + "\n…（前文已截断）"
    return head_candidate + tail_anchor


def build_context_for_summarizer(
    conv: ConversationState,
    current_user_text: str,
    settings: AppSettings,
) -> str:
    """Summarize 节点在 save 之前执行，history 不含本轮；仅用过去轮次 + 摘要辅助 LLM。"""
    rounds = max(1, settings.summarizer_context_rounds)
    max_turns = 2 * rounds
    max_chars = settings.summarizer_context_max_chars

    summary = str(conv.memory_summary or "").strip()
    hist = list(conv.history)
    recent = hist[-max_turns:] if len(hist) > max_turns else hist
    recent_lines = _turn_lines(recent, max_line_chars=320)

    parts: list[str] = []
    if summary:
        parts.append("【会话摘要】\n" + summary)
    rounds_body = "\n".join(recent_lines) if recent_lines else "（尚无更早轮次）"
    parts.append("【此前对话节选】\n" + rounds_body)

    cur = (current_user_text or "").strip()
    parts.append("【本轮用户原话】\n" + cur)

    block = "\n\n".join(parts)
    if len(block) <= max_chars:
        return block
    return block[: max_chars - 10] + "\n…（截断）"
