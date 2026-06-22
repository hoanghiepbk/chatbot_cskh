// Shapes consumed from the agent chat API (TIP-005/006/007/008). The widget
// only READS these — it never changes agent logic.

export type Citation = { doc_id: string; heading: string }

// public_pending() projection: confirm stage carries a summary; other stages
// carry options. The widget renders a confirm card only for the confirm stage.
export type PendingAction =
  | { type: string; stage: 'confirm'; summary?: string }
  | { type: string; stage: string; options?: { n: number; label: string }[] }

export type StartResponse = { conversation_id: string; greeting: string }

export type MessageResponse = {
  reply: string | null
  citations?: Citation[]
  intent?: string | null
  escalated?: boolean
  pending_action?: PendingAction | null
  mode?: 'human'
}

export type ConfirmResponse = {
  reply: string
  executed: boolean
  escalated: boolean
  pending_action: PendingAction | null
}

// SSE events from /message_stream.
export type FinalEvent = { reply: string | null; mode?: 'human' }
export type DoneEvent = {
  intent?: string | null
  citations?: Citation[]
  escalated?: boolean
  pending_action?: PendingAction | null
  mode?: 'human'
}

// UI message model.
export type Role = 'customer' | 'agent' | 'staff'
export type ChatMsg = {
  id: string
  role: Role
  text: string
  citations?: Citation[]
  pending?: PendingAction | null
  emergency?: boolean
}

// Masked message row from the messages_public view (human-mode staff messages).
export type PublicMessage = {
  id: string
  conversation_id: string
  sender: Role
  content_masked: string | null
  created_at: string
}
