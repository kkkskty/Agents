import type { ChatMessage } from '../types/chat'

type Props = {
  messages: ChatMessage[]
}

export function MessageList({ messages }: Props) {
  return (
    <div className="message-list" role="log" aria-live="polite" aria-relevant="additions">
      {messages.map((m) => (
        <article
          key={m.id}
          className={`bubble bubble--${m.role}`}
          aria-label={m.role === 'user' ? '玩家' : '客服'}
        >
          <div className="bubble-meta">
            {m.role === 'user' ? '我' : '客服'}
          </div>
          <div className="bubble-content">{m.content}</div>
          {m.role === 'assistant' && m.citations && m.citations.length > 0 ? (
            <div className="citations" aria-label="引用来源">
              <div className="citations-title">引用来源</div>
              {m.citations.map((c, idx) => (
                <div key={`${c.source}_${c.chunk_id}_${idx}`} className="citation-card">
                  <div className="citation-meta">
                    {c.source}#{c.chunk_id}
                    {typeof c.distance === 'number' ? (
                      <span className="citation-distance">
                        （相似度距离={c.distance.toFixed(4)}）
                      </span>
                    ) : null}
                  </div>
                  {c.snippet ? <div className="citation-snippet">{c.snippet}</div> : null}
                </div>
              ))}
            </div>
          ) : null}
        </article>
      ))}
    </div>
  )
}
