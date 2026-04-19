import type { ChatMessage, Citation, PendingAction } from '../types/chat'

const base = () =>
  (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, '') ?? ''

/** 供页面判断是否连接后端 */
export function getApiBase(): string {
  return base()
}

export type HealthResponse = {
  ok: boolean
  sessions_persistence?: boolean
}

function mapAssistantRequestError(e: unknown): Error {
  if (e instanceof TypeError) {
    return new Error('大模型服务连接失败，请确认后端已启动且网络正常。')
  }
  if (e instanceof Error) {
    if (/Failed to fetch|NetworkError|Load failed|fetch/i.test(e.message)) {
      return new Error('大模型服务连接失败，请确认后端已启动且网络正常。')
    }
    return e
  }
  return new Error('请求失败，请稍后重试。')
}

function sanitizeBackendError(raw?: string): string {
  const msg = (raw || '').trim()
  if (!msg) return ''
  // 屏蔽底层系统异常细节，避免前端直接暴露如 [Errno xx] / OSError 等信息
  if (/\[Errno\s*\d+\]|Invalid argument|OSError|WinError/i.test(msg)) {
    return '系统处理过程中出现异常，请稍后重试；如持续失败请联系人工客服。'
  }
  return msg
}

export async function fetchHealth(apiBase: string): Promise<HealthResponse> {
  try {
    const res = await fetch(`${apiBase}/api/v1/health`)
    if (!res.ok) throw new Error(`health ${res.status}`)
    return (await res.json()) as HealthResponse
  } catch (e) {
    throw mapAssistantRequestError(e)
  }
}

export type ConversationDTO = {
  id: string
  username: string
  title: string | null
  status: string
  created_at: string
  updated_at: string
}

export type StoredMessageDTO = {
  id: number
  role: string
  content: string
  sort_order: number
  created_at: string
}

export async function listConversations(
  apiBase: string,
  userUsername: string,
): Promise<ConversationDTO[]> {
  void apiBase
  void userUsername
  return []
}

export async function deleteConversation(
  apiBase: string,
  conversationId: string,
  userUsername: string,
): Promise<void> {
  void apiBase
  void conversationId
  void userUsername
}

/** 删除当前用户在服务端下的全部会话及消息（清空会话时使用） */
export async function deleteAllConversations(
  apiBase: string,
  userUsername: string,
): Promise<void> {
  void apiBase
  void userUsername
}

export async function createConversation(
  apiBase: string,
  userUsername: string,
  title?: string,
): Promise<ConversationDTO> {
  void apiBase
  return {
    id: `local-${userUsername}`,
    username: userUsername,
    title: title ?? null,
    status: 'open',
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  }
}

export async function getConversationMessages(
  apiBase: string,
  conversationId: string,
  userUsername: string,
): Promise<StoredMessageDTO[]> {
  void apiBase
  void conversationId
  void userUsername
  return []
}

export async function appendConversationMessage(
  apiBase: string,
  conversationId: string,
  userUsername: string,
  role: 'user' | 'assistant' | 'system',
  content: string,
): Promise<void> {
  void apiBase
  void conversationId
  void userUsername
  void role
  void content
}

export function storedMessageToChatMessage(m: StoredMessageDTO): ChatMessage {
  const r = m.role === 'user' ? 'user' : 'assistant'
  return {
    id: `db-${m.id}`,
    role: r,
    content: m.content,
    createdAt: new Date(m.created_at).getTime(),
  }
}

/**
 * 若设置 VITE_API_BASE_URL，则统一通过 POST {base}/api/chat 与后端交互：
 * - body: { messages: { role, content }[], user_username?: string }，由后端 Agent 决定是否仅回答、做 RAG 检索，或调用工具
 *   （如基于本地 MySQL 的 get_user_orders / get_user_coupons / handoff_to_human 等）。
 * - 期望响应 JSON: { reply: string } 或 { message: string }，代表最终给玩家看的文本回复。
 * 未设置时使用本地演示回复。
 */
export type ChatRequestOptions = {
  /** 当前登录用户名（演示会话），供后端回答「我是谁」等 */
  userUsername?: string
  sessionId?: string
}

