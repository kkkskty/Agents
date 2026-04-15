from app.core.orchestrator import MultiAgentOrchestrator
from app.core.session_store import SessionStore


##依赖注入 初始化公用对象 给各个API路由复用
# 创建一个全局 SessionStore（用于保存会话状态、订单上下文、图状态）
# 创建一个全局 MultiAgentOrchestrator，并把这个 session_store 注入进去
# 其他模块（比如 chat.py）直接 from app.deps import orchestrator 即可使用，不用每次请求都重新 new 一遍



session_store = SessionStore()
orchestrator = MultiAgentOrchestrator(session_store=session_store)
