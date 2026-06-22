import { useEffect, useState } from 'react'
import { supabase } from './supabase'
import type { PublicMessage } from './types'

// Human-mode staff messages from the anon-readable messages_public view. The
// base `messages` table isn't anon-readable for Realtime push, so we poll every
// 3s while human mode is active. Returns only staff-authored masked messages —
// the customer's own messages are already shown locally.
export function useStaffMessages(cid: string | null, active: boolean): PublicMessage[] {
  const [staffMsgs, setStaffMsgs] = useState<PublicMessage[]>([])

  useEffect(() => {
    if (!active || !cid || !supabase) return
    const client = supabase
    let cancelled = false
    const load = async () => {
      const { data, error } = await client
        .from('messages_public')
        .select('id, conversation_id, sender, content_masked, created_at')
        .eq('conversation_id', cid)
        .eq('sender', 'staff')
        .order('created_at', { ascending: true })
      if (!cancelled && !error) setStaffMsgs((data as PublicMessage[]) ?? [])
    }
    void load()
    const timer = setInterval(() => void load(), 3000)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [active, cid])

  return active ? staffMsgs : []
}

// Watch conversations.mode (anon-readable, Realtime push) so the widget reacts
// to a staff claim (→ human) and resolve (→ agent) even when the customer isn't
// actively sending. Polls every 3s as the fallback.
export function useConversationMode(
  cid: string | null,
  active: boolean,
): 'agent' | 'human' | null {
  const [mode, setMode] = useState<'agent' | 'human' | null>(null)

  useEffect(() => {
    if (!active || !cid || !supabase) return
    const client = supabase
    let cancelled = false
    const load = async () => {
      const { data, error } = await client
        .from('conversations')
        .select('mode')
        .eq('id', cid)
        .limit(1)
      if (!cancelled && !error && data && data[0]) {
        setMode((data[0] as { mode: 'agent' | 'human' }).mode)
      }
    }
    void load()
    const timer = setInterval(() => void load(), 3000)
    const channel = client
      .channel(`rt-conv-${cid}`)
      .on(
        'postgres_changes',
        { event: 'UPDATE', schema: 'public', table: 'conversations', filter: `id=eq.${cid}` },
        () => void load(),
      )
      .subscribe()
    return () => {
      cancelled = true
      clearInterval(timer)
      void client.removeChannel(channel)
    }
  }, [active, cid])

  return mode
}
