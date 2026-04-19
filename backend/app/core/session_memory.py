"""会话 history 超长时的裁剪与 memory_summary 合并。"""

from __future__ import annotations

import logging

from app.core.settings import AppSettings
from app.core.state import ConversationState, ConversationTurn

logger = logging.getLogger(__name__)


def merge_memory_summary(old: str, overflow: list[ConversationTurn], max_chars: int) -> str:
    """规则合并：溢出轮次线性拼接，超长从头部裁掉。"""
    lines: list[str] = []
    for t in overflow:
        role = str(getattr(t, "role", "") or "")
        content = str(getattr(t, "content", "") or "").strip().replace("\n", " ")
        if len(content) > 300:
            content = content[:300] + "…"
        intent = getattr(t, "intent", None)
        suf = f" intent={intent}" if intent else ""
        lines.append(f"[{role}]{suf} {content}")
    block = "\n".join(lines)
    old_s = (old or "").strip()
    merged = f"{old_s}\n\n---\n\n{block}".strip() if old_s else block
    if len(merged) <= max_chars:
        return merged
    return merged[-max_chars:]


def _normalize_even_history(conv: ConversationState) -> None:
    while len(conv.history) % 2 == 1 and len(conv.history) > 0:
        conv.history.pop(0)
        logger.warning("session_memory: dropped oldest orphan turn to even history length")


def trim_history_if_needed(conv: ConversationState, settings: AppSettings) -> None:
    """超过 SESSION_MEMORY_ROUNDS_K 对应条数时裁头并合并摘要。"""
    max_keep = max(2, 2 * max(1, settings.session_memory_rounds_k))
    _normalize_even_history(conv)
    if len(conv.history) <= max_keep:
        return

    overflow = conv.history[:-max_keep]
    conv.history = conv.history[-max_keep:]

    conv.memory_summary = merge_memory_summary(
        conv.memory_summary,
        overflow,
        settings.memory_summary_max_chars,
    )
