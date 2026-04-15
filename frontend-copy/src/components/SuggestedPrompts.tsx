import {
  SUGGESTED_PROMPTS,
  type SuggestedPrompt,
} from '../constants/suggestedPrompts'

type Props = {
  onPick: (prompt: SuggestedPrompt) => void
  disabled?: boolean
}

export function SuggestedPrompts({ onPick, disabled }: Props) {
  return (
    <section className="suggested" aria-label="玩家常见问题快捷入口">
      <p className="suggested-title">玩家常问</p>
      <div className="suggested-grid" role="list">
        {SUGGESTED_PROMPTS.map((p) => (
          <button
            key={p.id}
            type="button"
            className="suggested-chip"
            role="listitem"
            disabled={disabled}
            onClick={() => onPick(p)}
          >
            {p.label}
          </button>
        ))}
      </div>
    </section>
  )
}
