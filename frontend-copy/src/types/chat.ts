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

export type OrderField = {
  key: string
  label: string
}

export type OrderFillFieldsAction = {
  type: 'order_fill_fields'
  task_id?: string
  operation?: 'create' | 'cancel' | 'modify' | string
  required_fields: OrderField[]
  display_fields?: OrderField[]
  /** 只读字段（与后端 order_field_config.readonly 对齐） */
  readonly_fields?: OrderField[]
  prefill?: Record<
    string,
    string | Array<{ item_name: string; quantity: string }> | Array<string>
  >
  hint?: string
}

export type PendingAction = OrderFillFieldsAction | Record<string, unknown>
