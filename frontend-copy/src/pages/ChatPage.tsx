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
  createConversation,
  deleteAllConversations,
  finalizeOrderFlow,
  fetchHealth,
  getApiBase,
  getConversationMessages,
  listConversations,
  requestAssistantReply,
  storedMessageToChatMessage,
} from '../lib/chatApi'
import type { ChatMessage } from '../types/chat'
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
  const [pendingOrderLink, setPendingOrderLink] = useState<string | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

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
        const { reply, citations, sessionId, actionRequired, orderLink } = await requestAssistantReply(history, {
          userUsername: username,
          sessionId: agentSessionId ?? undefined,
        })
        if (sessionId) setAgentSessionId(sessionId)
        if (actionRequired === 'click_order_link_confirm' && orderLink) {
          setPendingOrderLink(orderLink)
        } else {
          setPendingOrderLink(null)
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
    setPendingOrderLink(null)
  }

  async function onFinishOrderFlow() {
    const api = getApiBase()
    if (!api || !agentSessionId) return
    try {
      const result = await finalizeOrderFlow(api, {
        sessionId: agentSessionId,
        userId: username,
        clickConfirmed: true,
      })
      const assistantMsg: ChatMessage = {
        id: newId(),
        role: 'assistant',
        content: result.message,
        createdAt: Date.now(),
      }
      setMessages((h) => [...h, assistantMsg])
      setPendingOrderLink(null)
    } catch (e) {
      setSendError(e instanceof Error ? e.message : '订单确认失败')
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
            <MessageList messages={messages} />
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
            {pendingOrderLink ? (
              <div className="composer-order-actions">
                <a href={pendingOrderLink} target="_blank" rel="noreferrer">
                  打开订单结果链接
                </a>
                <button
                  type="button"
                  className="btn btn-outline"
                  onClick={() => void onFinishOrderFlow()}
                >
                  我已点击并确认，结束流程
                </button>
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
              disabled={sending || serverMode === 'loading'}
              aria-label="向智能客服输入问题"
            />
            <button
              type="submit"
              className="btn btn-primary composer-send"
              disabled={sending || !input.trim() || serverMode === 'loading'}
            >
              {sending ? '发送中…' : '发送'}
            </button>
          </div>
        </form>
      </footer>
    </div>
  )
}
