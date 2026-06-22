import type {
  ConfirmResponse,
  DoneEvent,
  FinalEvent,
  MessageResponse,
  StartResponse,
} from './types'

// EMPTY base in dev → relative /chat goes through the Vite proxy. An absolute
// VITE_AGENT_URL targets the agent directly (production).
const BASE = import.meta.env.VITE_AGENT_URL ?? ''

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const data = await res.json()
      detail = (data?.detail as string) ?? detail
    } catch {
      // non-JSON error body
    }
    throw new Error(detail)
  }
  return (await res.json()) as T
}

export function startChat(phone: string): Promise<StartResponse> {
  return postJson<StartResponse>('/chat/start', { phone })
}

export function sendMessage(cid: string, text: string): Promise<MessageResponse> {
  return postJson<MessageResponse>(`/chat/${cid}/message`, { text })
}

export function confirmAction(cid: string, accept: boolean): Promise<ConfirmResponse> {
  return postJson<ConfirmResponse>(`/chat/${cid}/confirm`, { accept })
}

export type StreamHandlers = {
  status: (message: string) => void
  final: (data: FinalEvent) => void
  done: (data: DoneEvent) => void
  error: (detail: string) => void
}

function parseFrame(frame: string, on: StreamHandlers): void {
  let event = 'message'
  let data = ''
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) data += line.slice(5).trim()
  }
  if (!data) return
  let payload: unknown
  try {
    payload = JSON.parse(data)
  } catch {
    return
  }
  if (event === 'status') on.status((payload as { message: string }).message)
  else if (event === 'final') on.final(payload as FinalEvent)
  else if (event === 'done') on.done(payload as DoneEvent)
  else if (event === 'error') on.error((payload as { detail: string }).detail)
}

// SSE over POST: EventSource is GET-only, so we read the stream ourselves.
// Throws on a non-OK response so the caller can fall back to sync sendMessage.
export async function streamMessage(
  cid: string,
  text: string,
  on: StreamHandlers,
): Promise<void> {
  const res = await fetch(`${BASE}/chat/${cid}/message_stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  })
  if (!res.ok || !res.body) throw new Error(`stream failed (${res.status})`)

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let idx = buffer.indexOf('\n\n')
    while (idx !== -1) {
      parseFrame(buffer.slice(0, idx), on)
      buffer = buffer.slice(idx + 2)
      idx = buffer.indexOf('\n\n')
    }
  }
}

export function isValidVnPhone(raw: string): boolean {
  const s = raw.replace(/[\s.-]/g, '')
  return /^(?:\+?84|0)\d{9}$/.test(s)
}
