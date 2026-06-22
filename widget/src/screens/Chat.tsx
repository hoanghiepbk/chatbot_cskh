import { useEffect, useRef, useState } from 'react'
import { confirmAction, sendMessage, streamMessage } from '../api'
import { ConfirmCard } from '../components/ConfirmCard'
import { EmergencyBanner } from '../components/EmergencyBanner'
import { HumanBanner } from '../components/HumanBanner'
import { MessageBubble } from '../components/MessageBubble'
import { StatusIndicator } from '../components/StatusIndicator'
import { useConversationMode, useStaffMessages } from '../realtime'
import type { ChatMsg, DoneEvent, PendingAction } from '../types'

let seq = 0
function nextId(): string {
  seq += 1
  return `m${seq}`
}

// Screen 2 — the conversation. SSE streaming with a sync fallback; renders each
// reply by type (citations / confirm card / emergency banner / human mode).
export function Chat({
  conversationId,
  greeting,
}: {
  conversationId: string
  greeting: string
}) {
  const [messages, setMessages] = useState<ChatMsg[]>([
    { id: nextId(), role: 'agent', text: greeting },
  ])
  const [draft, setDraft] = useState('')
  const [status, setStatus] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [pending, setPending] = useState<PendingAction | null>(null)
  const [respHuman, setRespHuman] = useState(false)

  const listRef = useRef<HTMLDivElement>(null)
  const seenStaff = useRef<Set<string>>(new Set())
  const prevHuman = useRef(false)

  // conversation mode drives human-mode UI even when the customer isn't sending
  // (staff claim/resolve push via Realtime; poll fallback inside the hook).
  const serverMode = useConversationMode(conversationId, true)
  const humanMode = serverMode === 'human' || (serverMode === null && respHuman)
  const staffMsgs = useStaffMessages(conversationId, humanMode)

  // Append newly-arrived staff messages (dedup by DB id).
  useEffect(() => {
    if (!staffMsgs.length) return
    setMessages((prev) => {
      const additions: ChatMsg[] = []
      for (const m of staffMsgs) {
        if (seenStaff.current.has(m.id)) continue
        seenStaff.current.add(m.id)
        additions.push({ id: `s-${m.id}`, role: 'staff', text: m.content_masked ?? '' })
      }
      return additions.length ? [...prev, ...additions] : prev
    })
  }, [staffMsgs])

  // When the staff member resolves (human → agent), note the handback.
  useEffect(() => {
    if (prevHuman.current && !humanMode) {
      setMessages((prev) => [
        ...prev,
        {
          id: nextId(),
          role: 'agent',
          text: 'Nhân viên đã hoàn tất hỗ trợ — trợ lý tự động tiếp tục phục vụ anh/chị nhé.',
        },
      ])
      setRespHuman(false)
    }
    prevHuman.current = humanMode
  }, [humanMode])

  // Auto-scroll to the latest message / status.
  useEffect(() => {
    const el = listRef.current
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
  }, [messages, status])

  function applyAgentReply(text: string, meta?: DoneEvent) {
    const pa = meta?.pending_action ?? null
    setMessages((prev) => [
      ...prev,
      {
        id: nextId(),
        role: 'agent',
        text,
        citations: meta?.citations ?? [],
        emergency: meta?.intent === 'emergency',
      },
    ])
    setPending(pa && pa.stage === 'confirm' ? pa : null)
  }

  async function handleSend() {
    const text = draft.trim()
    if (!text || busy) return
    setDraft('')
    setPending(null)
    setMessages((prev) => [...prev, { id: nextId(), role: 'customer', text }])
    setBusy(true)
    setStatus('Đang xử lý yêu cầu của anh/chị…')

    let gotFinal = false
    let gotError = false
    let finalReply: string | null = null
    let finalHuman = false
    let doneMeta: DoneEvent | undefined

    try {
      await streamMessage(conversationId, text, {
        status: (m) => setStatus(m),
        final: (d) => {
          gotFinal = true
          finalReply = d.reply
          if (d.mode === 'human') finalHuman = true
        },
        done: (d) => {
          doneMeta = d
          if (d.mode === 'human') finalHuman = true
        },
        error: () => {
          gotError = true
        },
      })
    } catch {
      // stream never started (network / non-200) → sync fallback below
    }

    setStatus(null)

    if (gotFinal) {
      if (finalHuman || finalReply === null) setRespHuman(true)
      else applyAgentReply(finalReply, doneMeta)
      setBusy(false)
      return
    }
    if (gotError) {
      setMessages((prev) => [
        ...prev,
        { id: nextId(), role: 'agent', text: 'Xin lỗi, có lỗi xử lý. Anh/chị nhắn lại giúp mình nhé.' },
      ])
      setBusy(false)
      return
    }

    // Sync fallback (/message).
    try {
      const res = await sendMessage(conversationId, text)
      if (res.mode === 'human' || res.reply === null) {
        setRespHuman(true)
      } else {
        applyAgentReply(res.reply, {
          intent: res.intent,
          citations: res.citations,
          escalated: res.escalated,
          pending_action: res.pending_action,
        })
      }
    } catch (e) {
      setMessages((prev) => [
        ...prev,
        {
          id: nextId(),
          role: 'agent',
          text: `Xin lỗi, có lỗi kết nối (${e instanceof Error ? e.message : 'thử lại sau'}).`,
        },
      ])
    } finally {
      setBusy(false)
    }
  }

  async function handleConfirm(accept: boolean) {
    setBusy(true)
    setPending(null)
    try {
      const res = await confirmAction(conversationId, accept)
      applyAgentReply(res.reply, { pending_action: res.pending_action })
    } catch (e) {
      setMessages((prev) => [
        ...prev,
        {
          id: nextId(),
          role: 'agent',
          text: `Xin lỗi, không xác nhận được (${e instanceof Error ? e.message : 'thử lại'}).`,
        },
      ])
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="chat">
      <header className="chat-header">
        <div className="chat-header-brand">🔧 XeCare</div>
        <div className="chat-header-sub">Trợ lý chăm sóc khách hàng</div>
      </header>

      {humanMode && <HumanBanner />}

      <div className="messages" ref={listRef}>
        {messages.map((m) => (
          <div key={m.id}>
            {m.emergency && <EmergencyBanner />}
            <MessageBubble msg={m} />
          </div>
        ))}

        {pending && (
          <ConfirmCard
            summary={'summary' in pending ? pending.summary : undefined}
            busy={busy}
            onConfirm={() => void handleConfirm(true)}
            onCancel={() => void handleConfirm(false)}
          />
        )}

        {status && <StatusIndicator text={status} />}
      </div>

      <div className="composer">
        <textarea
          className="composer-input"
          value={draft}
          placeholder={humanMode ? 'Nhắn cho nhân viên…' : 'Nhập tin nhắn…'}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              void handleSend()
            }
          }}
          rows={1}
          aria-label="Nội dung tin nhắn"
        />
        <button
          className="btn btn-send"
          onClick={() => void handleSend()}
          disabled={busy || !draft.trim()}
          aria-label="Gửi"
        >
          ➤
        </button>
      </div>
    </div>
  )
}