export async function requestAssistantReply(
  history: ChatMessage[],
  options?: ChatRequestOptions,
): Promise<{
  reply: string
  citations?: Citation[]
  sessionId?: string
  actionRequired?: string
  orderLink?: string
  pendingActions?: PendingAction[]
}> {
  const apiBase = base()
  if (apiBase) {
    const user_id = options?.userUsername?.trim() || 'guest'
    const text = history.filter((m) => m.role === 'user').pop()?.content?.trim() || ''
    let res: Response
    try {
      res = await fetch(`${apiBase}/api/v1/chat/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          user_id,
          text,
          session_id: options?.sessionId,
        }),
      })
    } catch (e) {
      throw mapAssistantRequestError(e)
    }
    if (!res.ok) {
      let detail = ''
      try {
        detail = await res.text()
      } catch {
        /* ignore */
      }
      if (res.status === 0 || res.status >= 500) {
        throw new Error('大模型服务暂时不可用，请稍后重试或确认后端已启动。')
      }
      throw new Error(detail?.trim() || `请求失败（${res.status}）`)
    }
    const data = (await res.json()) as {
      session_id?: string
      reply?: string
      status?: string
      citations?: Citation[]
      action_required?: string
      order_link?: string
      pending_actions?: PendingAction[]
      error?: string
    }
    const reply = data.reply
    if (typeof reply === 'string' && reply.trim()) {
      const isError = data.status === 'error'
      const cleanedError = sanitizeBackendError(data.error)
      const body = isError && cleanedError
        ? `${reply.trim()}\n\n（详情：${cleanedError}）`
        : reply.trim()
      return {
        reply: body,
        citations: data.citations,
        sessionId: data.session_id,
        actionRequired: data.action_required,
        orderLink: data.order_link,
        pendingActions: data.pending_actions,
      }
    }
    if (data.status === 'error' && data.error?.trim()) {
      throw new Error(`大模型服务异常：${sanitizeBackendError(data.error)}`)
    }
    throw new Error('接口未返回有效回复字段（reply / message）')
  }

  await new Promise((r) => setTimeout(r, 600 + Math.random() * 400))
  const last = history.filter((m) => m.role === 'user').pop()
  const q = last?.content?.trim() || '（空问题）'
  const u = options?.userUsername?.trim()
  const identityLike =
    u &&
    /我是谁|我的用户名|当前账号|什么名字|我叫什么|登录名/i.test(q)
  if (identityLike) {
    return {
      reply: [
        '【演示模式】当前未连接后端，但根据本地登录会话：',
        '',
        `您的登录用户名是：「${u}」。`,
        '',
        '接入真实后端后，请在 `frontend/.env` 设置 `VITE_API_BASE_URL`，由服务端同步该信息并回答。',
      ].join('\n'),
    }
  }
  return {
    reply: [
    '【演示模式 · 游戏社区智能客服】当前未连接真实客服大脑，以下为占位回复。',
    '',
    `您咨询的问题是：「${q}」`,
    '',
    '正式使用时，客服将结合平台规则与知识库为您解答。技术接入：在项目根目录创建 `.env`，设置 `VITE_API_BASE_URL` 并实现 `POST /api/chat`（格式见 `src/lib/chatApi.ts` 注释）。',
    ].join('\n'),
  }
}

export async function finalizeOrderFlow(
  apiBase: string,
  payload: { sessionId: string; userId: string; clickConfirmed: boolean },
): Promise<{ message: string; status: string }> {
  let res: Response
  try {
    res = await fetch(`${apiBase}/api/v1/orders/finalize`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: payload.sessionId,
        user_id: payload.userId,
        click_confirmed: payload.clickConfirmed,
      }),
    })
  } catch (e) {
    throw mapAssistantRequestError(e)
  }
  if (!res.ok) {
    const t = await res.text()
    throw new Error(t?.trim() || `大模型服务连接失败（${res.status}）`)
  }
  return (await res.json()) as { message: string; status: string }
}

export async function submitOrderFields(
  apiBase: string,
  payload: {
    sessionId: string
    userId: string
    fields: Record<string, string>
    items?: Array<{ item_name: string; quantity: string }>
  },
): Promise<{
  message: string
  status: string
  order_link?: string
  action_required?: string
  pending_actions?: PendingAction[]
  error?: string
}> {
  let res: Response
  try {
    res = await fetch(`${apiBase}/api/v1/orders/fill-fields`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: payload.sessionId,
        user_id: payload.userId,
        fields: payload.fields,
        items: payload.items ?? [],
      }),
    })
  } catch (e) {
    throw mapAssistantRequestError(e)
  }
  if (!res.ok) {
    const t = await res.text()
    throw new Error(t?.trim() || `订单表单提交失败（${res.status}）`)
  }
  return (await res.json()) as {
    message: string
    status: string
    order_link?: string
    action_required?: string
    pending_actions?: PendingAction[]
    error?: string
  }
}

/** 订单执行前确认（不走聊天文本，避免「确认/取消」被意图解析干扰） */
export async function submitOrderConfirm(
  apiBase: string,
  payload: { sessionId: string; userId: string; confirm: boolean },
): Promise<{
  message: string
  status: string
  order_link?: string
  action_required?: string
  pending_actions?: PendingAction[]
  error?: string
}> {
  let res: Response
  try {
    res = await fetch(`${apiBase}/api/v1/orders/confirm`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: payload.sessionId,
        user_id: payload.userId,
        confirm: payload.confirm,
      }),
    })
  } catch (e) {
    throw mapAssistantRequestError(e)
  }
  if (!res.ok) {
    const t = await res.text()
    throw new Error(t?.trim() || `订单确认请求失败（${res.status}）`)
  }
  return (await res.json()) as {
    message: string
    status: string
    order_link?: string
    action_required?: string
    pending_actions?: PendingAction[]
    error?: string
  }
}

export async function cancelOrderFlow(
  apiBase: string,
  payload: { sessionId: string; userId: string },
): Promise<{ message: string; status: string; error?: string }> {
  let res: Response
  try {
    res = await fetch(`${apiBase}/api/v1/orders/cancel-flow`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: payload.sessionId,
        user_id: payload.userId,
      }),
    })
  } catch (e) {
    throw mapAssistantRequestError(e)
  }
  if (!res.ok) {
    const t = await res.text()
    throw new Error(t?.trim() || `取消订单流程失败（${res.status}）`)
  }
  return (await res.json()) as { message: string; status: string; error?: string }
}
