import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from 'react'
import { useAuth } from '../context/AuthContext'
import { loadMessages, saveMessages } from '../lib/chatStorage'
import {
  appendConversationMessage,
  cancelOrderFlow,
  createConversation,
  deleteAllConversations,
  fetchHealth,
  getApiBase,
  getConversationMessages,
  listConversations,
  requestAssistantReply,
  submitOrderConfirm,
  submitOrderFields,
  storedMessageToChatMessage,
} from '../lib/chatApi'
import type { ChatMessage, OrderFillFieldsAction, PendingAction } from '../types/chat'
import { MessageList } from '../components/MessageList'
import { SuggestedPrompts } from '../components/SuggestedPrompts'
import type { SuggestedPrompt } from '../constants/suggestedPrompts'

function newId(): string {
  return crypto.randomUUID()
}

type ServerMode = 'off' | 'loading' | 'on' | 'error'

export function ChatPage() {
  const { session, logout } = useAuth()
  const username = session!.username

  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [serverMode, setServerMode] = useState<ServerMode>('loading')
  const [persistReady, setPersistReady] = useState(false)
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [sendError, setSendError] = useState<string | null>(null)
  const [agentSessionId, setAgentSessionId] = useState<string | null>(null)
  const [pendingOrderFormAction, setPendingOrderFormAction] = useState<OrderFillFieldsAction | null>(null)
  const [orderFormValues, setOrderFormValues] = useState<Record<string, string>>({})
  const [detectedOrderItems, setDetectedOrderItems] = useState<Array<{ item_name: string; quantity: string }>>([])
  const [detectedCancelOrderIds, setDetectedCancelOrderIds] = useState<string[]>([])
  /** 订单确认气泡内按钮：每条消息仅能成功选择一次 */
  const [orderConfirmChoice, setOrderConfirmChoice] = useState<
    Record<string, 'confirm' | 'cancel'>
  >({})
  const [orderConfirmSubmitting, setOrderConfirmSubmitting] = useState<{
    messageId: string
    side: 'confirm' | 'cancel'
  } | null>(null)
  const orderConfirmInFlightRef = useRef<Set<string>>(new Set())
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  const pickOrderFillAction = (actions?: PendingAction[]): OrderFillFieldsAction | null => {
    if (!Array.isArray(actions)) return null
    const hit = actions.find(
      (a) =>
        !!a &&
        typeof a === 'object' &&
        'type' in a &&
        (a as { type?: string }).type === 'order_fill_fields',
    )
    if (!hit || typeof hit !== 'object') return null
    const cand = hit as Partial<OrderFillFieldsAction>
    if (!Array.isArray(cand.required_fields)) return null
    const normalizeFields = (fields?: Array<{ key: string; label?: string }>) =>
      (Array.isArray(fields) ? fields : [])
        .filter((f) => !!f && typeof f.key === 'string')
        .map((f) => ({ key: f.key, label: f.label || f.key }))
    return {
      type: 'order_fill_fields',
      task_id: cand.task_id,
      operation: cand.operation,
      required_fields: normalizeFields(cand.required_fields),
      display_fields: normalizeFields(cand.display_fields),
      readonly_fields: normalizeFields(cand.readonly_fields),
      prefill: cand.prefill ?? {},
      hint: cand.hint,
    }
  }

  const pickDetectedItems = (
    action: OrderFillFieldsAction | null,
  ): Array<{ item_name: string; quantity: string }> => {
    if (!action?.prefill || typeof action.prefill !== 'object') return []
    const prefill = action.prefill as Record<string, unknown>
    const raw = prefill.items
    if (Array.isArray(raw)) {
      const normalized = raw
        .map((it) => {
          if (!it || typeof it !== 'object') return null
          const name = String((it as Record<string, unknown>).item_name ?? '').trim()
          const qty = String((it as Record<string, unknown>).quantity ?? '').trim() || '1'
          if (!name) return null
          return { item_name: name, quantity: qty }
        })
        .filter((x): x is { item_name: string; quantity: string } => !!x)
      if (normalized.length > 0) return normalized
    }
    // 兜底：后端仅下发 item_name/quantity 时，仍构造单条清单用于前端展示。
    const name = String(prefill.item_name ?? '').trim()
    if (!name) return []
    const qty = String(prefill.quantity ?? '').trim() || '1'
    return [{ item_name: name, quantity: qty }]
  }

  const pickDetectedCancelOrderIds = (action: OrderFillFieldsAction | null): string[] => {
    if (!action?.prefill || typeof action.prefill !== 'object') return []
    const raw = (action.prefill as Record<string, unknown>).cancel_order_ids
    if (Array.isArray(raw)) {
      return raw
        .map((x) => String(x ?? '').trim())
        .filter((x, i, arr) => !!x && arr.indexOf(x) === i)
    }
    if (typeof raw === 'string') {
      return raw
        .split(',')
        .map((x) => x.trim())
        .filter((x, i, arr) => !!x && arr.indexOf(x) === i)
    }
    return []
  }

  useEffect(() => {
    let cancelled = false
    setPersistReady(false)
    setServerMode('loading')

    async function boot() {
      const api = getApiBase()
      if (!api) {
        if (!cancelled) {
          setMessages(loadMessages(username))
          setConversationId(null)
          setServerMode('off')
          setPersistReady(true)
        }
        return
      }

      try {
        const health = await fetchHealth(api)
        if (!health.sessions_persistence) {
          if (!cancelled) {
            setMessages(loadMessages(username))
            setConversationId(null)
            setServerMode('off')
            setPersistReady(true)
          }
          return
        }

        const convs = await listConversations(api, username)
        let cid: string
        if (convs.length === 0) {
          const c = await createConversation(api, username)
          cid = c.id
        } else {
          const open = convs.find((c) => c.status === 'open')
          cid = (open ?? convs[0]).id
        }
        const rows = await getConversationMessages(api, cid, username)
        const mapped = rows.map(storedMessageToChatMessage)
        if (!cancelled) {
          setConversationId(cid)
          setMessages(mapped)
          saveMessages(username, mapped)
          setServerMode('on')
          setPersistReady(true)
        }
      } catch {
        if (!cancelled) {
          setMessages(loadMessages(username))
          setConversationId(null)
          setServerMode('error')
          setPersistReady(true)
        }
      }
    }

    void boot()
    return () => {
      cancelled = true
    }
  }, [username])

  useEffect(() => {
    if (!persistReady) return
    saveMessages(username, messages)
  }, [username, messages, persistReady])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, sending])

  const showPrompts = messages.length === 0

  const ensureConversationId = useCallback(async (): Promise<string | null> => {
    const api = getApiBase()
    if (serverMode !== 'on' || !api) return conversationId
    if (conversationId) return conversationId
    const c = await createConversation(api, username)
    setConversationId(c.id)
    return c.id
  }, [conversationId, serverMode, username])

  const sendText = useCallback(
    async (text: string) => {
      const trimmed = text.trim()
      if (!trimmed || sending) return

      setSendError(null)
      const userMsg: ChatMessage = {
        id: newId(),
        role: 'user',
        content: trimmed,
        createdAt: Date.now(),
      }

      setMessages((prev) => [...prev, userMsg])
      setInput('')
      setSending(true)

      let cid: string | null = conversationId
      try {
        const api = getApiBase()
        if (serverMode === 'on' && api) {
          cid = await ensureConversationId()
          if (cid) {
            await appendConversationMessage(api, cid, username, 'user', trimmed)
          }
        }

        const history = [...messages, userMsg]
        const { reply, citations, sessionId, actionRequired, orderLink, pendingActions } = await requestAssistantReply(history, {
          userUsername: username,
          sessionId: agentSessionId ?? undefined,
        })
        if (sessionId) setAgentSessionId(sessionId)
        void actionRequired
        void orderLink
        const orderFillAction = pickOrderFillAction(pendingActions)
        if (orderFillAction) {
          setPendingOrderFormAction(orderFillAction)
          setDetectedOrderItems(pickDetectedItems(orderFillAction))
          setDetectedCancelOrderIds(pickDetectedCancelOrderIds(orderFillAction))
          const mergedPrefill: Record<string, string> = {}
          const formFields =
            orderFillAction.display_fields && orderFillAction.display_fields.length > 0
              ? orderFillAction.display_fields
              : orderFillAction.required_fields
          for (const f of formFields) {
            const value = orderFillAction.prefill?.[f.key]
            mergedPrefill[f.key] = typeof value === 'string' ? value : ''
          }
          setOrderFormValues(mergedPrefill)
        } else {
          setPendingOrderFormAction(null)
          setDetectedOrderItems([])
          setDetectedCancelOrderIds([])
          setOrderFormValues({})
        }
        const assistantMsg: ChatMessage = {
          id: newId(),
          role: 'assistant',
          content: reply,
          createdAt: Date.now(),
          citations,
        }
        setMessages((h) => [...h, assistantMsg])

        if (serverMode === 'on' && api && cid) {
          await appendConversationMessage(api, cid, username, 'assistant', reply)
        }
      } catch (e) {
        const msg = e instanceof Error ? e.message : '发送失败'
        setSendError(msg)
        // 保留用户消息，避免“点了没反应”；错误提示在输入框上方展示
      } finally {
        setSending(false)
      }
    },
    [messages, sending, username, serverMode, conversationId, ensureConversationId, agentSessionId],
  )

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    void sendText(input)
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void sendText(input)
    }
  }

  const orderFormReadonlyKeys = new Set(
    (pendingOrderFormAction?.readonly_fields ?? []).map((x) => x.key),
  )

  function onPickPrompt(p: SuggestedPrompt) {
    setInput(p.text)
    textareaRef.current?.focus()
  }

  async function clearChat() {
    if (sending) return
    setSendError(null)
    const api = getApiBase()
    setMessages([])
    if (serverMode === 'on' && api) {
      try {
        await deleteAllConversations(api, username)
        const c = await createConversation(api, username)
        setConversationId(c.id)
      } catch {
        setConversationId(null)
      }
    } else {
      setConversationId(null)
    }
    setAgentSessionId(null)
    setPendingOrderFormAction(null)
    setDetectedOrderItems([])
    setDetectedCancelOrderIds([])
    setOrderFormValues({})
    setOrderConfirmChoice({})
    setOrderConfirmSubmitting(null)
    orderConfirmInFlightRef.current.clear()
  }

  const handleOrderConfirm = useCallback(
    async (messageId: string, confirm: boolean) => {
      if (orderConfirmInFlightRef.current.has(messageId)) return
      const api = getApiBase()
      if (!api || !agentSessionId) {
        setSendError('当前会话未就绪，无法确认订单。')
        return
      }
      orderConfirmInFlightRef.current.add(messageId)
      setSendError(null)
      setOrderConfirmSubmitting({ messageId, side: confirm ? 'confirm' : 'cancel' })
      try {
        const result = await submitOrderConfirm(api, {
          sessionId: agentSessionId,
          userId: username,
          confirm,
        })
        setOrderConfirmChoice((prev) => {
          if (prev[messageId]) return prev
          return { ...prev, [messageId]: confirm ? 'confirm' : 'cancel' }
        })
        const assistantMsg: ChatMessage = {
          id: newId(),
          role: 'assistant',
          content: result.message,
          createdAt: Date.now(),
        }
        setMessages((h) => [...h, assistantMsg])
        const orderFillAction = pickOrderFillAction(result.pending_actions)
        if (orderFillAction) {
          setPendingOrderFormAction(orderFillAction)
          setDetectedOrderItems(pickDetectedItems(orderFillAction))
          setDetectedCancelOrderIds(pickDetectedCancelOrderIds(orderFillAction))
          const mergedPrefill: Record<string, string> = {}
          const formFields =
            orderFillAction.display_fields && orderFillAction.display_fields.length > 0
              ? orderFillAction.display_fields
              : orderFillAction.required_fields
          for (const f of formFields) {
            const value = orderFillAction.prefill?.[f.key]
            mergedPrefill[f.key] = typeof value === 'string' ? value : ''
          }
          setOrderFormValues(mergedPrefill)
        } else {
          setPendingOrderFormAction(null)
          setDetectedOrderItems([])
          setDetectedCancelOrderIds([])
          setOrderFormValues({})
        }
        if (serverMode === 'on' && conversationId) {
          await appendConversationMessage(api, conversationId, username, 'assistant', result.message)
        }
      } catch (e) {
        setSendError(e instanceof Error ? e.message : '订单确认失败')
      } finally {
        orderConfirmInFlightRef.current.delete(messageId)
        setOrderConfirmSubmitting(null)
      }
    },
    [
      agentSessionId,
      username,
      serverMode,
      conversationId,
    ],
  )

  function onChangeOrderField(key: string, value: string) {
    setOrderFormValues((prev) => ({ ...prev, [key]: value }))
  }

  function onChangeDetectedItemQuantity(index: number, quantity: string) {
    setDetectedOrderItems((prev) =>
      prev.map((it, i) => (i === index ? { ...it, quantity } : it)),
    )
  }

  async function onSubmitOrderForm() {
    if (!pendingOrderFormAction || sending) return
    const api = getApiBase()
    if (!api || !agentSessionId) {
      setSendError('当前会话未就绪，无法提交订单表单。')
      return
    }
    setSendError(null)
    setSending(true)
    try {
      const fields: Record<string, string> = {}
      const formFields =
        pendingOrderFormAction.display_fields && pendingOrderFormAction.display_fields.length > 0
          ? pendingOrderFormAction.display_fields
          : pendingOrderFormAction.required_fields
      for (const f of formFields) {
        fields[f.key] = (orderFormValues[f.key] ?? '').trim()
      }
      const normalizedItems = detectedOrderItems.map((it) => ({
        item_name: it.item_name,
        quantity: (it.quantity ?? '').trim() || '1',
      }))
      const result = await submitOrderFields(api, {
        sessionId: agentSessionId,
        userId: username,
        fields,
        items: normalizedItems,
      })
      const assistantMsg: ChatMessage = {
        id: newId(),
        role: 'assistant',
        content: result.message,
        createdAt: Date.now(),
      }
      setMessages((h) => [...h, assistantMsg])
      void result.action_required
      void result.order_link
      const orderFillAction = pickOrderFillAction(result.pending_actions)
      if (orderFillAction) {
        setPendingOrderFormAction(orderFillAction)
        setDetectedOrderItems(pickDetectedItems(orderFillAction))
        setDetectedCancelOrderIds(pickDetectedCancelOrderIds(orderFillAction))
        const mergedPrefill: Record<string, string> = {}
        const formFields =
          orderFillAction.display_fields && orderFillAction.display_fields.length > 0
            ? orderFillAction.display_fields
            : orderFillAction.required_fields
        for (const f of formFields) {
          const value = orderFillAction.prefill?.[f.key]
          mergedPrefill[f.key] = typeof value === 'string' ? value : ''
        }
        setOrderFormValues(mergedPrefill)
      } else {
        setPendingOrderFormAction(null)
        setDetectedOrderItems([])
        setDetectedCancelOrderIds([])
        setOrderFormValues({})
      }
      if (serverMode === 'on' && conversationId) {
        await appendConversationMessage(api, conversationId, username, 'assistant', result.message)
      }
    } catch (e) {
      setSendError(e instanceof Error ? e.message : '提交订单信息失败')
    } finally {
      setSending(false)
    }
  }

  async function onCancelOrderFlow() {
    const api = getApiBase()
    if (!api || !agentSessionId || sending) return
    setSendError(null)
    setSending(true)
    try {
      const result = await cancelOrderFlow(api, {
        sessionId: agentSessionId,
        userId: username,
      })
      const assistantMsg: ChatMessage = {
        id: newId(),
        role: 'assistant',
        content: result.message,
        createdAt: Date.now(),
      }
      setMessages((h) => [...h, assistantMsg])
      setPendingOrderFormAction(null)
      setDetectedOrderItems([])
      setDetectedCancelOrderIds([])
      setOrderFormValues({})
      if (serverMode === 'on' && conversationId) {
        await appendConversationMessage(api, conversationId, username, 'assistant', result.message)
      }
    } catch (e) {
      setSendError(e instanceof Error ? e.message : '取消订单流程失败')
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="chat-layout">
      <header className="chat-header">
        <div className="chat-header-inner">
          <div className="chat-brand">
            <span className="chat-logo" aria-hidden="true">
              ◆
            </span>
            <div>
              <h1 className="chat-title">玩家智能客服</h1>
              <p className="chat-subtitle">抢购 · 发帖动态 · 好友社交 · 账号与充值</p>
            </div>
          </div>
          <div className="chat-header-actions">
            <span className="chat-user" title={username}>
              {username}
            </span>
            {serverMode === 'on' ? (
              <span className="chat-user" title="会话已同步到服务端">
                已同步
              </span>
            ) : null}
            {serverMode === 'error' ? (
              <span className="chat-user" title="服务端会话不可用，已回退本地">
                本地会话
              </span>
            ) : null}
            <button type="button" className="btn btn-ghost" onClick={() => void clearChat()}>
              清空会话
            </button>
            <button type="button" className="btn btn-outline" onClick={logout}>
              退出登录
            </button>
          </div>
        </div>
      </header>

      <main className="chat-main">
        <div className="chat-panel">
          {serverMode === 'loading' ? (
            <p className="empty-desc" aria-live="polite">
              正在加载会话…
            </p>
          ) : showPrompts ? (
            <div className="empty-state">
              <h2 className="empty-title">您好，我是游戏社区智能客服</h2>
              <p className="empty-desc">
                可咨询限时抢购、订单、发帖审核、加好友/圈子、充值到账与举报等问题。点击下方快捷问题可一键填入，也支持直接输入；Shift+Enter
                换行。
              </p>
              <SuggestedPrompts onPick={onPickPrompt} disabled={sending} />
            </div>
          ) : (
            <MessageList
              messages={messages}
              orderConfirmChoice={orderConfirmChoice}
              orderConfirmSubmitting={orderConfirmSubmitting}
              onOrderConfirm={handleOrderConfirm}
            />
          )}
          <div ref={bottomRef} />
        </div>
      </main>

      <footer className="chat-composer-wrap">
        <form className="chat-composer" onSubmit={onSubmit}>
          {sendError ? (
            <p className="composer-error" role="alert">
              {sendError}
            </p>
          ) : null}
          <div className="composer-row">
            {pendingOrderFormAction ? (
              <div className="order-fill-form">
                <div className="order-fill-header">
                  <strong>请补全订单信息</strong>
                  <span>{pendingOrderFormAction.hint || '请填写以下必填项后继续。'}</span>
                </div>
                {detectedOrderItems.length > 0 ? (
                  <div className="detected-items">
                    <div className="detected-items-title">已从依赖任务识别到商品清单（可修改数量）</div>
                    {detectedOrderItems.map((it, idx) => (
                      <div key={`${it.item_name}_${idx}`} className="detected-item-row">
                        <span>{it.item_name}</span>
                        <label className="detected-item-qty">
                          <span>x</span>
                          <input
                            className="field-input"
                            value={it.quantity}
                            onChange={(ev) => onChangeDetectedItemQuantity(idx, ev.target.value)}
                            disabled={sending}
                          />
                        </label>
                      </div>
                    ))}
                  </div>
                ) : null}
                {detectedCancelOrderIds.length > 0 ? (
                  <div className="detected-items">
                    <div className="detected-items-title">已从依赖任务识别到订单号</div>
                    {detectedCancelOrderIds.map((oid) => (
                      <div key={oid} className="detected-item-row">
                        <span>{oid}</span>
                      </div>
                    ))}
                  </div>
                ) : null}
                <div className="order-fill-grid">
                  {(pendingOrderFormAction.display_fields && pendingOrderFormAction.display_fields.length > 0
                    ? pendingOrderFormAction.display_fields
                    : pendingOrderFormAction.required_fields
                  )
                    .filter((f) => {
                      // 有多条已识别商品时，商品名称/数量由上方清单承载，避免下方单条输入造成歧义。
                      if (detectedOrderItems.length > 0 && (f.key === 'item_name' || f.key === 'quantity')) {
                        return false
                      }
                      return true
                    })
                    .map((f) => (
                    <label key={f.key} className="order-fill-field">
                      <span>{f.label || f.key}</span>
                      <input
                        className="field-input"
                        value={orderFormValues[f.key] ?? ''}
                        onChange={(ev) => onChangeOrderField(f.key, ev.target.value)}
                        placeholder={`请输入${f.label || f.key}`}
                        disabled={
                          sending ||
                          orderFormReadonlyKeys.has(f.key) ||
                          (f.key === 'order_id' &&
                            (pendingOrderFormAction.operation === 'cancel' ||
                              pendingOrderFormAction.operation === 'modify'))
                        }
                      />
                    </label>
                  ))}
                </div>
                <div className="order-fill-actions">
                  <button type="button" className="btn btn-outline" disabled={sending} onClick={() => void onCancelOrderFlow()}>
                    取消订单流程
                  </button>
                  <button type="button" className="btn btn-primary" disabled={sending} onClick={() => void onSubmitOrderForm()}>
                    {sending ? '提交中…' : '提交订单信息'}
                  </button>
                </div>
              </div>
            ) : null}
            <textarea
              ref={textareaRef}
              className="composer-input"
              rows={2}
              placeholder="描述您遇到的问题，例如抢购失败、动态被删、好友添加不了…"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeyDown}
              disabled={sending || serverMode === 'loading' || !!pendingOrderFormAction}
              aria-label="向智能客服输入问题"
            />
            <button
              type="submit"
              className="btn btn-primary composer-send"
              disabled={sending || !input.trim() || serverMode === 'loading' || !!pendingOrderFormAction}
            >
              {sending ? '发送中…' : '发送'}
            </button>
          </div>
        </form>
      </footer>
    </div>
  )
}
