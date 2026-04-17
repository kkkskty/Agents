# """会话元问题：规则判定 + 基于 conversation.history 的回复格式化（不走 SQL/RAG）。"""
#
# import re
#
# from app.core.state import ConversationTurn
#
#
# def is_session_meta_question(text: str) -> bool:
#     """是否为「关于本会话历史」的追问，命中则走编排短路，不拆解意图、不跑检索。"""
#     t = (text or "").strip()
#     if len(t) < 2:
#         return False
#     patterns = (
#         r"之前.*(问|说|提)",
#         r"上一轮",
#         r"刚才.*(问|说|提)",
#         r"历史记录",
#         r"会话.*(记录|历史)",
#         r"我(刚才|之前).{0,12}(问|说|提)了什么",
#         r"你记.{0,6}(我|刚才|之前)",
#         r"重述.*(问题|说过)",
#         r"刚才.*内容",
#         r"上面.*(问|说)",
#         r"我提过什么",
#         r"问过什么",
#     )
#     for p in patterns:
#         if re.search(p, t):
#             return True
#     if t in {"我之前问了什么", "我之前提了什么问题", "我刚才问了什么", "历史消息"}:
#         return True
#     return False
#
#
# def format_meta_session_reply(
#     history: list[ConversationTurn],
#     *,
#     max_history_turns: int,
#     current_text: str,
# ) -> str:
#     """
#     按 MAX_HISTORY_TURNS 控制窗口，从 history 尾部取若干条；突出展示用户发言。
#     max_history_turns：与 settings 一致，最多保留约该「轮次」×2 条消息（user/assistant 交替计）。
#     """
#     max_keep = max(2, max_history_turns * 2)
#     if not history:
#         return (
#             "当前会话里还没有更早的提问记录。"
#             f"（本轮输入：{current_text.strip()[:300]}{'…' if len(current_text.strip()) > 300 else ''}）"
#         )
#
#     tail = history[-max_keep:]
#     user_items: list[str] = []
#     for t in tail:
#         if t.role != "user":
#             continue
#         c = (t.content or "").strip().replace("\r\n", "\n")
#         if len(c) > 1200:
#             c = c[:1200] + "…"
#         user_items.append(c)
#
#     if not user_items:
#         lines = ["本段历史窗口内暂无用户发言记录（可能仅有助手回复）。"]
#     else:
#         lines = ["您在本会话中曾输入过（按时间顺序，不含本轮正在输入）："]
#         for i, u in enumerate(user_items, start=1):
#             lines.append(f"{i}. {u}")
#
#     lines.append("")
#     lines.append("如需继续办理业务，请直接说明新问题。")
#     return "\n".join(lines)
