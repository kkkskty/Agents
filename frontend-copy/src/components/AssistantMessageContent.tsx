import {
  parseKvLines,
  parseProductLines,
  tryParseOrderConfirmation,
} from '../lib/orderConfirmParse'

type Props = {
  content: string
  messageId: string
  /** 已点击过的按钮（仅此条气泡内单次有效） */
  confirmChoice?: 'confirm' | 'cancel'
  /** 请求进行中：两键均禁用；标注哪一侧被点击 */
  confirmSubmittingSide?: 'confirm' | 'cancel'
  onConfirmAction?: (messageId: string, confirm: boolean) => void
}

export function AssistantMessageContent({
  content,
  messageId,
  confirmChoice,
  confirmSubmittingSide,
  onConfirmAction,
}: Props) {
  const parsed = tryParseOrderConfirmation(content)

  if (!parsed) {
    return <div className="bubble-text">{content}</div>
  }

  const { intro, sections, footer } = parsed
  const submitting = Boolean(confirmSubmittingSide)
  const used = Boolean(confirmChoice) || submitting
  const canUseApi = Boolean(onConfirmAction)

  return (
    <div className="order-confirm-rich">
      {intro ? <div className="order-confirm-intro">{intro}</div> : null}

      <div className="order-confirm-stack">
        <div className="order-confirm-main-title">待确认的订单信息</div>

        {sections.map((sec, idx) => (
          <OrderConfirmSectionBlock key={`${sec.title}_${idx}`} title={sec.title} body={sec.body} />
        ))}
      </div>

      {footer ? (
        <div className="order-confirm-footer">
          <p className="order-confirm-footer-text">{footer}</p>
          {canUseApi ? (
            <div className="order-confirm-actions">
              <button
                type="button"
                className={`btn btn-primary btn-sm order-confirm-btn ${confirmChoice === 'confirm' ? 'order-confirm-btn--chosen' : ''} ${confirmSubmittingSide === 'confirm' ? 'order-confirm-btn--busy' : ''}`}
                disabled={used || !onConfirmAction}
                onClick={() => onConfirmAction?.(messageId, true)}
              >
                {confirmSubmittingSide === 'confirm' ? '…' : '确认'}
              </button>
              <button
                type="button"
                className={`btn btn-outline btn-sm order-confirm-btn ${confirmChoice === 'cancel' ? 'order-confirm-btn--chosen-cancel' : ''} ${confirmSubmittingSide === 'cancel' ? 'order-confirm-btn--busy' : ''}`}
                disabled={used || !onConfirmAction}
                onClick={() => onConfirmAction?.(messageId, false)}
              >
                {confirmSubmittingSide === 'cancel' ? '…' : '取消'}
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

function OrderConfirmSectionBlock({ title, body }: { title: string; body: string }) {
  const products = parseProductLines(body)
  if (products.length > 0) {
    return (
      <section className="order-confirm-section">
        <header className="order-confirm-section-head">{title}</header>
        <div className="order-confirm-section-body order-confirm-products">
          {products.map((line, i) => (
            <div key={i} className="order-confirm-product">
              <span className="order-confirm-dot" aria-hidden="true">
                ·
              </span>
              <span className="order-confirm-product-text">{line}</span>
            </div>
          ))}
        </div>
      </section>
    )
  }

  const kv = parseKvLines(body)
  if (kv.length > 0) {
    return (
      <section className="order-confirm-section">
        <header className="order-confirm-section-head">{title}</header>
        <div className="order-confirm-section-body">
          <dl className="order-confirm-kv">
            {kv.map((row, i) => (
              <div key={`${row.label}_${i}`} className="order-confirm-kv-row">
                <dt>{row.label}</dt>
                <dd>{row.value}</dd>
              </div>
            ))}
          </dl>
        </div>
      </section>
    )
  }

  return (
    <section className="order-confirm-section">
      <header className="order-confirm-section-head">{title}</header>
      <div className="order-confirm-section-body order-confirm-plain">{body}</div>
    </section>
  )
}
