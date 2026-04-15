export type ChatRole = 'user' | 'assistant'

export type Citation = {
  source: string
  chunk_id: number
  distance?: number
  snippet?: string | null
}

export type ChatMessage = {
  id: string
  role: ChatRole
  content: string
  citations?: Citation[]
  createdAt: number
}
