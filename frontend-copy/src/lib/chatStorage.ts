import type { ChatMessage } from '../types/chat'

function keyForUser(username: string): string {
  return `assistant_chat_${username}`
}

export function loadMessages(username: string): ChatMessage[] {
  try {
    const raw = localStorage.getItem(keyForUser(username))
    if (!raw) return []
    const parsed = JSON.parse(raw) as ChatMessage[]
    if (!Array.isArray(parsed)) return []
    return parsed.filter(
      (m) =>
        m &&
        typeof m.id === 'string' &&
        (m.role === 'user' || m.role === 'assistant') &&
        typeof m.content === 'string',
    )
  } catch {
    return []
  }
}

export function saveMessages(username: string, messages: ChatMessage[]): void {
  localStorage.setItem(keyForUser(username), JSON.stringify(messages))
}
